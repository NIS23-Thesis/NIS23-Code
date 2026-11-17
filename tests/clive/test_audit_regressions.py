"""Regression tests for the 2026-11-12 audit fixes."""
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clive"))


class TimelineBuckets(unittest.TestCase):
    def _run(self, duration, events, bucket):
        import visualizer
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "debug").mkdir()
            (run / "debug" / "n-packets.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in events))
            obj = visualizer.RunData.__new__(visualizer.RunData)
            import threading
            obj._lock = threading.RLock(); obj._timeline_caches = {}
            obj._plan = SimpleNamespace(config=SimpleNamespace(duration_s=duration))
            obj._event_log_sources = lambda: [(run / "debug" / "n-packets.jsonl", "packet")]
            return obj.timeline(bucket)

    def test_no_phantom_bucket_when_duration_is_multiple(self):
        r = self._run(6.0, [{"event": "transmit", "time_s": 5.9}], 1.0)
        self.assertEqual([x["time_s"] for x in r["rows"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual(r["totals"]["sent"], 1)

    def test_event_after_nominal_end_extends(self):
        r = self._run(6.0, [{"event": "receive", "time_s": 6.2}], 1.0)
        self.assertEqual(len(r["rows"]), 7)
        self.assertEqual(r["rows"][6]["received"], 1)

    def test_partial_last_bucket_and_totals(self):
        ev = [{"event": "transmit", "time_s": t} for t in (0.1, 2.4, 5.9)]
        r = self._run(6.0, ev, 2.5)
        self.assertEqual(len(r["rows"]), 3)
        self.assertEqual(sum(x["sent"] for x in r["rows"]), 3)


class RevisionLimit(unittest.TestCase):
    def test_overflow_is_recorded_not_raised(self):
        from flame_protocol import FlameProtocol
        events = []
        stub = type("S", (), {})()
        stub.stats = Counter()
        stub._record = lambda name, **kw: events.append((name, kw))
        FlameProtocol._revision_limit_reached(stub, OverflowError("Model update ID cannot exceed 8 bits."))
        self.assertEqual(stub.stats["model_revision_limit_reached"], 1)
        self.assertEqual(events[0][0], "model_revision_limit_reached")


if __name__ == "__main__":
    unittest.main()
