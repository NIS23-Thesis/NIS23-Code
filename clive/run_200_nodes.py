"""Run a 200-node localhost CLIVE stress scenario.

Default composition is 20 Cluster Heads + 180 Sensor Nodes. This is a scalability stress
scenario, not one of the thesis Table 4-8 density levels (whose highest listed topology is
10 CH + 100 SN).
"""
from __future__ import annotations

import argparse
import os
import tempfile

from emulator import Emulator
from orchestrator import Orchestrator


def build_200_node_plan(duration_s: float, startup_delay_s: float):
    return Orchestrator().prepare({
        "num_ch": 20,
        "num_sn": 180,
        "duration": duration_s,
        "seed": 810,
        "startup_delay_s": startup_delay_s,
        "mobility": {
            "tick_interval": 0.5,
            "alpha": 0.5,
            "area_width_m": 40.0,
            "area_height_m": 40.0,
            "ch_mean_speed_mps": 5.0,
            "sn_mean_speed_mps": 50.0,
            "speed_stddev": 0.0,
            "direction_stddev": 0.0
        },
        "runtime": {
            "readiness_timeout_s": 120.0,
            "worker_grace_s": 15.0,
            "start_tolerance_s": 0.5,
            "io_poll_interval_s": 0.01,
            "receive_batch_size": 32,
            "receive_budget_s": 0.002
        }
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--startup-delay", type=float, default=1.0)
    parser.add_argument("--results", default="results-200")
    parser.add_argument(
        "--protocol",
        help="ProtocolInterface subclass as module:ClassName. Omit for NullProtocol.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="validate/generate the 200-node plan without spawning 200 processes",
    )
    parser.add_argument(
        "--plan-output",
        default="plan-200.json",
        help="persistent plan path used with --plan-only",
    )
    args = parser.parse_args()

    plan = build_200_node_plan(args.duration, args.startup_delay)
    if len(plan.nodes) != 200:
        raise AssertionError("200-node plan generation failed")

    if args.plan_only:
        path = os.path.abspath(args.plan_output)
        plan.save(path)
        print(f"200-node plan OK: {path} (20 CH + 180 SN)")
        return

    with tempfile.TemporaryDirectory(prefix="clive-200-plan-") as directory:
        path = os.path.join(directory, "plan.json")
        plan.save(path)
        run_dir = Emulator(path, args.results, protocol_class_path=args.protocol).run()
        print(run_dir)


if __name__ == "__main__":
    main()
