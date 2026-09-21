import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import PacketType, fragment_payload
from flame.communication.reliability import (
    MIN_WINDOW_SIZE,
    ReliabilityManager,
    prepare_fragments,
)


DEST = ("127.0.0.1", 9000)


def fragments(count=5):
    return {block: f"BLOCK_{block}".encode() for block in range(count)}


def register(manager, *, count=5, sequence=10, retries=None):
    kwargs = {} if retries is None else {"max_retries": retries}
    key = manager.register_transfer(
        DEST,
        PacketType.SENSOR_DATA,
        message_id=7,
        sequence_number=sequence,
        fragments=fragments(count),
        **kwargs,
    )
    return key


def sensor_fragment(block, last, sequence=10, data_id=7, payload=None):
    return {
        "version": 1,
        "packet_type": PacketType.SENSOR_DATA,
        "role": 0,
        "sequence_number": sequence,
        "data_id": data_id,
        "sensor_type": 1,
        "block_number": block,
        "last_block_number": last,
        "payload": payload if payload is not None else f"B{block}".encode(),
    }


class OutboundWindowTests(unittest.TestCase):
    def test_initial_window_starts_at_two(self):
        manager = ReliabilityManager()
        key = register(manager)
        action = manager.start_transfer(key, now=0.0)
        self.assertEqual([block for block, _ in action.datagrams], [0, 1])
        self.assertEqual((action.window_start, action.window_end), (0, 1))

    def test_minimum_window_cannot_be_one(self):
        with self.assertRaises(ValueError):
            ReliabilityManager(initial_window_size=1, min_window_size=1)
        self.assertEqual(MIN_WINDOW_SIZE, 2)

    def test_full_ack_advances_and_additively_increases(self):
        manager = ReliabilityManager()
        key = register(manager)
        manager.start_transfer(key, now=0.0)
        action = manager.process_ack(
            DEST, PacketType.SENSOR_DATA, 7, 10,
            window_start=0, window_end=1, received_blocks=[0, 1], now=0.1,
        )
        self.assertTrue(action.window_advanced)
        self.assertEqual((action.window_start, action.window_end), (2, 4))
        self.assertEqual([block for block, _ in action.datagrams], [2, 3, 4])
        self.assertEqual(manager.get_outbound_transfer(key).window_size, 3)

    def test_loss_keeps_current_bounds_and_retransmits_only_missing(self):
        manager = ReliabilityManager()
        key = register(manager)
        manager.start_transfer(key, now=0.0)
        action = manager.process_ack(
            DEST, PacketType.SENSOR_DATA, 7, 10,
            window_start=0, window_end=1, received_blocks=[0], now=0.1,
        )
        transfer = manager.get_outbound_transfer(key)
        self.assertEqual((transfer.window_start, transfer.window_end), (0, 1))
        self.assertEqual([block for block, _ in action.datagrams], [1])
        self.assertEqual(transfer.reduced_window_size, 2)

        next_action = manager.process_ack(
            DEST, PacketType.SENSOR_DATA, 7, 10,
            window_start=0, window_end=1, received_blocks=[0, 1], now=0.2,
        )
        self.assertEqual((next_action.window_start, next_action.window_end), (2, 3))
        self.assertEqual(manager.get_outbound_transfer(key).window_size, 2)

    def test_window_growth_is_capped(self):
        manager = ReliabilityManager(initial_window_size=2, max_window_size=3)
        key = register(manager, count=12)
        manager.start_transfer(key, now=0.0)
        manager.process_ack(DEST, PacketType.SENSOR_DATA, 7, 10, 0, 1, [0, 1], now=0.1)
        self.assertEqual(manager.get_outbound_transfer(key).window_size, 3)
        manager.process_ack(DEST, PacketType.SENSOR_DATA, 7, 10, 2, 4, [2, 3, 4], now=0.2)
        self.assertEqual(manager.get_outbound_transfer(key).window_size, 3)

    def test_final_ack_removes_transfer(self):
        manager = ReliabilityManager()
        key = register(manager, count=2)
        manager.start_transfer(key, now=0.0)
        action = manager.process_ack(
            DEST, PacketType.SENSOR_DATA, 7, 10, 0, 1, [0, 1], now=0.1
        )
        self.assertTrue(action.completed)
        self.assertFalse(manager.has_outbound_transfer(key))


