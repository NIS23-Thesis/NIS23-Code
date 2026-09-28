import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.communication.parser import parse_datagram, serialize_message
from flame.neighbor.neighbor_module import NeighborModule, NodeRole, AssociationState

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

    def test_hello_updates_neighbor_module(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=0,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            communication.receive_datagram(
                self._hello_bytes(),
                ("10.0.0.9", 12345),
            )

            self.assertTrue(
                wait_until(
                    lambda: neighbor_module.has_neighbor(
                        "10.0.0.9",
                        12345,
                    )
                )
            )

            neighbor = neighbor_module.get_neighbor(
                "10.0.0.9",
                12345,
            )

            self.assertIsNotNone(neighbor)
            self.assertEqual(
                neighbor.node_role,
                NodeRole.CLUSTER_HEAD,
            )
            self.assertEqual(
                neighbor.association_state,
                AssociationState.DISCOVERED,
            )
            self.assertEqual(neighbor.model_id, 1)

        finally:
            communication.stop()

    def test_join_updates_neighbor_module(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.JOIN,
                "role": 0,
                "sequence_number": 1,
                "join_id": 10,
                "model_id": 1,
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.8", 12345),
            )

            time.sleep(0.1)

            # print("Neighbor buffer:", communication.pop_neighbor_messages())
            # print("Neighbor table:", neighbor_module.neighbor_table)

        finally:
            communication.stop()

    def test_join_accept_updates_neighbor_module(self):
        neighbor_module = NeighborModule()

        neighbor_module.start_join(
            ip_address="10.0.0.8",
            port=12345,
            join_id=10,
            model_id=1,
            current_time=1000.0,
        )

        communication = CommunicationModule(
            self.socket,
            role=0,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.JOIN_ACCEPT,
                "role": 1,
                "sequence_number": 2,
                "join_id": 10,
                "timeout": 30,
                "rejected": 0,
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.8", 12345),
            )

            self.assertTrue(
                wait_until(
                    lambda: neighbor_module.get_neighbor(
                        "10.0.0.8",
                        12345,
                    ).association_state
                    == AssociationState.ASSOCIATED
                )
            )

            neighbor = neighbor_module.get_neighbor(
                "10.0.0.8",
                12345,
            )

            self.assertIsNotNone(neighbor)
            self.assertEqual(
                neighbor.node_role,
                NodeRole.CLUSTER_HEAD,
            )
            self.assertEqual(
                neighbor.association_state,
                AssociationState.ASSOCIATED,
            )
            self.assertEqual(
                neighbor.join_id,
                10,
            )

        finally:
            communication.stop()

    def test_sensor_join_flow(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=0,
            neighbor_module=neighbor_module,
            auto_start=False,
        )

        neighbor_module.start_join(
            ip_address="10.0.0.3",
            port=5000,
            join_id=15,
            model_id=2,
            current_time=1000.0,
        )

        communication.send_message(
            {
                "packet_type": PacketType.JOIN,
                "role": 0,
                "sequence_number": 1,
                "join_id": 15,
                "model_id": 2,
            },
            destination=("10.0.0.3", 5000),
        )

        neighbor = neighbor_module.get_neighbor(
            "10.0.0.3",
            5000,
        )

        self.assertIsNotNone(neighbor)
        self.assertEqual(
            neighbor.association_state,
            AssociationState.JOIN_PENDING,
        )
        self.assertEqual(
            neighbor.join_id,
            15,
        )

        self.assertEqual(
            len(self.socket.sent),
            1,
        )

        payload, address = self.socket.sent[0]

        parsed = parse_datagram(payload)

        self.assertEqual(
            address,
            ("10.0.0.3", 5000),
        )
        self.assertEqual(
            parsed.packet_type,
            PacketType.JOIN,
        )
        self.assertEqual(
            parsed.payload["join_id"],
            15,
        )
        self.assertEqual(
            parsed.payload["model_id"],
            2,
        )

    def test_join_generates_join_accept(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.JOIN,
                "role": 0,
                "sequence_number": 1,
                "join_id": 15,
                "model_id": 2,
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.4", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: neighbor_module.has_neighbor(
                        "10.0.0.4",
                        5000,
                    )
                )
            )

            self.assertTrue(
                wait_until(
                    lambda: len(self.socket.sent) >= 1
                )
            )

            payload, destination = self.socket.sent[-1]

            parsed = parse_datagram(payload)

            self.assertEqual(
                destination,
                ("10.0.0.4", 5000),
            )

            self.assertEqual(
                parsed.packet_type,
                PacketType.JOIN_ACCEPT,
            )

            self.assertEqual(
                parsed.payload["join_id"],
                15,
            )

            self.assertEqual(
                parsed.payload["rejected"],
                0,
            )

        finally:
            communication.stop()

    def test_repeated_join_resends_join_accept_without_duplicate(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.JOIN,
                "role": 0,
                "sequence_number": 1,
                "join_id": 15,
                "model_id": 2,
            })

            # First JOIN
            communication.receive_datagram(
                raw,
                ("10.0.0.4", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: len(self.socket.sent) >= 1
                )
            )

            first_count = len(self.socket.sent)

            # Second JOIN with the same Join ID
            communication.receive_datagram(
                raw,
                ("10.0.0.4", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: len(self.socket.sent) >= first_count + 1
                )
            )

            # JOIN_ACCEPT should have been sent again
            self.assertGreaterEqual(
                len(self.socket.sent),
                first_count + 1,
            )

            payload, destination = self.socket.sent[-1]
            parsed = parse_datagram(payload)

            self.assertEqual(
                destination,
                ("10.0.0.4", 5000),
            )

            self.assertEqual(
                parsed.packet_type,
                PacketType.JOIN_ACCEPT,
            )

            self.assertEqual(
                parsed.payload["join_id"],
                15,
            )

            self.assertEqual(
                parsed.payload["rejected"],
                0,
            )

            # Only one Neighbor Table entry should exist
            neighbors = neighbor_module.get_neighbors()

            self.assertEqual(
                len(neighbors),
                1,
            )

            neighbor = neighbors[0]

            self.assertEqual(
                neighbor.ip_address,
                "10.0.0.4",
            )

            self.assertEqual(
                neighbor.port,
                5000,
            )

            self.assertEqual(
                neighbor.join_id,
                15,
            )

            self.assertEqual(
                neighbor.association_state,
                AssociationState.JOIN_PENDING,
            )

        finally:
            communication.stop()

    def test_leave_updates_neighbor_module(self):
        neighbor_module = NeighborModule()

        # Pre-existing Sensor Node
        neighbor_module.process_join(
            ip_address="10.0.0.4",
            port=5000,
            join_id=15,
            model_id=2,
        )

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.LEAVE,
                "role": 0,
                "sequence_number": 2,
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.4", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: neighbor_module.get_neighbor(
                        "10.0.0.4",
                        5000,
                    ).association_state
                    == AssociationState.INACTIVE
                )
            )

            neighbor = neighbor_module.get_neighbor(
                "10.0.0.4",
                5000,
            )

            self.assertIsNotNone(neighbor)
            self.assertEqual(
                neighbor.association_state,
                AssociationState.INACTIVE,
            )

        finally:
            communication.stop()

    def test_sensor_data_refreshes_neighbor_membership(self):
        neighbor_module = NeighborModule()

        # Existing associated Sensor Node
        neighbor_module.add_neighbor(
            ip_address="10.0.0.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=100.0,
            expiration_time=110.0,
        )

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.SENSOR_DATA,
                "role": 0,
                "sequence_number": 1,
                "message_id": 10,
                "block_number": 0,
                "last_block_number": 0,
                "payload": b"TEST_SENSOR_DATA",
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.4", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: (
                        neighbor_module.get_neighbor(
                            "10.0.0.4",
                            5000,
                        ).last_heard
                        > 100.0
                    )
                )
            )

            neighbor = neighbor_module.get_neighbor(
                "10.0.0.4",
                5000,
            )

            self.assertEqual(
                neighbor.association_state,
                AssociationState.ASSOCIATED,
            )

            self.assertGreater(
                neighbor.expiration_time,
                110.0,
            )

        finally:
            communication.stop()

    def test_cluster_head_discovers_cluster_head(self):
        neighbor_module = NeighborModule()

        communication = CommunicationModule(
            self.socket,
            role=1,
            neighbor_module=neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        try:
            raw = serialize_message({
                "packet_type": PacketType.HELLO,
                "role": 1,
                "sequence_number": 1,
                "model_id": 3,
            })

            communication.receive_datagram(
                raw,
                ("10.0.0.5", 5000),
            )

            self.assertTrue(
                wait_until(
                    lambda: neighbor_module.has_neighbor(
                        "10.0.0.5",
                        5000,
                    )
                )
            )

            neighbor = neighbor_module.get_neighbor(
                "10.0.0.5",
                5000,
            )

            self.assertIsNotNone(neighbor)

            self.assertEqual(
                neighbor.node_role,
                NodeRole.CLUSTER_HEAD,
            )

            self.assertEqual(
                neighbor.association_state,
                AssociationState.DISCOVERED,
            )

            self.assertEqual(
                neighbor.model_id,
                3,
            )

        finally:
            communication.stop()




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

    def test_send_join_message(self):
        self.module.send_message(
            {
                "packet_type": PacketType.JOIN,
                "role": 0,
                "sequence_number": 1,
                "join_id": 15,
                "model_id": 2,
            },
            destination=("10.0.0.3", 5000),
        )

        self.assertEqual(len(self.socket.sent), 1)

        payload, addr = self.socket.sent[0]

        self.assertEqual(
            addr,
            ("10.0.0.3", 5000),
        )

        parsed = parse_datagram(payload)
        # print(parsed)
        # print(vars(parsed))

        self.assertEqual(
            parsed.packet_type,
            PacketType.JOIN,
        )
        self.assertEqual(
            parsed.role,
            0,
        )
        self.assertEqual(
            parsed.payload["join_id"],
            15,
        )
        self.assertEqual(
            parsed.payload["model_id"],
            2,
        )

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
