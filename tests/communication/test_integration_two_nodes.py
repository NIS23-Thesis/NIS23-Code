import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.communication.socket_interface import SocketInterface


def wait_until(predicate, timeout=3.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TwoNodeUdpIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.cluster_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
        self.sensor_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
        self.cluster = CommunicationModule(
            self.cluster_socket,
            role=1,
            auto_start=True,
            poll_interval=0.002,
            ack_timeout=0.15,
            ack_delay=0.02,
        )
        self.sensor = CommunicationModule(
            self.sensor_socket,
            role=0,
            auto_start=True,
            poll_interval=0.002,
            ack_timeout=0.15,
            ack_delay=0.02,
        )

    def tearDown(self):
        self.cluster.stop()
        self.sensor.stop()
        self.cluster_socket.close()
        self.sensor_socket.close()

    def test_hello_preserves_source_address(self):
        self.cluster.send_message(
            {
                "packet_type": PacketType.HELLO,
                "role": 1,
                "model_id": 5,
            },
            destination=self.sensor_socket.address,
        )
        self.assertTrue(wait_until(lambda: len(self.sensor.neighbor_buffer) == 1))
        hello = self.sensor.pop_neighbor_messages()[0]
        self.assertEqual(hello["model_id"], 5)
        self.assertEqual(hello["source_address"], self.cluster_socket.address)

    def test_real_end_to_end_fragmentation_ack_and_reassembly(self):
        payload = b"sensor-reading:" + (b"x" * 3000)
        self.sensor.send_message(
            {
                "packet_type": PacketType.SENSOR_DATA,
                "role": 0,
                "data_id": 42,
                "sensor_type": 1,
                "payload": payload,
            },
            destination=self.cluster_socket.address,
        )

        self.assertTrue(wait_until(lambda: len(self.cluster.sensor_buffer) == 1))
        delivered = self.cluster.pop_sensor_messages()[0]
        self.assertEqual(delivered["payload"], payload)
        self.assertEqual(delivered["source_address"], self.sensor_socket.address)

        self.assertTrue(wait_until(lambda: any(
            event["type"] == "transfer_complete"
            and event["message_id"] == 42
            for event in self.sensor.event_buffer.items
        )))
        sent = [
            event for event in self.sensor.event_buffer.items
            if event["type"] == "fragment_sent" and event["message_id"] == 42
        ]
        self.assertEqual([event["block_number"] for event in sent], [0, 1, 2])
        self.assertTrue(any(
            event["type"] == "window_advanced"
            for event in self.sensor.event_buffer.items
        ))


if __name__ == "__main__":
    unittest.main()
