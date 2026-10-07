#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nodesocket import link_conditions
from orchestrator import GaussMarkovNode, MobilityConfig, NodeRole, Orchestrator


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def test_static_topologies():
    for topology in ("line", "star", "grid", "random"):
        plan = Orchestrator().prepare({
            "num_ch": 1,
            "num_sn": 5,
            "duration": 2.0,
            "seed": 810,
            "mobility": {
                "model": "static",
                "initial_topology": topology,
                "tick_interval": 0.5,
                "area_width_m": 300.0,
                "area_height_m": 300.0,
                "topology_margin_m": 20.0,
                "star_radius_m": 60.0,
            },
        })
        first = plan.trace[0]
        check(all(plan.trace[t] == first for t in plan.trace), f"static {topology} topology remains stationary")

    star = Orchestrator().prepare({
        "num_ch": 1,
        "num_sn": 5,
        "duration": 1.0,
        "mobility": {
            "model": "static", "initial_topology": "star", "tick_interval": 0.5,
            "area_width_m": 300.0, "area_height_m": 300.0, "star_radius_m": 60.0,
        },
        "link": {"range_mode": "explicit", "max_range_m": 60.0},
    })
    hub = star.trace[0]["CH-1"]
    leaf_distances = [math.hypot(star.trace[0][f"SN-{i}"][0]-hub[0], star.trace[0][f"SN-{i}"][1]-hub[1]) for i in range(1,6)]
    check(all(abs(d-60.0) < 1e-6 for d in leaf_distances), "star topology places leaves at configured hub radius")

    import json
    scenario_root = Path(__file__).resolve().parents[1] / "configs" / "demos"
    strict_star = Orchestrator().prepare(json.loads((scenario_root / "static_star.json").read_text()))
    pos = strict_star.trace[0]
    adjacency = {}
    for src in strict_star.nodes:
        reach = []
        tx_range = strict_star.link_config.tx_range_m(src)
        for dst in strict_star.nodes:
            if src.name == dst.name:
                continue
            distance = math.hypot(pos[src.name][0]-pos[dst.name][0], pos[src.name][1]-pos[dst.name][1])
            if link_conditions(distance, strict_star.link_config, tx_range)["reachable"]:
                reach.append(dst.name)
        adjacency[src.name] = reach
    expected_leaves = {"CH-1", "SN-1", "SN-2", "SN-3", "SN-4"}
    check(set(adjacency["CH-2"]) == expected_leaves, "custom static_star center CH-2 reaches all five leaves")
    check(all(adjacency[name] == ["CH-2"] for name in expected_leaves), "custom static_star scenario is a true star connectivity graph")

    strict_line = Orchestrator().prepare(json.loads((scenario_root / "static_line.json").read_text()))
    pos = strict_line.trace[0]
    degrees = []
    for src in strict_line.nodes:
        tx_range = strict_line.link_config.tx_range_m(src)
        degree = 0
        for dst in strict_line.nodes:
            if src.name == dst.name:
                continue
            distance = math.hypot(pos[src.name][0]-pos[dst.name][0], pos[src.name][1]-pos[dst.name][1])
            degree += int(link_conditions(distance, strict_line.link_config, tx_range)["reachable"])
        degrees.append(degree)
    check(sorted(degrees) == [1, 1, 2, 2, 2, 2], "included static_line scenario is a true line/chain connectivity graph")

    ordered_names = [name for name, _ in sorted(strict_line.trace[0].items(), key=lambda item: item[1][0])]
    check(ordered_names == ["SN-5", "SN-4", "SN-3", "CH-1", "SN-2", "SN-1"], "line topology honors explicit node_order")

    manual = Orchestrator().prepare(json.loads((scenario_root / "static_manual.json").read_text()))
    check(manual.trace[0]["CH-1"] == [60.0, 150.0] and manual.trace[0]["SN-4"] == [150.0, 220.0], "manual topology honors exact user coordinates")


