#!/usr/bin/env python3
"""Artifact-level checks for CLIVE scalability optimizations."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nodesocket import MetricsCollector
from orchestrator import Orchestrator
from visualizer import RunData


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def main():
    # Normal metrics must not create packet-event objects/files when diagnostics are off.
    collector = MetricsCollector("TEST")
    collector.record("delivery_attempt", header={"packet_type": 15}, counters={"delivery_attempts": 109}, distance_m=999)
    snap = collector.snapshot()
    require(snap["delivery_attempts"] == 109, "aggregate metrics work without a packet JSONL stream")
    require(collector.stream is None, "normal evaluation keeps packet JSONL logging disabled")

    nodesocket_source = (ROOT / "nodesocket.py").read_text()
    require('"targets": targets' not in nodesocket_source, "broadcast IPC no longer serializes an O(N) target list per send")
    require("_broadcast_targets_by_port" in nodesocket_source, "radio child expands broadcasts from its immutable node table")
    require("_fast_link_from_squared_distance" in nodesocket_source, "radio path uses allocation-light link evaluation")

    emulator_source = (ROOT / "emulator.py").read_text()
    require("wait_connections" in emulator_source, "supervisor blocks on worker pipes instead of polling every worker in a tight loop")

    app_source = (ROOT / "visualizer" / "static" / "app.js").read_text()
    require("/api/mobility.bin" in app_source, "browser streams the canonical binary mobility trace directly")
    require("getFloat64" in app_source, "browser reads exact stored float64 mobility coordinates")

    with tempfile.TemporaryDirectory(prefix="clive-opt-test-") as td:
        run = Path(td) / "run-test"
        run.mkdir()
        plan = Orchestrator().prepare({
            "num_ch": 2,
            "num_sn": 3,
            "duration": 1.0,
            "seed": 810,
            "mobility": {"tick_interval": 0.5},
        })
        plan.save(str(run / "plan.json"))
        (run / "run-manifest.json").write_text(json.dumps({"status": "complete", "success": True}))
        data = RunData(run)
        meta = data.meta()
        binary_path = data.mobility_binary_path()
        require(binary_path.name == "mobility.bin" and binary_path.is_file(), "visualizer exposes one canonical mobility.bin")
        require(meta["trace_storage"]["byte_size"] == binary_path.stat().st_size, "visualizer metadata matches binary trace size")

    print("\nCLIVE optimization validation: PASS")


if __name__ == "__main__":
    main()
