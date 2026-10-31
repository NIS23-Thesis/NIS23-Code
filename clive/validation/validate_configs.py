#!/usr/bin/env python3
"""Validate every shipped CLIVE JSON configuration without launching node processes."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator


def main():
    files = sorted((ROOT / "configs").rglob("*.json"))
    if not files:
        raise SystemExit("no JSON configurations found")
    for path in files:
        scenario = json.loads(path.read_text())
        plan = Orchestrator().prepare(scenario)
        assert len(plan.nodes) == scenario["num_ch"] + scenario["num_sn"]
        print(f"[PASS] {path.relative_to(ROOT)} ({len(plan.nodes)} nodes, {len(plan.trace)} ticks)")
    print(f"\nConfiguration validation: PASS ({len(files)} files)")


if __name__ == "__main__":
    main()
