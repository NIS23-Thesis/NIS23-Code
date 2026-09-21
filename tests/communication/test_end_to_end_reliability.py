import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.communication.parser import parse_datagram


def wait_until(predicate, timeout=2.0, interval=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class MemoryNetwork:
    def __init__(self):
        self.sockets = {}
        self.interceptor = None

    def socket(self, address):
        sock = MemorySocket(self, address)
        self.sockets[address] = sock
        return sock

    def enqueue(self, source, destination, payload):
        self.sockets[destination].enqueue(payload, source)

    def send(self, source, destination, payload):
        if self.interceptor and self.interceptor(source, destination, payload):
            return
        self.enqueue(source, destination, payload)


class MemorySocket:
    def __init__(self, network, address):
        self.network = network
        self.address = address
        self.incoming = deque()
        self.lock = threading.Lock()

    def enqueue(self, payload, source):
        with self.lock:
            self.incoming.append((payload, source))

    def sendto(self, payload, destination):
        self.network.send(self.address, destination, payload)
        return len(payload)

    def recvfrom(self):
        with self.lock:
            if not self.incoming:
                return None
            return self.incoming.popleft()

    def close(self):
        pass


class EndToEndReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.network = MemoryNetwork()
        self.sensor_socket = self.network.socket(("sensor", 10001))
        self.cluster_socket = self.network.socket(("cluster", 10002))
        options = {
            "auto_start": True,
            "poll_interval": 0.001,
            "ack_timeout": 0.08,
            "ack_delay": 0.015,
            "reassembly_timeout": 0.5,
            "completed_retention": 0.5,
            "max_retries": 2,
        }
        self.sensor = CommunicationModule(self.sensor_socket, role=0, **options)
        self.cluster = CommunicationModule(self.cluster_socket, role=1, **options)

    def tearDown(self):
        self.sensor.stop()
        self.cluster.stop()

    def send(self, data_id, payload):
        self.sensor.send_message(
            {
                "packet_type": PacketType.SENSOR_DATA,
                "role": 0,
                "data_id": data_id,
                "sensor_type": 1,
                "payload": payload,
            },
            destination=self.cluster_socket.address,
        )

    def delivered(self, count=1):
        return wait_until(lambda: len(self.cluster.sensor_buffer) >= count)

    def transfer_event(self, event_type, data_id):
        return any(
            event["type"] == event_type and event.get("message_id") == data_id
            for event in self.sensor.event_buffer.items
        )

    def test_1_normal_transmission(self):
        payload = b"N" * 2500
        self.send(1, payload)
        self.assertTrue(self.delivered())
        self.assertEqual(self.cluster.pop_sensor_messages()[0]["payload"], payload)
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 1)))

    def test_2_fragment_loss_selectively_retransmits_only_missing_block(self):
        dropped = False

        def drop_block_one_once(source, destination, payload):
            nonlocal dropped
            packet = parse_datagram(payload).payload
            if (
                packet["packet_type"] == PacketType.SENSOR_DATA
                and packet["block_number"] == 1
                and not dropped
            ):
                dropped = True
                return True
            return False

        self.network.interceptor = drop_block_one_once
        payload = b"L" * 3500
        self.send(2, payload)
        self.assertTrue(self.delivered())
        self.assertEqual(self.cluster.pop_sensor_messages()[0]["payload"], payload)

        selective = [
            event for event in self.sensor.event_buffer.items
            if event["type"] == "fragment_sent"
            and event.get("reason") == "selective_retransmission"
        ]
        self.assertEqual([event["block_number"] for event in selective], [1])
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 2)))

    def test_3_ack_loss_causes_timeout_and_no_duplicate_delivery(self):
        dropped = False

        def drop_first_ack(source, destination, payload):
            nonlocal dropped
            packet = parse_datagram(payload).payload
            if packet["packet_type"] == PacketType.SENSOR_DATA_ACK and not dropped:
                dropped = True
                return True
            return False

        self.network.interceptor = drop_first_ack
        payload = b"A" * 1500
        self.send(3, payload)
        self.assertTrue(self.delivered())
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 3)))
        timeout_blocks = [
            event["block_number"] for event in self.sensor.event_buffer.items
            if event["type"] == "fragment_sent"
            and event.get("reason") == "timeout_retransmission"
        ]
        self.assertEqual(timeout_blocks, [0, 1])
        time.sleep(0.05)
        self.assertEqual(len(self.cluster.sensor_buffer), 1)

    def test_4_duplicate_fragment_is_not_delivered_twice(self):
        duplicated = False

        def duplicate_block_zero(source, destination, payload):
            nonlocal duplicated
            packet = parse_datagram(payload).payload
            if (
                packet["packet_type"] == PacketType.SENSOR_DATA
                and packet["block_number"] == 0
                and not duplicated
            ):
                duplicated = True
                self.network.enqueue(source, destination, payload)
                self.network.enqueue(source, destination, payload)
                return True
            return False

        self.network.interceptor = duplicate_block_zero
        self.send(4, b"D" * 1500)
        self.assertTrue(self.delivered())
        time.sleep(0.05)
        self.assertEqual(len(self.cluster.sensor_buffer), 1)
        self.assertTrue(any(
            event["type"] == "fragment_duplicate"
            for event in self.cluster.event_buffer.items
        ))

    def test_5_out_of_order_fragments_reassemble_once(self):
        held = None

        def reverse_first_window(source, destination, payload):
            nonlocal held
            packet = parse_datagram(payload).payload
            if packet["packet_type"] != PacketType.SENSOR_DATA:
                return False
            if packet["block_number"] == 0 and held is None:
                held = (source, destination, payload)
                return True
            if packet["block_number"] == 1 and held is not None:
                self.network.enqueue(source, destination, payload)
                old_source, old_destination, old_payload = held
                self.network.enqueue(old_source, old_destination, old_payload)
                held = None
                return True
            return False

        self.network.interceptor = reverse_first_window
        payload = b"O" * 2500
        self.send(5, payload)
        self.assertTrue(self.delivered())
        self.assertEqual(self.cluster.pop_sensor_messages()[0]["payload"], payload)

    def test_6_retry_exhaustion_fails_cleanly(self):
        def drop_all_data(source, destination, payload):
            return parse_datagram(payload).packet_type == PacketType.SENSOR_DATA

        self.network.interceptor = drop_all_data
        self.send(6, b"F" * 1500)
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_failed", 6)))
        self.assertEqual(self.sensor.reliability_manager.outbound_transfers, {})
        self.assertEqual(len(self.cluster.sensor_buffer), 0)

    def test_7_multiple_transfers_do_not_interfere(self):
        first = b"1" * 1800
        second = b"2" * 2300
        self.send(7, first)
        self.send(8, second)
        self.assertTrue(self.delivered(count=2))
        delivered = {message["data_id"]: message["payload"] for message in self.cluster.pop_sensor_messages()}
        self.assertEqual(delivered, {7: first, 8: second})
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 7)))
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 8)))

    def test_8_identifier_rollover_uses_transfer_sequence(self):
        self.send(255, b"old")
        self.assertTrue(self.delivered())
        first = self.cluster.pop_sensor_messages()[0]
        self.assertTrue(wait_until(lambda: self.transfer_event("transfer_complete", 255)))

        self.send(255, b"new")
        self.assertTrue(self.delivered())
        second = self.cluster.pop_sensor_messages()[0]
        self.assertEqual((first["payload"], second["payload"]), (b"old", b"new"))
        self.assertNotEqual(first["sequence_number"], second["sequence_number"])


class ModelUpdateEndToEndTests(unittest.TestCase):
    def test_cluster_heads_exchange_fragmented_model_update(self):
        network = MemoryNetwork()
        first_socket = network.socket(("cluster-a", 11001))
        second_socket = network.socket(("cluster-b", 11002))
        options = {
            "role": 1,
            "poll_interval": 0.001,
            "ack_timeout": 0.08,
            "ack_delay": 0.015,
        }
        first = CommunicationModule(first_socket, **options)
        second = CommunicationModule(second_socket, **options)
        payload = b"weights" + (b"w" * 2600)
        try:
            first.send_message(
                {
                    "packet_type": PacketType.MODEL_UPDATE,
                    "role": 1,
                    "update_id": 9,
                    "model_id": 3,
                    "payload": payload,
                },
                destination=second_socket.address,
            )
            self.assertTrue(wait_until(lambda: len(second.fl_buffer) == 1))
            message = second.pop_fl_messages()[0]
            self.assertEqual(message["payload"], payload)
            self.assertTrue(wait_until(lambda: any(
                event["type"] == "transfer_complete"
                and event.get("message_id") == 9
                for event in first.event_buffer.items
            )))
        finally:
            first.stop()
            second.stop()


if __name__ == "__main__":
    unittest.main()
