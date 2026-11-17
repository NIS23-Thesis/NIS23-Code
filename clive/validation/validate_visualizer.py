"""Small artifact-level validation for the CLIVE browser visualizer."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import Orchestrator
from visualizer import RunData


def main():
    with tempfile.TemporaryDirectory(prefix="clive-visualizer-test-") as tmp:
        run = Path(tmp) / "run-test"
        run.mkdir()
        plan = Orchestrator().prepare({
            "num_ch": 2,
            "num_sn": 3,
            "duration": 1.0,
            "seed": 810,
            "startup_delay_s": 0.2,
            "mobility": {
                "tick_interval": 0.5,
                "area_width_m": 100.0,
                "area_height_m": 100.0,
                "ch_mean_speed_mps": 4.0,
                "sn_mean_speed_mps": 1.0,
            },
        })
        plan.save(str(run / "plan.json"))
        (run / "run-manifest.json").write_text(json.dumps({
            "status": "running",
            "success": False,
            "epoch_start": plan.epoch_start,
        }))

        packet = {
            "event": "transmit",
            "node_name": "CH-1",
            "common_header": {"packet_type": 1, "sequence_number": 7},
            "counters": {"packets_sent": 1, "bits_sent": 64, "energy_sent_j": 0.001},
            "time_s": 0.2,
            "destination": ["127.0.0.4", 27062],
            "is_broadcast": False,
        }
        (run / "debug" / "packets").mkdir(parents=True)
        (run / "debug" / "events").mkdir(parents=True)
        (run / "debug" / "packets" / "CH-1.jsonl").write_text(json.dumps(packet) + "\n")
        (run / "debug" / "events" / "CH-1.jsonl").write_text(json.dumps({
            "type": "protocol_send", "node_name": "CH-1", "time_s": 0.2, "sequence": 7
        }) + "\n")

        data = RunData(run)
        meta = data.meta()
        frame = data.frame(0.6)
        trace = data.mobility_trace()
        events = data.events(from_s=0, to_s=1)
        metrics = data.metrics()
        timeline = data.timeline(bucket_s=0.5)

        assert meta["ready"] and len(meta["nodes"]) == 5
        assert frame["tick"] == 1 and len(frame["positions"]) == 5
        assert trace["ready"] and len(trace["frames"]) == len(plan.trace)
        assert trace["node_names"] == [node.name for node in plan.nodes]
        # Compact browser trace must preserve the exact precomputed coordinates; no smoothing.
        first_name = trace["node_names"][0]
        assert trace["frames"][1][0:2] == plan.trace[1][first_name]
        assert "next_positions" not in frame
        assert len(events["events"]) == 2
        assert metrics["totals"]["packets_sent"] == 1
        assert metrics["totals"]["bits_sent"] == 64
        assert timeline["rows"] and timeline["rows"][0]["sent"] == 1
        assert timeline["has_packet_traffic"] is True
        assert timeline["totals"]["sent"] == 1
        # A 1.0 s run contains [0.0, 0.5) and [0.5, 1.0).
        # The empty [1.0, 1.5) interval is not part of the experiment.
        assert [row["time_s"] for row in timeline["rows"]] == [0.0, 0.5]
        print("CLIVE visualizer validation PASS")


if __name__ == "__main__":
    main()
