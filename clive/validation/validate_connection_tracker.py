"""Observational node-connection telemetry regression (no FLAME modifications)."""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator
from visualizer import RunData


def main():
    with tempfile.TemporaryDirectory(prefix="connection-tracker-") as folder:
        root = Path(folder) / "run-test"
        root.mkdir()
        plan = Orchestrator().prepare({
            "num_ch": 2, "num_sn": 1, "duration": 10,
            "logging": {"protocol_events": False, "packet_events": False},
            "runtime": {"duration_s": 10, "startup_delay_s": 0.4},
            "mobility": {"model": "static", "tick_interval": 0.5},
        })
        plan.save(str(root / "plan.json"))
        addr = {node.name: list(node.address) for node in plan.nodes}
        rows = {
            "SN-1": [
                {"type":"flame_join_sent", "destination":addr["CH-1"], "time_s":0.5},
                {"type":"flame_joined", "destination":addr["CH-1"], "sequence_number":7,"time_s":0.8},
                {"type":"flame_connection_lost", "destination":addr["CH-1"], "reason":"association became inactive or expired","time_s":4.0},
                {"type":"flame_join_sent", "destination":addr["CH-2"], "time_s":5.0},
                {"type":"flame_joined", "destination":addr["CH-2"], "time_s":5.5},
                {"type":"flame_leave_sent", "destination":addr["CH-2"], "time_s":9.0},
            ],
            "CH-1": [
                {"type":"flame_sensor_received", "source":addr["SN-1"], "sequence_number":8, "time_s":1.0},
                {"type":"flame_member_expired", "source":addr["SN-1"], "reason":"membership timeout","time_s":4.1},
            ],
            "CH-2": [
                {"type":"flame_model_sent", "destination":addr["CH-1"], "time_s":3.5},
                {"type":"flame_transfer_failed", "packet_type":4, "destination":addr["CH-1"],"reason":"retry_exhausted","time_s":3.7},
            ],
        }
        for name, events in rows.items():
            path = root / "debug" / "connections" / f"{name}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(dict(event,node_name=name)) + "\n" for event in events))
        run = RunData(root)
        rel = run.relationships()
        assert rel["available"] and len(rel["events"]) == 10, rel
        assert [r["time_s"] for r in rel["events"]] == sorted(r["time_s"] for r in rel["events"])
        assert [(r["source"],r["target"]) for r in rel["events"] if r["action"]=="flame_member_expired"] == [("SN-1","CH-1")]
        assert any(r["reason"] == "retry_exhausted" and r["packet_type"] == 4 for r in rel["events"])
        assert len(run.relationships()["events"]) == 10  # no duplicate ingestion
        assert run.events()["events"]  # compact connection events visible in timeline
        print("CLIVE compact connection provenance PASS")

if __name__ == "__main__":
    main()
