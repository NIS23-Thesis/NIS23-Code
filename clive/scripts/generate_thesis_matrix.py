#!/usr/bin/env python3
"""Generate all 108 Chapter 4.7 factorial test cases (CLIVE/integration only).

4 mobility x 3 density x 3 frequency x 3 area = 108 scenarios per seed.
Generation is cheap; --run is intentionally NOT supported here. Use clive.py on
an individual generated JSON after reviewing resource requirements, or run a
batch with your experiment scheduler. No test result is fabricated.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

MOBILITY = {
    "none": (0.0, 0.0, None),
    "low": (4.0, 1.0, (15.0, 25.0)),
    "medium": (6.0, 2.0, (5.0, 15.0)),
    "high": (12.0, 4.0, (1.0, 5.0)),
}
DENSITY = {"low": (4, 20), "medium": (7, 50), "high": (10, 100)}
FREQUENCY = {"low": 60, "medium": 30, "high": 15}
AREA = {"small": 200, "medium": 350, "large": 500}


def make_scenario(mobility, density, frequency, area, *, seed=810, duration_s=300):
    if duration_s < 300:
        raise ValueError("Thesis Table 4-6 requires at least 300 seconds")
    ch_speed, sn_speed, pauses = MOBILITY[mobility]
    num_ch, num_sn = DENSITY[density]
    size = AREA[area]
    movement = {
        "model": "gauss_markov", "initial_topology": "random", "tick_interval": 0.5,
        "alpha": 0.5, "area_width_m": size, "area_height_m": size,
        "ch_mean_speed_mps": ch_speed, "sn_mean_speed_mps": sn_speed,
        "speed_stddev": 0.0 if pauses is None else 1.0,
        "direction_stddev": 0.5,
    }
    if pauses is None:
        movement["pause_trigger"] = "zero_speed"
    else:
        movement.update(pause_trigger="periodic", pause_time_range=list(pauses),
                        move_time_range=[10.0, 30.0])
    return {
        "num_ch": num_ch, "num_sn": num_sn,
        "runtime": {"duration_s": float(duration_s), "seed": seed,
                    "startup_delay_s": 10.0, "readiness_timeout_s": 180.0,
                    "start_tolerance_s": 5.0, "radio_execution": "thread"},
        "mobility": movement,
        "link": {"range_mode": "rssi", "pathloss_exponent": 3.0,
                 "rssi0_dbm": -30.0, "d0_m": 1.0, "max_data_rate_bps": 1_000_000_000.0},
        "logging": {"protocol_events": False, "packet_events": False, "mobility_tsv": False},
        "flame": {"sample_source": "mnist", "trainer": "mnist",
                  "mnist_distribution": "spatial_clusters", "repeat_sensor_samples": False,
                  "sample_interval_s": float(FREQUENCY[frequency]),
                  "evaluate_accuracy": True, "save_final_model": True},
    }


def generate(output_dir, *, seed=810, duration_s=300):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for mobility, density, frequency, area in itertools.product(MOBILITY, DENSITY, FREQUENCY, AREA):
        case_id = f"m-{mobility}_d-{density}_f-{frequency}_a-{area}"
        scenario = make_scenario(mobility, density, frequency, area, seed=seed,
                                 duration_s=duration_s)
        filename = case_id + ".json"
        (output / filename).write_text(json.dumps(scenario, indent=2) + "\n")
        entries.append({"case_id": case_id, "scenario_file": filename,
                        "mobility": mobility, "density": density,
                        "frequency": frequency, "area": area, "seed": seed})
    manifest = {
        "source": "Thesis November 4, 2026 Chapter 4.7, Tables 4-6 through 4-10",
        "cases": len(entries), "seeds": [seed], "run_status": "generated_not_executed",
        "caveats": [
            "Every case loads actual MNIST and runs PyTorch, potentially using substantial RAM.",
            "Pause initiation [10,30] s and GM alpha=.5 are emulation assumptions, not prescribed by the thesis.",
            "Class assignment is a fixed ten-region spatial clustering approximation.",
            "Actual FLAME reliability defects are outside the scope of CLIVE adapter repairs.",
        ],
        "scenarios": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "configs" / "evaluations" / "thesis_matrix"))
    p.add_argument("--seed", type=int, default=810)
    p.add_argument("--duration", type=float, default=300.0)
    args = p.parse_args()
    result = generate(args.output, seed=args.seed, duration_s=args.duration)
    print(f"Wrote {result['cases']} Chapter 4.7 scenarios to {args.output} (none executed)")


if __name__ == "__main__":
    main()
