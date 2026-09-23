"""CLIVE command line: smoke run or a supplied scenario/plan."""
from __future__ import annotations
import argparse
import json
import os
import tempfile
from emulator import Emulator
from orchestrator import Orchestrator


def build_smoke_plan(duration_s=20.0):
    return Orchestrator().prepare(dict(num_ch=4, num_sn=20, duration=duration_s,
        mode="localhost", seed=810, startup_delay_s=5.0,
        mobility=dict(tick_interval=.1, sn_mean_speed_mps=4.0, ch_mean_speed_mps=12.0,
                      pause_time_range=(0.0, 0.0))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--scenario", help="JSON scenario for Orchestrator.prepare")
    parser.add_argument("--plan", help="existing plan (must have a future epoch)")
    parser.add_argument("--results", default="results")
    parser.add_argument("--local-underlay-ip")
    parser.add_argument("--protocol", help="trusted Python module:ClassName")
    args = parser.parse_args()
    if args.plan and args.scenario:
        parser.error("choose --plan or --scenario")
    if args.plan:
        print(Emulator(args.plan, args.results, local_underlay_ip=args.local_underlay_ip,
                       protocol_class_path=args.protocol).run())
    else:
        if args.scenario:
            with open(args.scenario, encoding="utf-8") as stream:
                plan = Orchestrator().prepare(json.load(stream))
        else:
            plan = build_smoke_plan(args.duration)
        with tempfile.TemporaryDirectory(prefix="clive-plan-") as directory:
            path = os.path.join(directory, "plan.json")
            plan.save(path)
            print(Emulator(path, args.results, local_underlay_ip=args.local_underlay_ip,
                           protocol_class_path=args.protocol).run())


if __name__ == "__main__":
    main()
