#!/usr/bin/env python3
"""Validate compact shared mobility storage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator import EmulationPlan, Orchestrator


def main():
    plan = Orchestrator().prepare({
        "num_ch": 10,
        "num_sn": 100,
        "runtime": {"duration_s": 10.0, "seed": 810, "startup_delay_s": 0.2},
        "mobility": {
            "model": "gauss_markov",
            "tick_interval": 0.1,
            "area_width_m": 350.0,
            "area_height_m": 350.0,
            "ch_mean_speed_mps": 4.0,
            "sn_mean_speed_mps": 1.0,
        },
    })
    sample = {
        (0, "CH-1"): tuple(plan.trace[0]["CH-1"]),
        (50, "SN-50"): tuple(plan.trace[50]["SN-50"]),
        (100, "SN-100"): tuple(plan.trace[100]["SN-100"]),
    }

    with tempfile.TemporaryDirectory(prefix="clive-mobility-store-") as td:
        td = Path(td)
        plan_path = td / "plan.json"
        plan.save(str(plan_path))
        mobility_path = td / "mobility.bin"
        raw = json.loads(plan_path.read_text())

        assert mobility_path.exists()
        assert "trace" not in raw, "plan.json must not embed the full trace"
        assert raw["trace_storage"]["file"] == "mobility.bin"
        assert raw["trace_storage"]["node_count"] == 110
        assert raw["trace_storage"]["tick_count"] == 101
        assert plan_path.stat().st_size < 100_000

        loaded = EmulationPlan.load(str(plan_path))
        assert loaded.trace is None, "loaded worker plan must not deserialize a trace dict"
        assert loaded.tick_count == 101
        with loaded.open_trace() as trace:
            for (tick, name), expected in sample.items():
                assert trace.position(tick, name) == expected

        # float64 storage is exactly 16 bytes per node per tick plus a tiny fixed header.
        expected_payload = 110 * 101 * 2 * 8
        assert mobility_path.stat().st_size < expected_payload + 64

        print("[PASS] plan.json contains metadata only; full trace is not duplicated in every worker")
        print("[PASS] mobility.bin stores one compact float64 trace for all workers")
        print("[PASS] read-only mapped trace reproduces exact precomputed coordinates")
        print(f"[INFO] plan.json={plan_path.stat().st_size} bytes mobility.bin={mobility_path.stat().st_size} bytes")
        print("Compact mobility storage validation: PASS")


if __name__ == "__main__":
    main()
