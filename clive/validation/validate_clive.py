#!/usr/bin/env python3
"""End-to-end validation for localhost-only CLIVE.

Checks Orchestrator -> EmulationPlan -> Emulator -> NodeBootstrapper -> NodeSocket and the
ProtocolInterface boundary. Use ``--stress-200`` to also launch 200 independent node
processes with NullProtocol for a short scalability smoke run.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emulator import Emulator
from orchestrator import GaussMarkovNode, MobilityConfig, Orchestrator
import random
import math
from protocol_interface import ProtocolInterface


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def make_probe_plan(duration=0.36):
    return Orchestrator().prepare({
        "num_ch": 1,
        "num_sn": 1,
        "duration": duration,
        "seed": 810,
        "startup_delay_s": 0.15,
        "mobility": {
            "tick_interval": 0.5,
            "area_width_m": 5.0,
            "area_height_m": 5.0,
            "ch_mean_speed_mps": 0.0,
            "sn_mean_speed_mps": 0.0,
            "speed_stddev": 0.0,
            "direction_stddev": 0.0
        },
        "runtime": {
            "readiness_timeout_s": 10.0,
            "start_tolerance_s": 0.25,
            "io_poll_interval_s": 0.002
        },
        "logging": {
            "protocol_events": True,
            "packet_events": True,
            "mobility_tsv": True
        }
    })


def validate_plan_generation():
    plan = Orchestrator().prepare({
        "num_ch": 4,
        "num_sn": 20,
        "duration": 1.0,
        "seed": 123,
        "mobility": {"tick_interval": 0.5},
    })
    require(len(plan.nodes) == 24, "Orchestrator creates the thesis low-density 4 CH + 20 SN topology")
    require(all(node.ip.startswith("127.") for node in plan.nodes), "All virtual nodes use loopback addresses")
    require(len({node.address for node in plan.nodes}) == 24, "Every localhost node address is unique")
    require(set(plan.trace[0]) == {node.name for node in plan.nodes}, "Mobility trace contains every node")

    plan200 = Orchestrator().prepare({
        "num_ch": 20,
        "num_sn": 180,
        "duration": 0.5,
        "seed": 810,
        "mobility": {"tick_interval": 0.5},
    })
    require(len(plan200.nodes) == 200, "200-node plan generation succeeds")
    require(len({node.address for node in plan200.nodes}) == 200, "200-node plan has unique loopback endpoints")

    # Boundary reflection must turn both the instantaneous and asymptotic direction inward.
    # Otherwise Gauss-Markov immediately pulls a reflected node back into the wall and nodes
    # accumulate along the simulation edges.
    mobility = MobilityConfig(
        tick_interval=0.1, alpha=0.5, area_width_m=10.0, area_height_m=10.0,
        ch_mean_speed_mps=4.0, sn_mean_speed_mps=1.0, speed_stddev=0.0,
        direction_stddev=0.0,
    )
    mover = GaussMarkovNode(4.0, 9.9, 5.0, mobility, random.Random(1))
    mover.direction = 0.0
    mover.mean_direction = 0.0
    mover.speed = 4.0
    mover.step(0.1)
    bounced_x = mover.x
    require(0.0 < mover.x < 10.0 and math.cos(mover.direction) < 0, "Gauss-Markov reflects inward at the right boundary")
    require(math.cos(mover.mean_direction) < 0, "Gauss-Markov reflects its asymptotic mean direction at the boundary")
    mover.step(0.1)
    require(mover.x < bounced_x, "Reflected Gauss-Markov node continues away from the edge instead of sticking")


def validate_protocol_boundary():
    forbidden = {"step", "tick", "on_tick", "next_wakeup", "on_timer"}
    present = forbidden.intersection(ProtocolInterface.__dict__)
    require(not present, "ProtocolInterface has no mobility-tick/step callback")
    required = {"start", "on_datagram", "close"}
    require(required.issubset(ProtocolInterface.__dict__), "ProtocolInterface exposes lifecycle + datagram callbacks only")


def validate_end_to_end():
    with tempfile.TemporaryDirectory(prefix="clive-e2e-") as td:
        td = Path(td)
        plan = make_probe_plan()
        plan_path = td / "plan.json"
        plan.save(plan_path)
        run_dir = Path(Emulator(str(plan_path), str(td / "results"), protocol_class_path="example_protocol:IndependentProbeProtocol").run())
        manifest = json.loads((run_dir / "run-manifest.json").read_text())
        require(manifest["success"] and manifest["status"] == "complete", "End-to-end localhost run completes successfully")

        send_times = []
        receive_count = 0
        for node in plan.nodes:
            events = (run_dir / "debug" / "events" / f"{node.name}.jsonl").read_text().splitlines()
            parsed = [json.loads(line) for line in events]
            send_times.extend(e["time_s"] for e in parsed if e.get("type") == "protocol_send")
            receive_count += sum(e.get("type") == "protocol_receive" for e in parsed)
            metrics = json.loads((run_dir / "metrics.json").read_text())["nodes"][node.name]
            require(metrics["success"], f"{node.name} metrics report successful shutdown")

        # Mobility quantum is 0.5 s while the entire test is only 0.36 s. Multiple protocol
        # sends therefore prove protocol execution is not being stepped by mobility ticks.
        require(len(send_times) >= 6, "Protocol-owned 70 ms timers fire repeatedly inside one 500 ms mobility quantum")
        require(receive_count > 0, "Protocol datagrams traverse NodeSocket and reach peer callbacks")


def stress_200():
    with tempfile.TemporaryDirectory(prefix="clive-200-test-") as td:
        td = Path(td)
        plan = Orchestrator().prepare({
            "num_ch": 20,
            "num_sn": 180,
            "duration": 0.25,
            "seed": 810,
            "startup_delay_s": 0.2,
            "mobility": {
                "tick_interval": 0.5,
                "area_width_m": 40.0,
                "area_height_m": 40.0,
                "ch_mean_speed_mps": 0.0,
                "sn_mean_speed_mps": 0.0,
                "speed_stddev": 0.0,
                "direction_stddev": 0.0
            },
            "runtime": {
                "readiness_timeout_s": 120.0,
                "worker_grace_s": 15.0,
                "start_tolerance_s": 1.0,
                "io_poll_interval_s": 0.01
            }
        })
        path = td / "plan.json"
        plan.save(path)
        run_dir = Path(Emulator(str(path), str(td / "results")).run())
        manifest = json.loads((run_dir / "run-manifest.json").read_text())
        metrics = json.loads((run_dir / "metrics.json").read_text())
        require(manifest["success"], "200-node localhost process run completes")
        require(len(metrics["nodes"]) == 200, "200-node run produces one compact run-level metrics file")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress-200", action="store_true")
    args = parser.parse_args()
    validate_plan_generation()
    validate_protocol_boundary()
    validate_end_to_end()
    if args.stress_200:
        stress_200()
    print("\nCLIVE localhost validation: PASS")


if __name__ == "__main__":
    main()
