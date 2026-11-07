"""Checks CLIVE's event-view query and adapter timer (no FLAME modifications)."""
from __future__ import annotations

import sys
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visualizer import RunData
from flame_protocol import FlameProtocol


class EventQueryTests(unittest.TestCase):
    def make_run(self):
        run = object.__new__(RunData)
        run._lock = threading.RLock()
        run._refresh_events = lambda: None
        run._events = deque(maxlen=200_000)
        # Deliberately mixed node timestamps: time order != file ingestion order.
        for seq in range(1, 2001):
            run._events.append({"seq": seq, "time_s": (seq * 73) % 137 / 10})
        return run

    def test_recent_events_identical_to_full_sort(self):
        run = self.make_run()
        for after, start, end, limit in [(None, None, None, 1),
                                          (None, 2, 8, 50),
                                          (250, 4, 11, 230),
                                          (1980, None, None, 300)]:
            expected = [e for e in run._events if
                        (after is None or e["seq"] > after) and
                        (start is None or e["time_s"] >= start) and
                        (end is None or e["time_s"] <= end)]
            expected = sorted(expected, key=lambda e: (e["time_s"], e["seq"]))[-limit:]
            got = run.events(from_s=start, to_s=end, after_seq=after, limit=limit)
            self.assertEqual(got["events"], expected)

    def test_limit_and_empty_window(self):
        run = self.make_run()
        self.assertEqual(run.events(from_s=999)["events"], [])
        self.assertEqual(len(run.events(limit=0)["events"]), 1)
        self.assertEqual(run.events(limit=5)["last_seq"], 2000)


class SensorTimerTests(unittest.TestCase):
    def test_exhausted_sample_still_runs_service_and_departure(self):
        """An exhausted finite data source must not suppress CLIVE housekeeping."""
        adapter = object.__new__(FlameProtocol)
        adapter.closed = False
        adapter.is_ch = False
        adapter.s = {"repeat_sensor_samples": False, "service_interval_s": 0.02}
        adapter._next_sample = 1.0
        adapter._next_hello = None
        adapter._next_leave = 1.0
        adapter._next_service = 1.0
        adapter._sample_source = SimpleNamespace(has_next=lambda repeat: False, cursor=2)
        seen = []
        adapter._record = lambda name, **kwargs: seen.append(name)
        adapter._is_joined = lambda: False
        adapter._service_join = lambda now: seen.append("service_join")
        adapter._flush_offline_samples = lambda: seen.append("flush")
        adapter._send_pending_models = lambda: None
        adapter.sensing = None
        adapter.neighbors = SimpleNamespace(check_expired_neighbors=lambda: seen.append("check_expired"))
        adapter.comms = SimpleNamespace(
            _service_send_buffer=lambda: seen.append("service_send"),
            _service_reliability=lambda: seen.append("service_reliability"),
            pop_received=lambda: None, pop_neighbor_messages=lambda: None,
            pop_sensor_messages=lambda: None, pop_fl_messages=lambda: None,
            pop_ack_messages=lambda: None, pop_events=lambda: None, pop_errors=lambda: [],
        )
        adapter.on_timer(1.0)
        self.assertIn("sample_source_exhausted", seen)
        self.assertIn("service_send", seen)
        self.assertIn("service_reliability", seen)
        self.assertTrue(adapter._left)
        self.assertIsNone(adapter._next_sample)


if __name__ == "__main__":
    unittest.main(verbosity=2)
