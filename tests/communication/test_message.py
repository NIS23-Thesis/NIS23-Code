import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import (
    LeaveReason,
    PacketType,
    deserialize_header,
    deserialize_hello,
    deserialize_join,
    deserialize_join_accept,
    deserialize_leave,
    deserialize_model_update,
    deserialize_model_update_ack,
    deserialize_sensor_data,
    deserialize_sensor_data_ack,
    fragment_payload,
    reassemble_fragments,
    serialize_header,
    serialize_hello,
    serialize_join,
    serialize_join_accept,
    serialize_leave,
    serialize_model_update,
    serialize_model_update_ack,
    serialize_sensor_data,
    serialize_sensor_data_ack,
)


class HeaderTests(unittest.TestCase):
    def test_header_is_four_bytes_and_round_trips(self):
        raw = serialize_header(version=1, packet_type=PacketType.SENSOR_DATA, role=1, sequence_number=300)
        self.assertEqual(len(raw), 4)
        decoded = deserialize_header(raw)
        self.assertEqual(decoded, {
            "version": 1, "packet_type": PacketType.SENSOR_DATA, "role": 1, "sequence_number": 300,
        })

    def test_bit_layout_matches_spec(self):
        # byte0 = version(4) | packet_type(4); byte1 = role(1) | padding(7); bytes2-3 = seq (big-endian)
        raw = serialize_header(version=1, packet_type=PacketType.SENSOR_DATA, role=1, sequence_number=300)
        self.assertEqual(raw[0] >> 4, 1)                       # version nibble
        self.assertEqual(raw[0] & 0x0F, PacketType.SENSOR_DATA)  # type nibble
        self.assertEqual(raw[1] >> 7, 1)                       # role bit
        self.assertEqual(raw[1] & 0x7F, 0)                     # padding must be zero
        self.assertEqual(int.from_bytes(raw[2:4], "big"), 300)

    def test_rejects_version_over_4_bits(self):
        with self.assertRaises(ValueError):
            serialize_header(version=16, packet_type=PacketType.HELLO, role=0, sequence_number=0)

    def test_rejects_sequence_number_over_16_bits(self):
        with self.assertRaises(ValueError):
            serialize_header(version=1, packet_type=PacketType.HELLO, role=0, sequence_number=70000)

    def test_rejects_unknown_packet_type_value(self):
        # Craft a header byte with packet-type nibble 0x0F, which has no PacketType member.
        raw = bytes([0x1F, 0x00, 0x00, 0x01])
        with self.assertRaises(ValueError):
            deserialize_header(raw)

    def test_rejects_truncated_datagram(self):
        with self.assertRaises(ValueError):
            deserialize_header(b"\x00\x00")

    def test_role_vs_packet_type_is_enforced_by_parser(self):
        raw = serialize_hello(version=1, role=0, sequence_number=1, model_id=1)
        with self.assertRaises(ValueError):
            from flame.communication.parser import parse_datagram

            parse_datagram(raw)

    def test_rejects_nonzero_reserved_header_bits(self):
        raw = bytearray(serialize_hello(version=1, role=1, sequence_number=1, model_id=1))
        raw[1] |= 0x01
        with self.assertRaises(ValueError):
            deserialize_header(bytes(raw))


class ControlPayloadTests(unittest.TestCase):
    def test_hello_round_trip(self):
        raw = serialize_hello(version=1, role=1, sequence_number=10, model_id=5)
        decoded = deserialize_hello(raw)
        self.assertEqual(decoded["model_id"], 5)
        self.assertEqual(decoded["packet_type"], PacketType.HELLO)

    def test_join_round_trip(self):
        raw = serialize_join(version=1, role=0, sequence_number=20, model_id=5, join_id=1000)
        decoded = deserialize_join(raw)
        self.assertEqual(decoded["join_id"], 1000)
        self.assertEqual(decoded["model_id"], 5)

    def test_join_accept_round_trip(self):
        raw = serialize_join_accept(version=1, role=1, sequence_number=21, join_id=1000, timeout=30, rejected=0)
        decoded = deserialize_join_accept(raw)
        self.assertEqual(decoded, {
            "version": 1, "packet_type": PacketType.JOIN_ACCEPT, "role": 1, "sequence_number": 21,
            "join_id": 1000, "timeout": 30, "rejected": 0,
        })

    def test_leave_round_trip(self):
        raw = serialize_leave(version=1, role=0, sequence_number=30, reason=LeaveReason.TASK_COMPLETED)
        decoded = deserialize_leave(raw)
        self.assertEqual(decoded["reason"], LeaveReason.TASK_COMPLETED)

    def test_leave_rejects_unknown_reason_byte(self):
        good = serialize_leave(version=1, role=0, sequence_number=1, reason=LeaveReason.NORMAL_DISCONNECTION)
        tampered = good[:4] + bytes([0xEE])
        with self.assertRaises(ValueError):
            deserialize_leave(tampered)


