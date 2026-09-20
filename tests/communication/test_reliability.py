import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.reliability import ReliabilityManager, prepare_fragments
from flame.communication.message import fragment_payload

DEST = "192.168.1.20"
PTYPE = "SENSOR_DATA"


def make_transfer(reliability, max_retries=3, window_size=3):
    fragments = {
        0: b"PACKET_BLOCK_0", 1: b"PACKET_BLOCK_1", 2: b"PACKET_BLOCK_2",
        3: b"PACKET_BLOCK_3", 4: b"PACKET_BLOCK_4",
    }
    reliability.register_transfer(
        destination_address=DEST, packet_type=PTYPE, message_id=10,
        fragments=fragments, window_size=window_size, max_retries=max_retries,
    )
    return fragments


class DuplicateDetectionTests(unittest.TestCase):
    def test_first_arrival_is_not_duplicate(self):
        r = ReliabilityManager()
        result = r.process_received_fragment(
            source_address="10.0.0.1", packet_type=PTYPE, message_id=1, block_number=0,
            fragment={"block_number": 0, "payload": b"X"},
        )
        self.assertIsNotNone(result)

    def test_second_arrival_of_same_block_is_duplicate(self):
        r = ReliabilityManager()
        fragment = {"block_number": 0, "payload": b"X"}
        r.process_received_fragment("10.0.0.1", PTYPE, 1, 0, fragment)
        duplicate = r.process_received_fragment("10.0.0.1", PTYPE, 1, 0, fragment)
        self.assertIsNone(duplicate)

    def test_different_block_number_is_not_duplicate(self):
        r = ReliabilityManager()
        r.process_received_fragment("10.0.0.1", PTYPE, 1, 0, {"block_number": 0, "payload": b"X"})
        result = r.process_received_fragment("10.0.0.1", PTYPE, 1, 1, {"block_number": 1, "payload": b"Y"})
        self.assertIsNotNone(result)


class AckGenerationTests(unittest.TestCase):
    def test_complete_ack_has_no_missing_blocks(self):
        r = ReliabilityManager()
        ack = r.create_ack(PTYPE, message_id=10, received_blocks={0, 1, 2}, last_block_number=2)
        self.assertEqual(ack["missing_blocks"], [])

    def test_ack_reports_missing_block(self):
        r = ReliabilityManager()
        ack = r.create_ack(PTYPE, message_id=10, received_blocks={0, 2}, last_block_number=2)
        self.assertEqual(ack["missing_blocks"], [1])


class WindowTests(unittest.TestCase):
    def test_current_window_bounded_by_fragment_count(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=3)
        self.assertEqual(r.get_current_window(DEST, PTYPE, 10), [0, 1, 2])

    def test_window_clamps_at_total_fragment_count(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=10)  # bigger than the 5 available fragments
        self.assertEqual(r.get_current_window(DEST, PTYPE, 10), [0, 1, 2, 3, 4])

    def test_get_fragment_returns_none_for_unknown_block(self):
        r = ReliabilityManager()
        make_transfer(r)
        self.assertIsNone(r.get_fragment(DEST, PTYPE, 10, 99))

    def test_get_missing_fragments_looks_up_by_block_number(self):
        r = ReliabilityManager()
        make_transfer(r)
        result = r.get_missing_fragments(DEST, PTYPE, 10, [1, 2])
        self.assertEqual(result, [b"PACKET_BLOCK_1", b"PACKET_BLOCK_2"])