class TimeoutTests(unittest.TestCase):
    def test_timeout_retransmits_only_unacknowledged_blocks(self):
        manager = ReliabilityManager(ack_timeout=0.1, ack_delay=0.01)
        key = register(manager)
        manager.start_transfer(key, now=0.0)
        manager.process_ack(
            DEST, PacketType.SENSOR_DATA, 7, 10, 0, 1, [0], now=0.02
        )
        actions = manager.service_outbound(now=0.2)
        self.assertEqual([block for block, _ in actions[0].datagrams], [1])

    def test_retry_exhaustion_fails_and_cleans_up(self):
        manager = ReliabilityManager(
            ack_timeout=0.1,
            ack_delay=0.01,
            max_retries=1,
        )
        key = register(manager, retries=1)
        manager.start_transfer(key, now=0.0)
        first = manager.service_outbound(now=0.2)[0]
        self.assertFalse(first.failed)
        second = manager.service_outbound(now=0.4)[0]
        self.assertTrue(second.failed)
        self.assertFalse(manager.has_outbound_transfer(key))


class InboundTests(unittest.TestCase):
    def test_out_of_order_window_is_accepted_and_reassembled(self):
        manager = ReliabilityManager()
        second = manager.receive_fragment(DEST, sensor_fragment(1, 1), now=0.0)
        self.assertIsNone(second.complete_fragments)
        first = manager.receive_fragment(DEST, sensor_fragment(0, 1), now=0.01)
        self.assertEqual([a.received_blocks for a in first.acknowledgements], [(0, 1)])
        self.assertEqual(
            b"".join(fragment["payload"] for fragment in first.complete_fragments),
            b"B0B1",
        )

    def test_partial_window_ack_lists_received_blocks(self):
        manager = ReliabilityManager(ack_timeout=0.2, ack_delay=0.05)
        manager.receive_fragment(DEST, sensor_fragment(0, 2), now=0.0)
        pending = manager.service_inbound(now=0.06)
        self.assertEqual(len(pending), 1)
        _key, ack = pending[0]
        self.assertEqual((ack.window_start, ack.window_end), (0, 1))
        self.assertEqual(ack.received_blocks, (0,))

    def test_duplicate_is_not_accepted_and_resends_ack(self):
        manager = ReliabilityManager()
        manager.receive_fragment(DEST, sensor_fragment(0, 1), now=0.0)
        manager.receive_fragment(DEST, sensor_fragment(1, 1), now=0.01)
        duplicate = manager.receive_fragment(DEST, sensor_fragment(0, 1), now=0.02)
        self.assertTrue(duplicate.duplicate)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.acknowledgements[0].received_blocks, (0, 1))

    def test_reused_identifier_with_new_sequence_is_new_transfer(self):
        manager = ReliabilityManager(completed_retention=10.0)
        first = manager.receive_fragment(DEST, sensor_fragment(0, 0, sequence=10), now=0.0)
        second = manager.receive_fragment(DEST, sensor_fragment(0, 0, sequence=11), now=0.1)
        self.assertIsNotNone(first.complete_fragments)
        self.assertIsNotNone(second.complete_fragments)
        self.assertFalse(second.duplicate)

    def test_incomplete_reassembly_expires(self):
        manager = ReliabilityManager(reassembly_timeout=0.1)
        manager.receive_fragment(DEST, sensor_fragment(0, 2), now=0.0)
        expired = manager.cleanup(now=0.2)
        self.assertEqual(len(expired), 1)
        self.assertEqual(manager.inbound_transfers, {})


class FragmentHelperTests(unittest.TestCase):
    def test_prepare_fragments_keys_by_block_number(self):
        prepared = prepare_fragments(fragment_payload(b"A" * 2500))
        self.assertEqual(sorted(prepared), [0, 1, 2])
        self.assertEqual([len(prepared[i]) for i in range(3)], [1024, 1024, 452])


if __name__ == "__main__":
    unittest.main()
