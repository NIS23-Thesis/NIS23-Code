#!/usr/bin/env python3
"""List, validate, plan, or execute CLIVE evaluation scenario groups."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "configs" / "evaluations"
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator


def scenario_files(group: str):
    if group == "all":
        return sorted(path for path in CONFIG_ROOT.rglob("*.json") if path.name != "manifest.json")
    folder = CONFIG_ROOT / group
    if not folder.is_dir():
        raise SystemExit(f"unknown evaluation group: {group}")
    return sorted(path for path in folder.glob("*.json") if path.name != "manifest.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("group", nargs="?", default="all", choices=["all", "mobility", "density", "area", "radio", "topology", "thesis_matrix"])
    p.add_argument("--list", action="store_true", help="list matching scenarios")
    p.add_argument("--run", action="store_true", help="execute scenarios; default only validates/builds plans")
    p.add_argument("--protocol", help="ProtocolInterface class as module:ClassName")
    p.add_argument("--duration", type=float, help="override duration for exploratory runs only")
    p.add_argument("--allow-short-thesis-smoke", action="store_true", help="explicitly permit sub-300s thesis-matrix smoke checks; NOT evaluation data")
    p.add_argument("--results", default="results/evaluations")
    args = p.parse_args()

    files = scenario_files(args.group)
    if args.duration is not None and args.duration <= 0:
        p.error('--duration must be positive')
    if (args.group in {'thesis_matrix', 'all'} and args.duration is not None
            and args.duration < 300 and not args.allow_short_thesis_smoke):
        p.error('Chapter 4.7 requires >=300 seconds. Use --allow-short-thesis-smoke for diagnostics, never for final thesis metrics.')
    if args.duration is not None and args.duration < 300 and args.allow_short_thesis_smoke:
        print('WARNING: SHORT-DURATION DIAGNOSTIC ONLY — these are not Chapter 4.7 evaluation results', flush=True)
    if args.list:
        for path in files:
            print(path.relative_to(ROOT))
        return

    for path in files:
        scenario = json.loads(path.read_text())
        if args.duration is not None:
            scenario.setdefault("runtime", {})["duration_s"] = args.duration
        plan = Orchestrator().prepare(scenario)
        print(f"[PLAN OK] {path.relative_to(ROOT)} | nodes={len(plan.nodes)} ticks={len(plan.trace)}")
        if not args.run:
            continue
        with tempfile.TemporaryDirectory(prefix="clive-eval-") as td:
            tmp = Path(td) / "scenario.json"
            tmp.write_text(json.dumps(scenario))
            cmd = [sys.executable, str(ROOT / "clive.py"), "--scenario", str(tmp), "--results", args.results]
            if args.protocol:
                cmd += ["--protocol", args.protocol]
            subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
