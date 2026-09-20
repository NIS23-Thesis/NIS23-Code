import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.datagram_buffer import DatagramBuffer


class DatagramBufferTests(unittest.TestCase):
    def test_fifo_order(self):
        buf = DatagramBuffer(max_size=5)
        buf.put(b"a")
        buf.put(b"b")
        buf.put(b"c")
        self.assertEqual(buf.get(), b"a")
        self.assertEqual(buf.get(), b"b")
        self.assertEqual(buf.get(), b"c")
        self.assertIsNone(buf.get())

    def test_overflow_drops_oldest_and_counts(self):
        buf = DatagramBuffer(max_size=3)
        for b in (b"a", b"b", b"c"):
            buf.put(b)
        self.assertEqual(buf.receive_overflow_counter, 0)

        buf.put(b"d")  # buffer was full -> should drop b"a"
        self.assertEqual(buf.receive_overflow_counter, 1)
        self.assertEqual(len(buf), 3)

        remaining = [buf.get() for _ in range(3)]
        self.assertEqual(remaining, [b"b", b"c", b"d"])

    def test_peek_does_not_remove(self):
        buf = DatagramBuffer(max_size=3)
        buf.put(b"a")
        self.assertEqual(buf.peek(), b"a")
        self.assertEqual(len(buf), 1)  # still there
        self.assertEqual(buf.get(), b"a")
        self.assertIsNone(buf.peek())

    def test_rejects_non_bytes(self):
        buf = DatagramBuffer(max_size=3)
        with self.assertRaises(TypeError):
            buf.put("not bytes")  # noqa: this must raise, per the buffer's own type guard

    def test_empty_and_clear(self):
        buf = DatagramBuffer(max_size=3)
        self.assertTrue(buf.empty())
        buf.put(b"a")
        self.assertFalse(buf.empty())
        buf.clear()
        self.assertTrue(buf.empty())


if __name__ == "__main__":
    unittest.main()