def test_mobility_swapping_and_independent_pauses():
    rwp = Orchestrator().prepare({
        "num_ch": 1, "num_sn": 2, "duration": 3.0, "seed": 810,
        "mobility": {
            "model": "random_waypoint", "tick_interval": 0.5,
            "area_width_m": 200.0, "area_height_m": 200.0,
            "ch_speed_range_mps": [2.0, 4.0], "sn_speed_range_mps": [0.5, 1.5],
            "pause_time_range": [0.5, 1.0],
        }
    })
    check(rwp.mobility_config.model == "random_waypoint", "Random Waypoint is selectable through scenario JSON")
    check(rwp.trace[0] != rwp.trace[max(rwp.trace)], "Random Waypoint produces movement")

    gm = Orchestrator().prepare({
        "num_ch": 2, "num_sn": 4, "duration": 20.0, "seed": 810,
        "mobility": {
            "model": "gauss_markov", "tick_interval": 0.5,
            "area_width_m": 200.0, "area_height_m": 200.0,
            "ch_mean_speed_mps": 4.0, "sn_mean_speed_mps": 1.0,
            "pause_trigger": "periodic", "move_time_range": [2.0, 4.0],
            "pause_time_range": [1.0, 2.0],
        }
    })
    stationary_ticks = {}
    for node in gm.nodes:
        ticks = set()
        for tick in range(1, len(gm.trace)):
            a, b = gm.trace[tick-1][node.name], gm.trace[tick][node.name]
            if math.hypot(a[0]-b[0], a[1]-b[1]) < 1e-12:
                ticks.add(tick)
        stationary_ticks[node.name] = ticks
    check(len({tuple(sorted(v)) for v in stationary_ticks.values()}) > 1, "Gauss-Markov node pause/stationary schedules are not synchronized")


def test_random_pauses():
    scenario = {
        "num_ch": 2, "num_sn": 4, "duration": 12.0, "seed": 810,
        "mobility": {
            "model": "gauss_markov", "tick_interval": 0.25,
            "area_width_m": 200.0, "area_height_m": 200.0,
            "ch_mean_speed_mps": 4.0, "sn_mean_speed_mps": 1.0,
            "pause_trigger": "random",
            "pause_probability_per_second": 0.65,
            "pause_time_range": [0.75, 1.5],
        },
    }
    plan = Orchestrator().prepare(scenario)
    check(plan.mobility_config.pause_trigger == "random", "random pause mode is selectable through scenario JSON")
    check(plan.mobility_config.move_time_range is None, "random pause mode does not require move_time_range")
    stationary = {}
    for node in plan.nodes:
        ticks = []
        for tick in range(1, len(plan.trace)):
            a, b = plan.trace[tick - 1][node.name], plan.trace[tick][node.name]
            if math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-12:
                ticks.append(tick)
        stationary[node.name] = ticks
    check(any(stationary.values()), "probabilistic pause mode produces actual stationary trace samples")
    check(len({tuple(v) for v in stationary.values()}) > 1, "probabilistic pauses are independently scheduled per node")

    # Reproducibility: same scenario/seed must give exactly the same pause/mobility trace.
    again = Orchestrator().prepare(scenario)
    check(plan.trace == again.trace, "random pause schedules are reproducible for the same scenario seed")

    # Configuration validation catches incomplete random-pause settings.
    try:
        MobilityConfig(
            pause_trigger="random", pause_time_range=(1.0, 2.0),
            pause_probability_per_second=None,
        ).validate()
    except ValueError:
        pass
    else:
        raise AssertionError("random pause mode accepted a missing probability")
    print("[PASS] random pause mode rejects a missing pause probability")


def test_per_node_ranges():
    plan = Orchestrator().prepare({
        "num_ch": 2, "num_sn": 2, "duration": 1.0,
        "link": {
            "range_mode": "explicit",
            "max_range_m": 70.0,
            "role_max_range_m": {"cluster_head": 100.0, "sensor_node": 60.0},
            "node_max_range_m": {"CH-2": 130.0, "SN-1": 40.0},
        },
        "mobility": {"model": "static", "initial_topology": "grid", "tick_interval": 0.5},
    })
    ranges = {n.name: plan.link_config.tx_range_m(n) for n in plan.nodes}
    check(ranges == {"CH-1": 100.0, "CH-2": 130.0, "SN-1": 40.0, "SN-2": 60.0}, "per-node and per-role TX range overrides resolve correctly")
    ch = plan.node_by_name("CH-1")
    sn = plan.node_by_name("SN-1")
    check(link_conditions(80.0, plan.link_config, plan.link_config.tx_range_m(ch))["reachable"], "80 m is reachable from a 100 m CH")
    check(not link_conditions(80.0, plan.link_config, plan.link_config.tx_range_m(sn))["reachable"], "80 m is unreachable from a 40 m SN")


def main():
    test_static_topologies()
    test_mobility_swapping_and_independent_pauses()
    test_random_pauses()
    test_per_node_ranges()
    print("\nCLIVE extension validation: PASS")


if __name__ == "__main__":
    main()
