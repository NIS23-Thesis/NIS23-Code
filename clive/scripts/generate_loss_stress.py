#!/usr/bin/env python3
"""Build controlled 300 s real-MNIST loss experiments; generation is NOT execution.

CH-to-CH distance sets nominal NodeSocket RSSI PLR (0/2/10 percent).
Nearby sensors remain connected so the primary stress is model transport.
All scenarios use the same node population, model workload, and duration.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodesocket import link_conditions
from orchestrator import LinkConfig

BASE = ROOT / 'configs' / 'demos' / 'panel-05-static-loss-real-mnist-2ch-4sn.json'
PROFILES = {"loss-00-reference": 10.0, "loss-02-moderate": 20.0, "loss-10-severe": 40.0}


def build_scenarios(output: Path, *, duration_s: float = 300.0, seed: int = 810):
    if duration_s < 300:
        raise ValueError("Full stress evaluation must be at least 300 seconds; use a diagnostic override in the runner")
    base = json.loads(BASE.read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, separation in PROFILES.items():
        c = copy.deepcopy(base)
        c['runtime'].update(duration_s=float(duration_s), seed=seed,
                            startup_delay_s=5.0, readiness_timeout_s=180.0,
                            start_tolerance_s=5.0, radio_execution='thread')
        c['mobility']['manual_positions'] = {
            'CH-1': [30, 50], 'CH-2': [30 + separation, 50],
            'SN-1': [29, 50], 'SN-2': [30, 51],
            'SN-3': [31 + separation, 50], 'SN-4': [30 + separation, 51],
        }
        c['mobility']['area_width_m'] = 120
        c['mobility']['area_height_m'] = 120
        c['logging'] = {'protocol_events': True, 'packet_events': False, 'mobility_tsv': False}
        c['flame'].update(sample_interval_s=15.0, mnist_samples_per_sensor=64,
                          track_learning=False, evaluate_accuracy=True, save_final_model=True)
        conditions = link_conditions(separation, LinkConfig())
        # Check the modeled condition instead of assuming 20m always means 2%.
        expected = {10.0: 0.0, 20.0: 0.02, 40.0: 0.10}[separation]
        assert conditions['plr'] == expected and conditions['reachable'], (name, conditions)
        (output / (name + '.json')).write_text(json.dumps(c, indent=2) + '\n', encoding='utf-8')
        rows.append({'scenario': name + '.json', 'distance_m': separation,
                     'modeled_link_plr': expected, 'duration_s': duration_s,
                     'seed': seed, 'data': 'real MNIST',
                     'disclaimer': 'Modeled loss probability; observed losses and FLAME transfer outcomes require execution.'})
    manifest = {'status': 'configured_not_executed', 'description': 'Controlled sustained-loss evaluation for FLAME under CLIVE NodeSocket',
                'scenarios': rows, 'caveats': [
                    'The 0/2/10% values are RSSI mapping probabilities, not measured outcomes.',
                    'Protocol transfer reliability may fail; do not count process exit as protocol success.',
                    'FLAME source is not altered by this generator.',
                    'Loss profiles alter CH-to-CH distance; sensor links are deliberately nearby.',
                    'Run at least three distinct random seeds before statistical comparisons.',
                ]}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT / 'configs' / 'evaluations' / 'loss_stress')
    p.add_argument('--duration', type=float, default=300.0)
    p.add_argument('--seed', type=int, default=810)
    args = p.parse_args()
    result = build_scenarios(args.output, duration_s=args.duration, seed=args.seed)
    print(f"Generated {len(result['scenarios'])} sustained-loss scenarios (NOT EXECUTED)")


if __name__ == '__main__':
    main()
