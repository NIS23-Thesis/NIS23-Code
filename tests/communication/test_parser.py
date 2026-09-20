import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import LeaveReason, PacketType, serialize_header
from flame.communication.parser import parse_datagram, serialize_message


class SerializeMessageTests(unittest.TestCase):
    """serialize_message() accepts dicts OR objects with matching attributes -- test both."""

    def test_hello_from_dict(self):
        raw = serialize_message({"packet_type": PacketType.HELLO, "role": 1, "sequence_number": 1, "model_id": 9})
        parsed = parse_datagram(raw)
        self.assertEqual(parsed.packet_type, PacketType.HELLO)
        self.assertEqual(parsed.payload["model_id"], 9)

    def test_sensor_data_from_dict(self):
        raw = serialize_message({
            "packet_type": PacketType.SENSOR_DATA, "role": 0, "sequence_number": 2,
            "data_id": 4, "sensor_type": 1, "block_number": 0, "last_block_number": 0, "payload": b"hi",
        })
        parsed = parse_datagram(raw)
        self.assertEqual(parsed.payload["payload"], b"hi")

    def test_leave_from_dict_with_default_reason(self):
        raw = serialize_message({"packet_type": PacketType.LEAVE, "role": 0, "sequence_number": 3})
        parsed = parse_datagram(raw)
        self.assertEqual(parsed.payload["reason"], LeaveReason.NORMAL_DISCONNECTION)

    def test_packet_type_as_string_name(self):
        raw = serialize_message({"packet_type": "HELLO", "role": 1, "sequence_number": 1, "model_id": 1})
        parsed = parse_datagram(raw)
        self.assertEqual(parsed.packet_type, PacketType.HELLO)

    def test_object_with_attributes_instead_of_dict(self):
        class FakeMessage:
            packet_type = PacketType.HELLO
            role = 1
            sequence_number = 5
            model_id = 2
        raw = serialize_message(FakeMessage())
        parsed = parse_datagram(raw)
        self.assertEqual(parsed.payload["model_id"], 2)

    def test_unsupported_packet_type_raises(self):
        with self.assertRaises(ValueError):
            serialize_message({"packet_type": 99, "role": 0, "sequence_number": 0})


class ParseDatagramTests(unittest.TestCase):
    def test_rejects_wrong_protocol_version(self):
        raw = serialize_header(version=2, packet_type=PacketType.HELLO, role=1, sequence_number=1) + b"\x00"
        with self.assertRaises(ValueError):
            parse_datagram(raw, expected_version=1)

    def test_rejects_unknown_packet_type(self):
        raw = bytes([0x1F, 0x00, 0x00, 0x01])  # type nibble 0xF has no PacketType member
        with self.assertRaises(ValueError):
            parse_datagram(raw)

    def test_rejects_role_mismatched_with_packet_type(self):
        raw = serialize_header(version=1, packet_type=PacketType.HELLO, role=0, sequence_number=1) + b"\x01"
        with self.assertRaises(ValueError):
            parse_datagram(raw)


if __name__ == "__main__":
    unittest.main()