class FragmentablePayloadTests(unittest.TestCase):
    def test_sensor_data_round_trip(self):
        raw = serialize_sensor_data(
            version=1, role=0, sequence_number=40, data_id=10, sensor_type=1,
            block_number=0, last_block_number=0, payload=b"MNIST_SAMPLE",
        )
        decoded = deserialize_sensor_data(raw)
        self.assertEqual(decoded["data_id"], 10)
        self.assertEqual(decoded["payload"], b"MNIST_SAMPLE")

    def test_sensor_data_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            serialize_sensor_data(
                version=1, role=0, sequence_number=1, data_id=1, sensor_type=1,
                block_number=0, last_block_number=0, payload=b"x" * 1025,
            )

    def test_sensor_data_rejects_block_after_last_block(self):
        with self.assertRaises(ValueError):
            serialize_sensor_data(
                version=1, role=0, sequence_number=1, data_id=1, sensor_type=1,
                block_number=2, last_block_number=1, payload=b"x",
            )

    def test_sensor_data_deserializer_rejects_oversized_wire_payload(self):
        header = serialize_header(1, PacketType.SENSOR_DATA, 0, 1)
        body = bytes([1, 1]) + (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
        with self.assertRaises(ValueError):
            deserialize_sensor_data(header + body + (b"x" * 1025))

    def test_sensor_data_rejects_invalid_identifier(self):
        with self.assertRaises(ValueError):
            serialize_sensor_data(
                version=1, role=0, sequence_number=1, data_id=256, sensor_type=1,
                block_number=0, last_block_number=0, payload=b"x",
            )

    def test_model_update_round_trip(self):
        raw = serialize_model_update(
            version=1, role=1, sequence_number=50, update_id=3, model_id=5,
            block_number=0, last_block_number=0, payload=b"WEIGHTS",
        )
        decoded = deserialize_model_update(raw)
        self.assertEqual(decoded["update_id"], 3)
        self.assertEqual(decoded["payload"], b"WEIGHTS")

    def test_sensor_data_ack_round_trip(self):
        raw = serialize_sensor_data_ack(
            version=1, sequence_number=60, data_id=10,
            window_start=2, window_end=4, received_blocks=[2, 4],
        )
        decoded = deserialize_sensor_data_ack(raw)
        self.assertEqual(decoded["data_id"], 10)
        self.assertEqual(decoded["window_start"], 2)
        self.assertEqual(decoded["window_end"], 4)
        self.assertEqual(decoded["received_blocks"], [2, 4])

    def test_model_update_ack_round_trip(self):
        raw = serialize_model_update_ack(
            version=1, sequence_number=70, update_id=3,
            window_start=0, window_end=1, received_blocks=[0, 1],
        )
        decoded = deserialize_model_update_ack(raw)
        self.assertEqual(decoded["update_id"], 3)
        self.assertEqual(decoded["received_blocks"], [0, 1])

    def test_ack_rejects_blocks_outside_window(self):
        with self.assertRaises(ValueError):
            serialize_sensor_data_ack(
                version=1, sequence_number=1, data_id=1,
                window_start=2, window_end=4, received_blocks=[1, 2],
            )

    def test_ack_rejects_malformed_count(self):
        raw = serialize_sensor_data_ack(
            version=1, sequence_number=1, data_id=1,
            window_start=0, window_end=1, received_blocks=[0, 1],
        )
        with self.assertRaises(ValueError):
            deserialize_sensor_data_ack(raw[:-1])


class FragmentationAndReassemblyTests(unittest.TestCase):
    def test_empty_payload_is_one_fragment(self):
        self.assertEqual(fragment_payload(b""), [(0, 0, b"")])

    def test_fragments_cover_full_payload_in_order(self):
        payload = b"A" * 2500
        fragments = fragment_payload(payload)
        self.assertEqual(len(fragments), 3)  # 1024 + 1024 + 452
        self.assertEqual([len(f) for _, _, f in fragments], [1024, 1024, 452])
        self.assertEqual(b"".join(f for _, _, f in fragments), payload)

    def test_full_wire_round_trip_with_fragmentation(self):
        payload = b"B" * 2500
        decoded_fragments = []
        for block_number, last_block_number, fragment in fragment_payload(payload):
            packet = serialize_sensor_data(
                version=1, role=0, sequence_number=block_number, data_id=10, sensor_type=1,
                block_number=block_number, last_block_number=last_block_number, payload=fragment,
            )
            decoded_fragments.append(deserialize_sensor_data(packet))
        self.assertEqual(reassemble_fragments(decoded_fragments), payload)

    def test_reassembly_rejects_missing_fragment(self):
        payload = b"C" * 2500
        decoded = [deserialize_sensor_data(serialize_sensor_data(
            version=1, role=0, sequence_number=bn, data_id=1, sensor_type=1,
            block_number=bn, last_block_number=lb, payload=frag,
        )) for bn, lb, frag in fragment_payload(payload)]
        with self.assertRaises(ValueError):
            reassemble_fragments([decoded[0], decoded[2]])  # block 1 missing

    def test_reassembly_handles_out_of_order(self):
        payload = b"D" * 2500
        decoded = [deserialize_sensor_data(serialize_sensor_data(
            version=1, role=0, sequence_number=bn, data_id=1, sensor_type=1,
            block_number=bn, last_block_number=lb, payload=frag,
        )) for bn, lb, frag in fragment_payload(payload)]
        shuffled = [decoded[2], decoded[0], decoded[1]]
        self.assertEqual(reassemble_fragments(shuffled), payload)

    def test_reassembly_rejects_duplicate_fragment(self):
        payload = b"E" * 2500
        decoded = [deserialize_sensor_data(serialize_sensor_data(
            version=1, role=0, sequence_number=bn, data_id=1, sensor_type=1,
            block_number=bn, last_block_number=lb, payload=frag,
        )) for bn, lb, frag in fragment_payload(payload)]
        with self.assertRaises(ValueError):
            reassemble_fragments([decoded[0], decoded[1], decoded[1], decoded[2]])


if __name__ == "__main__":
    unittest.main()
