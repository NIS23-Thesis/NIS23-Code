import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

from sympy import python

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

    def test_two_node_sensor_ch_handshake(self):
        sensor_socket = FakeSocketInterface()
        ch_socket = FakeSocketInterface()

        sensor_neighbor_module = NeighborModule()
        ch_neighbor_module = NeighborModule()

        sensor = CommunicationModule(
            sensor_socket,
            role=0,
            neighbor_module=sensor_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        cluster_head = CommunicationModule(
            ch_socket,
            role=1,
            neighbor_module=ch_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        sensor_address = ("10.0.0.4", 5000)
        ch_address = ("10.0.0.5", 5000)

        try:
            # ---------------------------------------------------------
            # 1. Sensor discovers the Cluster Head through HELLO
            # ---------------------------------------------------------
            hello = serialize_message({
                "packet_type": PacketType.HELLO,
                "role": 1,
                "sequence_number": 1,
                "model_id": 2,
            })

            sensor.receive_datagram(
                hello,
                ch_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: sensor_neighbor_module.has_neighbor(
                        ch_address[0],
                        ch_address[1],
                    )
                )
            )

            sensor_ch = sensor_neighbor_module.get_neighbor(
                ch_address[0],
                ch_address[1],
            )

            self.assertEqual(
                sensor_ch.node_role,
                NodeRole.CLUSTER_HEAD,
            )

            self.assertEqual(
                sensor_ch.association_state,
                AssociationState.DISCOVERED,
            )

            # ---------------------------------------------------------
            # 2. Sensor starts JOIN toward the Cluster Head
            # ---------------------------------------------------------
            sensor_neighbor_module.start_join(
                ip_address=ch_address[0],
                port=ch_address[1],
                join_id=15,
                model_id=2,
            )

            sensor.send_message(
                {
                    "packet_type": PacketType.JOIN,
                    "role": 0,
                    "join_id": 15,
                    "model_id": 2,
                },
                destination=ch_address,
            )

            self.assertEqual(
                len(sensor_socket.sent),
                1,
            )

            join_raw, join_destination = sensor_socket.sent[-1]

            self.assertEqual(
                join_destination,
                ch_address,
            )

            # ---------------------------------------------------------
            # 3. Deliver JOIN to the Cluster Head
            # ---------------------------------------------------------
            cluster_head.receive_datagram(
                join_raw,
                sensor_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: ch_neighbor_module.has_neighbor(
                        sensor_address[0],
                        sensor_address[1],
                    )
                )
            )

            ch_sensor = ch_neighbor_module.get_neighbor(
                sensor_address[0],
                sensor_address[1],
            )

            self.assertEqual(
                ch_sensor.node_role,
                NodeRole.SENSOR_NODE,
            )

            self.assertEqual(
                ch_sensor.association_state,
                AssociationState.JOIN_PENDING,
            )

            self.assertEqual(
                ch_sensor.join_id,
                15,
            )

            # ---------------------------------------------------------
            # 4. CH automatically sends JOIN_ACCEPT
            # ---------------------------------------------------------
            self.assertTrue(
                wait_until(
                    lambda: len(ch_socket.sent) >= 1
                )
            )

            join_accept_raw, join_accept_destination = ch_socket.sent[-1]

            self.assertEqual(
                join_accept_destination,
                sensor_address,
            )

            parsed_join_accept = parse_datagram(
                join_accept_raw
            )

            self.assertEqual(
                parsed_join_accept.packet_type,
                PacketType.JOIN_ACCEPT,
            )

            self.assertEqual(
                parsed_join_accept.payload["join_id"],
                15,
            )

            self.assertEqual(
                parsed_join_accept.payload["rejected"],
                0,
            )

            # ---------------------------------------------------------
            # 5. Deliver JOIN_ACCEPT back to the Sensor
            # ---------------------------------------------------------
            sensor.receive_datagram(
                join_accept_raw,
                ch_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: (
                        sensor_neighbor_module.get_neighbor(
                            ch_address[0],
                            ch_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                    )
                )
            )

            sensor_ch = sensor_neighbor_module.get_neighbor(
                ch_address[0],
                ch_address[1],
            )

            self.assertEqual(
                sensor_ch.association_state,
                AssociationState.ASSOCIATED,
            )

        finally:
            sensor.stop()
            cluster_head.stop()

    def test_sensor_data_after_join_reaches_cluster_head(self):
        sensor_socket = FakeSocketInterface()
        ch_socket = FakeSocketInterface()

        sensor_neighbor_module = NeighborModule()
        ch_neighbor_module = NeighborModule()

        sensor = CommunicationModule(
            sensor_socket,
            role=0,
            neighbor_module=sensor_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        cluster_head = CommunicationModule(
            ch_socket,
            role=1,
            neighbor_module=ch_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        sensor_address = ("10.0.0.4", 5000)
        ch_address = ("10.0.0.5", 5000)

        try:
            # Establish the Sensor -> CH membership.
            sensor_neighbor_module.start_join(
                ip_address=ch_address[0],
                port=ch_address[1],
                join_id=15,
                model_id=2,
            )

            join_raw = sensor.send_message(
                {
                    "packet_type": PacketType.JOIN,
                    "role": 0,
                    "join_id": 15,
                    "model_id": 2,
                },
                destination=ch_address,
            )

            cluster_head.receive_datagram(
                join_raw,
                sensor_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: len(ch_socket.sent) >= 1
                )
            )

            join_accept_raw, _ = ch_socket.sent[-1]

            sensor.receive_datagram(
                join_accept_raw,
                ch_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: (
                        sensor_neighbor_module.get_neighbor(
                            ch_address[0],
                            ch_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                    )
                )
            )

            # Send SENSOR_DATA after association.
            sensor.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": 20,
                    "payload": b"TEST_SENSOR_DATA",
                },
                destination=ch_address,
            )

            self.assertTrue(
                len(sensor_socket.sent) >= 2
            )

            # The reliable SENSOR_DATA fragments are sent through
            # the Sensor's socket.
            sensor_data_packets = [
                item
                for item in sensor_socket.sent
                if parse_datagram(item[0]).packet_type == PacketType.SENSOR_DATA
            ]

            self.assertTrue(len(sensor_data_packets) >= 1)

            sensor_data_raw, sensor_data_destination = sensor_data_packets[0]

            self.assertEqual(
                sensor_data_destination,
                ch_address,
            )

            # Deliver SENSOR_DATA to the CH.
            cluster_head.receive_datagram(
                sensor_data_raw,
                sensor_address,
            )


            # Deliver SENSOR_DATA to the CH.
            cluster_head.receive_datagram(
                sensor_data_raw,
                sensor_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: ch_neighbor_module.get_neighbor(
                        sensor_address[0],
                        sensor_address[1],
                    ).association_state
                    == AssociationState.ASSOCIATED
                )
            )

            sensor_neighbor = ch_neighbor_module.get_neighbor(
                sensor_address[0],
                sensor_address[1],
            )

            self.assertIsNotNone(sensor_neighbor)

            self.assertEqual(
                sensor_neighbor.node_role,
                NodeRole.SENSOR_NODE,
            )

            self.assertEqual(
                sensor_neighbor.association_state,
                AssociationState.ASSOCIATED,
            )

            # Verify that the reassembled SENSOR_DATA was delivered.
            delivered = cluster_head.pop_sensor_messages()

            self.assertTrue(len(delivered) >= 1)

            packet = delivered[0]

            self.assertEqual(
                packet["packet_type"],
                PacketType.SENSOR_DATA,
            )

            self.assertEqual(
                packet["payload"],
                b"TEST_SENSOR_DATA",
            )


        finally:
            sensor.stop()
            cluster_head.stop()

    def test_two_sensors_one_cluster_head(self):
        sensor1_socket = FakeSocketInterface()
        sensor2_socket = FakeSocketInterface()
        ch_socket = FakeSocketInterface()

        sensor1_neighbor_module = NeighborModule()
        sensor2_neighbor_module = NeighborModule()
        ch_neighbor_module = NeighborModule()

        sensor1 = CommunicationModule(
            sensor1_socket,
            role=0,
            neighbor_module=sensor1_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        sensor2 = CommunicationModule(
            sensor2_socket,
            role=0,
            neighbor_module=sensor2_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        cluster_head = CommunicationModule(
            ch_socket,
            role=1,
            neighbor_module=ch_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        sensor1_address = ("10.0.0.4", 5000)
        sensor2_address = ("10.0.0.6", 5000)
        ch_address = ("10.0.0.5", 5000)

        try:
            # Both Sensors discover the same Cluster Head.
            hello = serialize_message({
                "packet_type": PacketType.HELLO,
                "role": 1,
                "sequence_number": 1,
                "model_id": 2,
            })

            sensor1.receive_datagram(
                hello,
                ch_address,
            )

            sensor2.receive_datagram(
                hello,
                ch_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: (
                        sensor1_neighbor_module.has_neighbor(
                            ch_address[0],
                            ch_address[1],
                        )
                        and sensor2_neighbor_module.has_neighbor(
                            ch_address[0],
                            ch_address[1],
                        )
                    )
                )
            )

            # Start independent JOIN states.
            sensor1_neighbor_module.start_join(
                ip_address=ch_address[0],
                port=ch_address[1],
                join_id=101,
                model_id=2,
            )

            sensor2_neighbor_module.start_join(
                ip_address=ch_address[0],
                port=ch_address[1],
                join_id=202,
                model_id=2,
            )

            # Sensor 1 sends JOIN.
            join1_raw = sensor1.send_message(
                {
                    "packet_type": PacketType.JOIN,
                    "role": 0,
                    "join_id": 101,
                    "model_id": 2,
                },
                destination=ch_address,
            )

            cluster_head.receive_datagram(
                join1_raw,
                sensor1_address,
            )

            # Sensor 2 sends JOIN.
            join2_raw = sensor2.send_message(
                {
                    "packet_type": PacketType.JOIN,
                    "role": 0,
                    "join_id": 202,
                    "model_id": 2,
                },
                destination=ch_address,
            )

            cluster_head.receive_datagram(
                join2_raw,
                sensor2_address,
            )

            # Both Sensors should exist independently in the CH table.
            self.assertTrue(
                wait_until(
                    lambda: (
                        ch_neighbor_module.has_neighbor(
                            sensor1_address[0],
                            sensor1_address[1],
                        )
                        and ch_neighbor_module.has_neighbor(
                            sensor2_address[0],
                            sensor2_address[1],
                        )
                    )
                )
            )

            sensor1_neighbor = ch_neighbor_module.get_neighbor(
                sensor1_address[0],
                sensor1_address[1],
            )

            sensor2_neighbor = ch_neighbor_module.get_neighbor(
                sensor2_address[0],
                sensor2_address[1],
            )

            self.assertEqual(
                sensor1_neighbor.node_role,
                NodeRole.SENSOR_NODE,
            )

            self.assertEqual(
                sensor2_neighbor.node_role,
                NodeRole.SENSOR_NODE,
            )

            self.assertEqual(
                sensor1_neighbor.association_state,
                AssociationState.JOIN_PENDING,
            )

            self.assertEqual(
                sensor2_neighbor.association_state,
                AssociationState.JOIN_PENDING,
            )

            self.assertEqual(
                sensor1_neighbor.join_id,
                101,
            )

            self.assertEqual(
                sensor2_neighbor.join_id,
                202,
            )

            # CH should have generated two JOIN_ACCEPT messages.
            self.assertTrue(
                wait_until(
                    lambda: len(ch_socket.sent) >= 2
                )
            )

            join_accept_packets = [
                parse_datagram(raw)
                for raw, _ in ch_socket.sent
                if parse_datagram(raw).packet_type == PacketType.JOIN_ACCEPT
            ]

            self.assertEqual(
                len(join_accept_packets),
                2,
            )

            join_accept_ids = {
                packet.payload["join_id"]
                for packet in join_accept_packets
            }

            self.assertEqual(
                join_accept_ids,
                {101, 202},
            )

            # Deliver each JOIN_ACCEPT to the correct Sensor.
            for raw, destination in ch_socket.sent:
                parsed = parse_datagram(raw)

                if parsed.packet_type != PacketType.JOIN_ACCEPT:
                    continue

                join_id = parsed.payload["join_id"]

                if join_id == 101:
                    sensor1.receive_datagram(
                        raw,
                        ch_address,
                    )
                elif join_id == 202:
                    sensor2.receive_datagram(
                        raw,
                        ch_address,
                    )

            # Both Sensors should become associated.
            self.assertTrue(
                wait_until(
                    lambda: (
                        sensor1_neighbor_module.get_neighbor(
                            ch_address[0],
                            ch_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                        and
                        sensor2_neighbor_module.get_neighbor(
                            ch_address[0],
                            ch_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                    )
                )
            )

            # Sensor 1 sends SENSOR_DATA.
            sensor1.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": 30,
                    "payload": b"SENSOR_1_DATA",
                },
                destination=ch_address,
            )

            # Sensor 2 sends SENSOR_DATA.
            sensor2.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": 31,
                    "payload": b"SENSOR_2_DATA",
                },
                destination=ch_address,
            )

            sensor1_data = [
                item
                for item in sensor1_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.SENSOR_DATA
            ]

            sensor2_data = [
                item
                for item in sensor2_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.SENSOR_DATA
            ]

            self.assertTrue(len(sensor1_data) >= 1)
            self.assertTrue(len(sensor2_data) >= 1)

            # Deliver Sensor 1 data to the CH.
            cluster_head.receive_datagram(
                sensor1_data[0][0],
                sensor1_address,
            )

            # Deliver Sensor 2 data to the CH.
            cluster_head.receive_datagram(
                sensor2_data[0][0],
                sensor2_address,
            )

            # Both CH-side Sensor entries should become associated.
            self.assertTrue(
                wait_until(
                    lambda: (
                        ch_neighbor_module.get_neighbor(
                            sensor1_address[0],
                            sensor1_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                        and
                        ch_neighbor_module.get_neighbor(
                            sensor2_address[0],
                            sensor2_address[1],
                        ).association_state
                        == AssociationState.ASSOCIATED
                    )
                )
            )

            # Verify both data messages were delivered.
            delivered = cluster_head.pop_sensor_messages()

            self.assertEqual(
                len(delivered),
                2,
            )

            payloads = {
                packet["payload"]
                for packet in delivered
            }

            self.assertEqual(
                payloads,
                {
                    b"SENSOR_1_DATA",
                    b"SENSOR_2_DATA",
                },
            )

        finally:
            sensor1.stop()
            sensor2.stop()
            cluster_head.stop()

    def test_cluster_head_model_update(self):
        ch1_socket = FakeSocketInterface()
        ch2_socket = FakeSocketInterface()

        ch1_neighbor_module = NeighborModule()
        ch2_neighbor_module = NeighborModule()

        ch1 = CommunicationModule(
            ch1_socket,
            role=1,
            neighbor_module=ch1_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        ch2 = CommunicationModule(
            ch2_socket,
            role=1,
            neighbor_module=ch2_neighbor_module,
            auto_start=True,
            poll_interval=0.01,
        )

        ch1_address = ("10.0.0.5", 5000)
        ch2_address = ("10.0.0.7", 5000)

        try:
            # CH 1 discovers CH 2.
            hello_from_ch2 = serialize_message({
                "packet_type": PacketType.HELLO,
                "role": 1,
                "sequence_number": 1,
                "model_id": 2,
            })

            ch1.receive_datagram(
                hello_from_ch2,
                ch2_address,
            )

            # CH 2 discovers CH 1.
            hello_from_ch1 = serialize_message({
                "packet_type": PacketType.HELLO,
                "role": 1,
                "sequence_number": 2,
                "model_id": 3,
            })

            ch2.receive_datagram(
                hello_from_ch1,
                ch1_address,
            )

            self.assertTrue(
                wait_until(
                    lambda: (
                        ch1_neighbor_module.has_neighbor(
                            ch2_address[0],
                            ch2_address[1],
                        )
                        and
                        ch2_neighbor_module.has_neighbor(
                            ch1_address[0],
                            ch1_address[1],
                        )
                    )
                )
            )

            # Verify that CH-to-CH neighbors remain DISCOVERED.
            ch1_neighbor = ch1_neighbor_module.get_neighbor(
                ch2_address[0],
                ch2_address[1],
            )

            ch2_neighbor = ch2_neighbor_module.get_neighbor(
                ch1_address[0],
                ch1_address[1],
            )

            self.assertEqual(
                ch1_neighbor.node_role,
                NodeRole.CLUSTER_HEAD,
            )

            self.assertEqual(
                ch2_neighbor.node_role,
                NodeRole.CLUSTER_HEAD,
            )

            self.assertEqual(
                ch1_neighbor.association_state,
                AssociationState.DISCOVERED,
            )

            self.assertEqual(
                ch2_neighbor.association_state,
                AssociationState.DISCOVERED,
            )

            self.assertEqual(
                ch1_neighbor.model_id,
                2,
            )

            self.assertEqual(
                ch2_neighbor.model_id,
                3,
            )

            # CH 1 sends a MODEL_UPDATE to CH 2.
            model_payload = b"MODEL_UPDATE_TEST"

            ch1.send_message(
                {
                    "packet_type": PacketType.MODEL_UPDATE,
                    "role": 1,
                    "model_id": 3,
                    "update_id": 40,
                    "payload": model_payload,
                },
                destination=ch2_address,
            )

            model_update_packets = [
                item
                for item in ch1_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.MODEL_UPDATE
            ]

            self.assertTrue(
                len(model_update_packets) >= 1
            )

            model_update_raw, model_update_destination = (
                model_update_packets[0]
            )

            self.assertEqual(
                model_update_destination,
                ch2_address,
            )

            # Deliver the MODEL_UPDATE to CH 2.
            ch2.receive_datagram(
                model_update_raw,
                ch1_address,
            )

            # Verify that the model update reached the FL buffer.
            self.assertTrue(
                wait_until(
                    lambda: len(ch2.fl_buffer) >= 1
                )
            )

            delivered = ch2.pop_fl_messages()

            self.assertEqual(
                delivered[0]["packet_type"],
                PacketType.MODEL_UPDATE,
            )

            self.assertEqual(
                delivered[0]["payload"],
                model_payload,
            )

            # CH-to-CH relationship must remain DISCOVERED.
            ch1_neighbor = ch1_neighbor_module.get_neighbor(
                ch2_address[0],
                ch2_address[1],
            )

            self.assertEqual(
                ch1_neighbor.association_state,
                AssociationState.DISCOVERED,
            )

        finally:
            ch1.stop()
            ch2.stop()

    def test_cluster_head_large_model_update_reassembly(self):
        ch1_socket = FakeSocketInterface()
        ch2_socket = FakeSocketInterface()

        ch1 = CommunicationModule(
            ch1_socket,
            role=1,
            auto_start=True,
            poll_interval=0.01,
        )

        ch2 = CommunicationModule(
            ch2_socket,
            role=1,
            auto_start=True,
            poll_interval=0.01,
        )

        ch1_address = ("10.0.0.5", 5000)
        ch2_address = ("10.0.0.7", 5000)

        try:
            model_payload = b"M" * 2500

            ch1.send_message(
                {
                    "packet_type": PacketType.MODEL_UPDATE,
                    "role": 1,
                    "model_id": 3,
                    "update_id": 41,
                    "payload": model_payload,
                },
                destination=ch2_address,
            )

            # The initial reliability window is 2,
            # so only blocks 0 and 1 are transmitted immediately.
            model_update_packets = [
                item
                for item in ch1_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.MODEL_UPDATE
            ]

            self.assertEqual(len(model_update_packets), 2)

            parsed_fragments = [
                parse_datagram(item[0]).payload
                for item in model_update_packets
            ]

            self.assertEqual(
                [fragment["block_number"] for fragment in parsed_fragments],
                [0, 1],
            )

            self.assertEqual(
                [
                    len(fragment["payload"])
                    for fragment in parsed_fragments
                ],
                [1024, 1024],
            )

            self.assertEqual(
                [fragment["last_block_number"] for fragment in parsed_fragments],
                [2, 2],
            )

            # Deliver blocks 0 and 1 to CH2.
            for raw, _destination in model_update_packets:
                ch2.receive_datagram(
                    raw,
                    source_address=ch1_address,
                )

            # CH2 should acknowledge the received window.
            self.assertTrue(
                wait_until(
                    lambda: any(
                        parse_datagram(item[0]).packet_type
                        == PacketType.MODEL_UPDATE_ACK
                        for item in ch2_socket.sent
                    )
                )
            )

            ack_packet = next(
                item
                for item in ch2_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.MODEL_UPDATE_ACK
            )

            parsed_ack = parse_datagram(ack_packet[0]).payload

            self.assertEqual(parsed_ack["update_id"], 41)
            self.assertEqual(parsed_ack["window_start"], 0)
            self.assertEqual(parsed_ack["window_end"], 1)
            self.assertEqual(parsed_ack["received_blocks"], [0, 1])

            # Deliver the ACK back to CH1.
            ch1.receive_datagram(
                ack_packet[0],
                source_address=ch2_address,
            )

            # CH1 should transmit the remaining block 2.
            self.assertTrue(
                wait_until(
                    lambda: len(
                        [
                            item
                            for item in ch1_socket.sent
                            if parse_datagram(item[0]).packet_type
                            == PacketType.MODEL_UPDATE
                        ]
                    )
                    == 3
                )
            )

            all_model_update_packets = [
                item
                for item in ch1_socket.sent
                if parse_datagram(item[0]).packet_type
                == PacketType.MODEL_UPDATE
            ]

            final_fragment = parse_datagram(
                all_model_update_packets[2][0]
            ).payload

            self.assertEqual(final_fragment["block_number"], 2)
            self.assertEqual(final_fragment["last_block_number"], 2)
            self.assertEqual(len(final_fragment["payload"]), 452)

            # Deliver the final fragment to CH2.
            ch2.receive_datagram(
                all_model_update_packets[2][0],
                source_address=ch1_address,
            )

            # CH2 should now have the complete reassembled model update.
            self.assertTrue(
                wait_until(
                    lambda: len(ch2.fl_buffer.items) > 0
                )
            )

            delivered = ch2.pop_fl_messages()

            self.assertEqual(len(delivered), 1)
            self.assertEqual(
                delivered[0]["packet_type"],
                PacketType.MODEL_UPDATE,
            )
            self.assertEqual(delivered[0]["model_id"], 3)
            self.assertEqual(delivered[0]["update_id"], 41)
            self.assertEqual(delivered[0]["payload"], model_payload)

        finally:
            ch1.stop()
            ch2.stop()





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
