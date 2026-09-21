import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.communication.parser import serialize_message


class FakeSocketInterface:
    """Records outbound sends and lets a test manually queue inbound datagrams."""

    def __init__(self):
        self.sent: List[Tuple[bytes, Tuple[str, int]]] = []
        self._incoming: List[Tuple[bytes, Tuple[str, int]]] = []

    def queue_incoming(self, raw_bytes: bytes, addr=("10.0.0.9", 12345)) -> None:
        self._incoming.append((raw_bytes, addr))

    def sendto(self, payload: bytes, address) -> int:
        self.sent.append((payload, address))
        return len(payload)

    def recvfrom(self) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        if self._incoming:
            return self._incoming.pop(0)
        return None

    def close(self) -> None:
        pass


def wait_until(predicate, timeout=1.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.socket = FakeSocketInterface()
        self.module = CommunicationModule(self.socket, auto_start=True, poll_interval=0.01)

    def tearDown(self):
        self.module.stop()

    def _hello_bytes(self):
        return serialize_message({"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 1})

    def test_hello_routes_to_neighbor_buffer(self):
        self.module.receive_datagram(self._hello_bytes())
        self.assertTrue(wait_until(lambda: len(self.module.neighbor_buffer.items) == 1))
        msg = self.module.pop_neighbor_messages()[0]
        self.assertEqual(msg["packet_type"], PacketType.HELLO)

    def test_sensor_data_routes_to_sensor_buffer(self):
        raw = serialize_message({
            "packet_type": PacketType.SENSOR_DATA, "role": 0, "sequence_number": 1,
            "data_id": 1, "sensor_type": 1, "block_number": 0, "last_block_number": 0, "payload": b"x",
        })
        self.module.receive_datagram(raw, ("10.0.0.9", 12345))
        self.assertTrue(wait_until(lambda: len(self.module.sensor_buffer.items) == 1))

    def test_model_update_routes_to_fl_buffer(self):
        raw = serialize_message({
            "packet_type": PacketType.MODEL_UPDATE, "role": 1, "sequence_number": 1,
            "update_id": 1, "model_id": 1, "block_number": 0, "last_block_number": 0, "payload": b"w",
        })
        self.module.receive_datagram(raw, ("10.0.0.9", 12345))
        self.assertTrue(wait_until(lambda: len(self.module.fl_buffer.items) == 1))

    def test_sensor_data_ack_routes_to_ack_buffer(self):
        raw = serialize_message({
            "packet_type": PacketType.SENSOR_DATA_ACK, "sequence_number": 1, "data_id": 1,
            "window_start": 0, "window_end": 0, "received_blocks": [0],
        })
        self.module.receive_datagram(raw, ("10.0.0.9", 12345))
        self.assertTrue(wait_until(lambda: len(self.module.ack_buffer.items) == 1))

    def test_malformed_datagram_lands_in_diagnostic_error_buffer(self):
        self.module.receive_datagram(b"\x00\x00")  # too short for a header
        self.assertTrue(wait_until(lambda: len(self.module.error_buffer.items) == 1))
        item = self.module.pop_errors()[0]
        self.assertEqual(item["type"], "error")
        self.assertEqual(self.module.pop_received(), [])

    def test_route_handler_callback_is_invoked(self):
        seen = []
        module = CommunicationModule(FakeSocketInterface(), auto_start=True, poll_interval=0.01,
                                      route_handler=lambda packet: seen.append(packet))
        try:
            module.receive_datagram(self._hello_bytes())
            self.assertTrue(wait_until(lambda: len(seen) == 1))
        finally:
            module.stop()


class SendMessageTests(unittest.TestCase):
    def setUp(self):
        self.socket = FakeSocketInterface()
        self.module = CommunicationModule(self.socket, auto_start=False)

    def test_send_message_with_explicit_destination(self):
        self.module.send_message({"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 1},
                                  destination=("10.0.0.5", 9000))
        self.assertEqual(len(self.socket.sent), 1)
        payload, addr = self.socket.sent[0]
        self.assertEqual(addr, ("10.0.0.5", 9000))
        self.assertTrue(len(payload) >= 4)

    def test_send_message_resolves_destination_from_dict(self):
        self.module.send_message({
            "packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 1,
            "destination": ("10.0.0.6", 9001),
        })
        self.assertEqual(self.socket.sent[0][1], ("10.0.0.6", 9001))

    def test_send_message_without_destination_raises(self):
        with self.assertRaises(ValueError):
            self.module.send_message({"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1,
                                       "model_id": 1})

    def test_configured_role_is_enforced_on_send(self):
        module = CommunicationModule(FakeSocketInterface(), role=0, auto_start=False)
        with self.assertRaises(ValueError):
            module.send_message(
                {"packet_type": PacketType.HELLO, "role": 1, "model_id": 1},
                destination=("10.0.0.5", 9000),
            )

    def test_configured_version_is_used_for_send(self):
        module = CommunicationModule(FakeSocketInterface(), version=2, auto_start=False)
        raw = module.send_message(
            {"packet_type": PacketType.HELLO, "role": 1, "model_id": 1},
            destination=("10.0.0.5", 9000),
        )
        self.assertEqual(raw[0] >> 4, 2)


class ControllerIntegrationTests(unittest.TestCase):
    """Checks that the controller owns polling and reliability integration."""

    def test_socket_incoming_data_is_picked_up_automatically(self):
        socket = FakeSocketInterface()
        socket.queue_incoming(serialize_message(
            {"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 1}
        ))
        module = CommunicationModule(socket, auto_start=True, poll_interval=0.01)
        try:
            self.assertTrue(wait_until(lambda: len(module.neighbor_buffer.items) == 1))
        finally:
            module.stop()

    def test_public_ingest_does_not_create_a_second_receive_loop(self):
        socket = FakeSocketInterface()
        module = CommunicationModule(socket, auto_start=True, poll_interval=0.01)
        try:
            self.assertFalse(module.ingest_socket_once())
        finally:
            module.stop()

    def test_local_receiver_role_is_enforced(self):
        socket = FakeSocketInterface()
        module = CommunicationModule(socket, role=0, auto_start=True, poll_interval=0.01)
        try:
            raw = serialize_message({
                "packet_type": PacketType.SENSOR_DATA, "role": 0,
                "sequence_number": 1, "data_id": 1, "sensor_type": 1,
                "block_number": 0, "last_block_number": 0, "payload": b"x",
            })
            module.receive_datagram(raw, ("10.0.0.9", 12345))
            self.assertTrue(wait_until(lambda: len(module.error_buffer.items) == 1))
            self.assertEqual(module.pop_sensor_messages(), [])
        finally:
            module.stop()

    def test_reliability_manager_is_referenced_by_communication_module(self):
        import flame.communication.communication_module as cm_module
        self.assertTrue(
            hasattr(cm_module, "ReliabilityManager"),
            "communication_module.py should import ReliabilityManager after the wiring fix.",
        )

    def test_ack_is_consumed_and_completes_outbound_transfer(self):
        socket = FakeSocketInterface()
        module = CommunicationModule(socket, auto_start=True, poll_interval=0.01)
        try:
            destination = ("10.0.0.9", 12345)
            module.send_message({
                "packet_type": PacketType.SENSOR_DATA, "role": 0,
                "sequence_number": 1, "data_id": 1, "sensor_type": 1,
                "payload": b"one block",
            }, destination=destination)
            raw = serialize_message({
                "packet_type": PacketType.SENSOR_DATA_ACK, "sequence_number": 1, "data_id": 1,
                "window_start": 0, "window_end": 0, "received_blocks": [0],
            })
            module.receive_datagram(raw, destination)
            self.assertTrue(wait_until(lambda: len(module.ack_buffer.items) == 1))
            self.assertTrue(wait_until(lambda: any(
                event["type"] == "transfer_complete" for event in module.event_buffer.items
            )))
            self.assertEqual(module.reliability_manager.outbound_transfers, {})
        finally:
            module.stop()


if __name__ == "__main__":
    unittest.main()
