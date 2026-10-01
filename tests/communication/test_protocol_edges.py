import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import PacketType, serialize_sensor_data
from flame.communication.parser import parse_datagram
from flame.communication.reliability import ReliabilityManager


class AimdBehaviorTests(unittest.TestCase):
    def test_complete_windows_add_one_and_cap_at_maximum(self):
        manager = ReliabilityManager(max_window_size=4)
        key = manager.register_transfer(
            ("127.0.0.1", 7001), PacketType.SENSOR_DATA, 3, 12,
            {block: b"x" for block in range(12)},
        )
        action = manager.start_transfer(key, now=0)

        expected_window_size = 3
        while True:
            action = manager.process_ack(
                key.peer_address, PacketType.SENSOR_DATA, 3, 12,
                action.window_start, action.window_end,
                range(action.window_start, action.window_end + 1),
                now=0.1,
            )
            if action.completed:
                break
            self.assertTrue(action.window_advanced)
            transfer = manager.outbound_transfers[key]
            self.assertEqual(transfer.window_size, expected_window_size)
            expected_window_size = min(expected_window_size + 1, 4)

    def test_recovered_lossy_window_uses_reduced_size_without_increase(self):
        manager = ReliabilityManager(initial_window_size=4, max_window_size=8)
        key = manager.register_transfer(
            ("127.0.0.1", 7001), PacketType.SENSOR_DATA, 4, 13,
            {block: b"x" for block in range(10)},
        )
        manager.start_transfer(key, now=0)
        retry = manager.process_ack(
            key.peer_address, PacketType.SENSOR_DATA, 4, 13,
            0, 3, [0], now=0.1,
        )
        self.assertEqual([block for block, _ in retry.datagrams], [1, 2, 3])

        next_window = manager.process_ack(
            key.peer_address, PacketType.SENSOR_DATA, 4, 13,
            0, 3, [0, 1, 2, 3], now=0.2,
        )
        self.assertTrue(next_window.window_advanced)
        self.assertEqual((next_window.window_start, next_window.window_end), (4, 5))
        self.assertEqual(manager.outbound_transfers[key].window_size, 2)


class FragmentCompletionTests(unittest.TestCase):
    def test_rejects_block_outside_declared_transfer(self):
        manager = ReliabilityManager()
        packet = {
            "packet_type": PacketType.SENSOR_DATA,
            "sequence_number": 7,
            "data_id": 1,
            "sensor_type": 1,
            "block_number": 2,
            "last_block_number": 1,
            "payload": b"invalid",
        }
        with self.assertRaises(ValueError):
            manager.receive_fragment(("127.0.0.1", 7001), packet)

    def test_transfer_keys_include_source_address(self):
        manager = ReliabilityManager()
        packet = {
            "packet_type": PacketType.SENSOR_DATA,
            "sequence_number": 7,
            "data_id": 1,
            "sensor_type": 1,
            "block_number": 0,
            "last_block_number": 1,
            "payload": b"first",
        }
        first = manager.receive_fragment(("127.0.0.1", 7001), packet)
        second = manager.receive_fragment(("127.0.0.1", 7002), packet)
        self.assertNotEqual(first.key, second.key)
        self.assertEqual(len(manager.inbound_transfers), 2)


class PacketSizeAndChecksumTests(unittest.TestCase):
    def test_maximum_flame_fragment_fits_below_1500_byte_ipv4_mtu(self):
        datagram = serialize_sensor_data(
            version=1,
            role=0,
            sequence_number=1,
            data_id=1,
            sensor_type=1,
            block_number=0,
            last_block_number=0,
            payload=b"x" * 1024,
        )
        self.assertEqual(len(datagram), 1034)
        self.assertLessEqual(len(datagram) + 20 + 8, 1500)

    def test_flame_does_not_detect_payload_corruption_without_application_checksum(self):
        original = serialize_sensor_data(
            version=1,
            role=0,
            sequence_number=1,
            data_id=1,
            sensor_type=1,
            block_number=0,
            last_block_number=0,
            payload=b"original",
        )
        altered = original[:-1] + b"!"
        parsed = parse_datagram(altered)
        self.assertEqual(parsed.payload["payload"], b"origina!")
        self.assertNotIn("checksum", parsed.payload)


if __name__ == "__main__":
    unittest.main()