class AckProcessingAimdTests(unittest.TestCase):
    def test_missing_ack_shrinks_window_and_does_not_advance(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=3)
        result = r.process_ack(DEST, PTYPE, 10, received_blocks={0, 2}, missing_blocks=[1])
        self.assertEqual(result, {"advanced": False, "retransmit": [1]})
        self.assertEqual(r.get_window_size(DEST, PTYPE, 10), 1)  # 3 // 2 = 1
        self.assertEqual(r.get_current_window(DEST, PTYPE, 10), [0])  # window_start unchanged

    def test_full_ack_advances_window_and_grows_it(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=3)
        result = r.process_ack(DEST, PTYPE, 10, received_blocks={0, 1, 2}, missing_blocks=[])
        self.assertEqual(result, {"advanced": True, "retransmit": []})
        self.assertEqual(r.get_window_size(DEST, PTYPE, 10), 4)  # additive increase: 3 -> 4
        self.assertEqual(r.get_current_window(DEST, PTYPE, 10), [3, 4])

    def test_transfer_is_cleaned_up_once_all_blocks_acked(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=5)  # window covers all 5 fragments at once
        r.process_ack(DEST, PTYPE, 10, received_blocks={0, 1, 2, 3, 4}, missing_blocks=[])
        self.assertNotIn((DEST, PTYPE, 10), r.outbound_transfers)

    def test_KNOWN_GAP_window_size_has_no_upper_cap(self):
        r = ReliabilityManager()
        fragments = {i: f"BLOCK_{i}".encode() for i in range(200)}
        r.register_transfer(DEST, PTYPE, 20, fragments, window_size=50)
        for _ in range(10):
            window = r.get_current_window(DEST, PTYPE, 20)
            if not window:
                break
            r.process_ack(DEST, PTYPE, 20, received_blocks=set(window), missing_blocks=[])
        transfer = r.outbound_transfers.get((DEST, PTYPE, 20))
        if transfer is not None:
            self.assertLessEqual(transfer["window_size"], transfer["max_window_size"])


class TimeoutAndRetryTests(unittest.TestCase):
    def test_not_timed_out_immediately(self):
        r = ReliabilityManager()
        make_transfer(r)
        r.start_timeout(DEST, PTYPE, 10)
        self.assertFalse(r.is_timeout(DEST, PTYPE, 10, timeout_duration=0.2))

    def test_times_out_after_duration_elapses(self):
        r = ReliabilityManager()
        make_transfer(r)
        r.start_timeout(DEST, PTYPE, 10)
        time.sleep(0.05)
        self.assertTrue(r.is_timeout(DEST, PTYPE, 10, timeout_duration=0.02))

    def test_process_timeout_halves_window_and_increments_retry(self):
        r = ReliabilityManager()
        make_transfer(r, window_size=4)
        r.start_timeout(DEST, PTYPE, 10)
        time.sleep(0.02)
        fragments = r.process_timeout(DEST, PTYPE, 10, timeout_duration=0.01)
        self.assertTrue(len(fragments) > 0)
        self.assertEqual(r.get_window_size(DEST, PTYPE, 10), 2)  # 4 // 2
        self.assertEqual(r.outbound_transfers[(DEST, PTYPE, 10)]["retry_count"], 1)

    def test_transfer_removed_after_exceeding_max_retries(self):
        r = ReliabilityManager()
        make_transfer(r, max_retries=3)
        key = (DEST, PTYPE, 10)
        for _ in range(4):
            r.outbound_transfers[key]["timeout"] = time.time() - 1
            r.process_timeout(DEST, PTYPE, 10, timeout_duration=0.01)
        self.assertNotIn(key, r.outbound_transfers)

    def test_KNOWN_BUG_max_retries_argument_is_ignored(self):
        r = ReliabilityManager()
        make_transfer(r, max_retries=10)
        key = (DEST, PTYPE, 10)
        self.assertEqual(
            r.outbound_transfers[key]["max_retries"], 10,
        )
        for _ in range(10):
            r.outbound_transfers[key]["timeout"] = time.time() - 1
            r.process_timeout(DEST, PTYPE, 10, timeout_duration=0.01)
        self.assertIn(key, r.outbound_transfers)

        r.outbound_transfers[key]["timeout"] = time.time() - 1
        r.process_timeout(DEST, PTYPE, 10, timeout_duration=0.01)
        self.assertNotIn(key, r.outbound_transfers)


class FragmentIntegrationTests(unittest.TestCase):
    def test_prepare_fragments_keys_by_block_number(self):
        payload = b"A" * 2500
        fragments = fragment_payload(payload)
        prepared = prepare_fragments(fragments)
        self.assertEqual(sorted(prepared.keys()), [0, 1, 2])
        self.assertEqual([len(v) for v in prepared.values()], [1024, 1024, 452])


if __name__ == "__main__":
    unittest.main()
