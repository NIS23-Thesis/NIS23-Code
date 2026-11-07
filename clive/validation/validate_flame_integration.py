#!/usr/bin/env python3
"""End-to-end acceptance test: genuine FLAME traffic over CLIVE v2's NodeSocket.

This smoke case deliberately uses a tiny deterministic model and wire-compatible
synthetic sensor payload to exercise the whole protocol quickly. Run the separate
flame_mnist_small scenario for real PyTorch/MNIST training and model fragments.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print('[PASS]', message)


def main():
    with tempfile.TemporaryDirectory(prefix='clive-flame-integration-') as folder:
        p = subprocess.run([
            sys.executable, str(ROOT / 'clive.py'),
            '--scenario', str(ROOT / 'configs/demos/flame-synthetic-transport-and-aggregation-smoke.json'),
            '--results', str(Path(folder) / 'results'),
        ], cwd=ROOT, capture_output=True, text=True, timeout=70)
        if p.returncode:
            raise RuntimeError(f'CLIVE exited with code {p.returncode}:\n{p.stdout}\n{p.stderr}')
        run = Path(p.stdout.strip().splitlines()[-1])
        require(run.is_dir(), 'CLIVE emitted a complete emulation run directory')
        metrics = json.loads((run / 'metrics.json').read_text())
        require(metrics['success'], 'All 6 CLIVE nodes and their radio child processes finished successfully')
        by_type = metrics['by_packet_type']
        for code, label in [('0', 'HELLO'), ('6', 'JOIN'), ('7', 'JOIN_ACCEPT'),
                            ('2', 'SENSOR_DATA'), ('3', 'SENSOR_DATA_ACK'),
                            ('4', 'MODEL_UPDATE'), ('5', 'MODEL_UPDATE_ACK'),
                            ('1', 'LEAVE')]:
            require(by_type.get(code, {}).get('packets_received', 0) > 0,
                    f'Emulated NodeSocket delivered real FLAME {label} packets')
        chs = [json.loads((run/'flame'/f'CH-{i}.json').read_text()) for i in (1, 2)]
        sns = [json.loads((run/'flame'/f'SN-{i}.json').read_text()) for i in range(1, 5)]
        require(all(sn['counters'].get('joined', 0) for sn in sns), 'All sensors joined a cluster')
        require(sum(ch['counters'].get('sensor_received', 0) for ch in chs) > 0,
                'Cluster Heads reassembled and consumed sensor observations')
        require(sum(ch['counters'].get('trained', 0) for ch in chs) > 0,
                'Training triggered FLAME model dissemination')
        require(sum(ch['counters'].get('aggregated', 0) for ch in chs) > 0,
                'Cluster Heads received and aggregated complete model updates')
        require(all(not ch['counters'].get('datagram_error', 0) for ch in chs),
                'No FLAME parse errors at Cluster Heads')
        print('CLIVE v2 + FLAME end-to-end acceptance: PASS')


if __name__ == '__main__':
    main()
