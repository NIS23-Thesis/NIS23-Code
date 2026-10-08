"""CLIVE localhost command line runner."""
from __future__ import annotations

import argparse
import json
import os
import tempfile

from emulator import Emulator
from orchestrator import Orchestrator


def build_smoke_plan(duration_s=5.0):
    # Thesis Table 4-8 low-density topology: 4 CH + 20 SN.
    return Orchestrator().prepare({
        "num_ch": 4,
        "num_sn": 20,
        "duration": duration_s,
        "seed": 810,
        "startup_delay_s": 1.0,
        "mobility": {
            "tick_interval": 0.5,
            "area_width_m": 350.0,
            "area_height_m": 350.0,
            "sn_mean_speed_mps": 1.0,
            "ch_mean_speed_mps": 4.0,
            "pause_time_range": [15.0, 25.0],
        },
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--scenario", help="JSON scenario for Orchestrator.prepare")
    parser.add_argument("--plan", help="existing localhost plan")
    parser.add_argument("--results", default="results")
    parser.add_argument(
        "--protocol",
        help="ProtocolInterface subclass as module:ClassName; default is NullProtocol",
    )
    args = parser.parse_args()

    if args.plan and args.scenario:
        parser.error("choose --plan or --scenario")

    if args.plan:
        print(Emulator(args.plan, args.results, protocol_class_path=args.protocol).run())
        return

    if args.scenario:
        with open(args.scenario, encoding="utf-8") as stream:
            plan = Orchestrator().prepare(json.load(stream))
    else:
        plan = build_smoke_plan(args.duration)

    with tempfile.TemporaryDirectory(prefix="clive-plan-") as directory:
        path = os.path.join(directory, "plan.json")
        plan.save(path)
        # The prepared in-memory dict trace is no longer needed once mobility.bin exists.
        # Release it before launching hundreds of worker processes.
        plan.trace = None
        print(Emulator(path, args.results, protocol_class_path=args.protocol).run())


if __name__ == "__main__":
    main()
