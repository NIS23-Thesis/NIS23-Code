import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import PacketType, reassemble_fragments
from flame.communication.reliability import ReliabilityManager


SOURCE = ("127.0.0.1", 7000)


def fragment(block, last, *, sensor_type=1, payload=b"x"):
    return {
        "version": 1,
        "packet_type": PacketType.SENSOR_DATA,
        "role": 0,
        "sequence_number": 25,
        "data_id": 4,
        "sensor_type": sensor_type,
        "block_number": block,
        "last_block_number": last,
        "payload": payload,
    }


class StatefulReassemblyTests(unittest.TestCase):
    def test_waits_for_every_block_before_completion(self):
        manager = ReliabilityManager()
        self.assertIsNone(
            manager.receive_fragment(SOURCE, fragment(0, 2, payload=b"a")).complete_fragments
        )
        self.assertIsNone(
            manager.receive_fragment(SOURCE, fragment(2, 2, payload=b"c")).complete_fragments
        )
        complete = manager.receive_fragment(SOURCE, fragment(1, 2, payload=b"b"))
        self.assertEqual(reassemble_fragments(complete.complete_fragments), b"abc")

    def test_rejects_inconsistent_last_block_number(self):
        manager = ReliabilityManager()
        manager.receive_fragment(SOURCE, fragment(0, 2))
        with self.assertRaises(ValueError):
            manager.receive_fragment(SOURCE, fragment(1, 3))

    def test_rejects_inconsistent_message_metadata(self):
        manager = ReliabilityManager()
        manager.receive_fragment(SOURCE, fragment(0, 2, sensor_type=1))
        with self.assertRaises(ValueError):
            manager.receive_fragment(SOURCE, fragment(1, 2, sensor_type=2))


if __name__ == "__main__":
    unittest.main()
