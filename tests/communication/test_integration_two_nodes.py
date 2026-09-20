import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType, fragment_payload
from flame.communication.parser import serialize_message
from flame.communication.reliability import ReliabilityManager, prepare_fragments
from flame.communication.socket_interface import SocketInterface


def wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def pump(module: CommunicationModule, duration=0.3, interval=0.01):
    """Since CommunicationModule doesn't poll its own socket, something has to
    call ingest_socket_once() repeatedly. This stands in for that missing piece."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        module.ingest_socket_once()
        time.sleep(interval)


class TwoNodeUdpIntegrationTest(unittest.TestCase):
    def test_hello_and_sensor_data_over_real_sockets(self):
        cluster_head_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.05)
        sensor_node_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.05)

        cluster_head = CommunicationModule(cluster_head_socket, auto_start=True, poll_interval=0.01)
        sensor_node = CommunicationModule(sensor_node_socket, auto_start=True, poll_interval=0.01)

        stop_pumping = False
        import threading

        def pump_both():
            while not stop_pumping:
                cluster_head.ingest_socket_once()
                sensor_node.ingest_socket_once()
                time.sleep(0.005)

        pumper = threading.Thread(target=pump_both, daemon=True)
        pumper.start()

        try:
            # Cluster Head -> Sensor Node: HELLO
            cluster_head.send_message(
                {"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 5},
                destination=sensor_node_socket.address,
            )
            self.assertTrue(wait_until(lambda: len(sensor_node.neighbor_buffer.items) == 1))
            hello = sensor_node.pop_neighbor_messages()[0]
            self.assertEqual(hello["model_id"], 5)

            # Sensor Node -> Cluster Head: SENSOR_DATA
            sensor_node.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA, "role": 0, "sequence_number": 1,
                    "data_id": 7, "sensor_type": 1, "block_number": 0, "last_block_number": 0,
                    "payload": b"23.4C",
                },
                destination=cluster_head_socket.address,
            )
            self.assertTrue(wait_until(lambda: len(cluster_head.sensor_buffer.items) == 1))
            reading = cluster_head.pop_sensor_messages()[0]
            self.assertEqual(reading["payload"], b"23.4C")
        finally:
            stop_pumping = True
            pumper.join(timeout=1.0)
            cluster_head.stop()
            sensor_node.stop()
            cluster_head_socket.close()
            sensor_node_socket.close()


class ReliabilityWiringDemonstrationTest(unittest.TestCase):
    """
    reliability.py isn't called anywhere in communication_module.py today.
    This test proves the two ARE compatible if someone wires them together:
    it fragments a payload with message.fragment_payload(), sends every
    fragment as a real SENSOR_DATA datagram over a live socket, and drives
    ReliabilityManager by hand on the receiving end to detect duplicates,
    generate an ACK, and confirm completeness -- exactly the glue that
    would need to live in (or alongside) CommunicationModule for this to
    happen automatically.
    """

    def test_manual_reliability_wiring_over_real_sockets(self):
        sender_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.05)
        receiver_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.05)

        payload = b"MODEL_WEIGHTS_" + b"x" * 2000
        message_id = 42

        try:
            # --- sender side: fragment and send every block as a real datagram ---
            for block_number, last_block_number, chunk in fragment_payload(payload):
                raw = serialize_message({
                    "packet_type": PacketType.SENSOR_DATA, "role": 0, "sequence_number": block_number,
                    "data_id": message_id, "sensor_type": 1,
                    "block_number": block_number, "last_block_number": last_block_number, "payload": chunk,
                })
                sender_socket.sendto(raw, receiver_socket.address)

            # --- receiver side: pull raw datagrams straight off the socket, feed ReliabilityManager ---
            from flame.communication.parser import parse_datagram

            reliability = ReliabilityManager()
            received_fragments = {}
            expected_fragment_count = len(fragment_payload(payload))  # 2014 bytes -> 2 fragments (1024 + 990)
            deadline = time.monotonic() + 2.0
            while len(received_fragments) < expected_fragment_count and time.monotonic() < deadline:
                packet = receiver_socket.recvfrom()
                if packet is None:
                    continue
                raw, _addr = packet
                parsed = parse_datagram(raw)
                fragment = {
                    "block_number": parsed.payload["block_number"],
                    "last_block_number": parsed.payload["last_block_number"],
                    "payload": parsed.payload["payload"],
                }
                result = reliability.process_received_fragment(
                    source_address=receiver_socket.address, packet_type=PacketType.SENSOR_DATA,
                    message_id=message_id, block_number=fragment["block_number"], fragment=fragment,
                )
                if result is not None:
                    received_fragments[fragment["block_number"]] = fragment

            self.assertEqual(len(received_fragments), expected_fragment_count)

            last_block_number = next(iter(received_fragments.values()))["last_block_number"]
            ack = reliability.create_ack(
                PacketType.SENSOR_DATA, message_id,
                received_blocks=set(received_fragments.keys()), last_block_number=last_block_number,
            )
            self.assertEqual(ack["missing_blocks"], [])

            # reassemble using message.py's own reassembly to confirm the full path is consistent
            from flame.communication.message import reassemble_fragments
            reassembled = reassemble_fragments(list(received_fragments.values()))
            self.assertEqual(reassembled, payload)
        finally:
            sender_socket.close()
            receiver_socket.close()


if __name__ == "__main__":
    unittest.main()
