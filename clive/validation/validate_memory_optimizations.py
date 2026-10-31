#!/usr/bin/env python3
"""Verify memory optimizations preserve MNIST sampling and radio behavior."""
from __future__ import annotations

import gc
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))

from flame_protocol import _MNISTSource, _stable_seed
from mnist_cache import prepare_mnist_cache
from orchestrator import EmulationConfig


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print('[PASS]', message)


def main():
    # Subprocess is needed: the validator itself imports NumPy to verify parity.
    source = '''import sys;sys.path.insert(0, sys.argv[1]);import flame_protocol;assert "torch" not in sys.modules;print("OK")'''
    p = subprocess.run([sys.executable, '-c', source, str(ROOT)], capture_output=True, text=True)
    check(p.returncode == 0 and p.stdout.strip() == 'OK',
          'FLAME adapter import does not load torch in sensors')

    import numpy as np
    dataset = PROJECT / 'mnist' / 'mnist.npz'
    cache = prepare_mnist_cache(dataset)
    check(all((cache / f'digit-{i}.bin').is_file() for i in range(10)),
          'Shared MNIST class cache is complete')
    with np.load(dataset) as archive:
        images = archive['x_train']
        labels = archive['y_train']
        for sensor_num in (1, 3, 6, 10, 31):
            name = f'SN-{sensor_num}'
            digit = (sensor_num - 1) % 10
            rng = random.Random(_stable_seed(810, name))
            indexes = np.flatnonzero(labels == digit)
            picks = [int(indexes[rng.randrange(len(indexes))]) for _ in range(128)]
            expected = [images[index].tobytes() + bytes([digit]) for index in picks]
            actual = _MNISTSource(name, 810, {'mnist_samples_per_sensor':128, 'sample_source':'mnist'})
            check(actual.samples == expected and [actual.collect() for _ in range(128)] == expected,
                  f'MNIST samples and exact RNG selection unchanged for {name}')

    check(EmulationConfig.from_dict({'radio_execution':'thread'}).radio_execution == 'thread',
          'Compact radio execution is accepted by runtime schema')
    try:
        EmulationConfig.from_dict({'radio_execution':'invalid'})
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid radio execution backend silently accepted')

    tracemalloc.start()
    gc.collect()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(3):
        sources = [
            _MNISTSource(f'SN-{n}', 810, {'mnist_samples_per_sensor':128,'sample_source':'mnist'})
            for n in range(1, 101)
        ]
        check(all(len(s.samples)==128 for s in sources), '100 sensor samplers load compact sample lists')
        del sources
        gc.collect()
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    check(after-before < 2_000_000,
          f'Repeated sensor creation releases retained sample memory ({after-before} B)')

    with tempfile.TemporaryDirectory(prefix='clive-memory-thread-validation-') as folder:
        with (ROOT / 'configs/demos/flame_smoke.json').open() as f:
            scenario=json.load(f)
        scenario['runtime']['radio_execution']='thread'
        path=Path(folder)/'scenario.json';path.write_text(json.dumps(scenario))
        run=subprocess.run([sys.executable,str(ROOT/'clive.py'),'--scenario',str(path),
                            '--results',str(Path(folder)/'results')],
                           cwd=ROOT,capture_output=True,text=True,timeout=90)
        check(run.returncode==0,f'Compact radio end-to-end smoke succeeds: {run.stderr[-400:]}')
        result=Path(run.stdout.strip().splitlines()[-1]);metrics=json.loads((result/'metrics.json').read_text())
        check(metrics['success'] and metrics['totals']['packets_received']>0,
              'Compact radio preserves real packet handling')
        heads=[json.loads((result/'flame'/f'CH-{i}.json').read_text()) for i in (1,2)]
        check(sum(h['counters'].get('trained',0) for h in heads)>0 and
              sum(h['counters'].get('aggregated',0) for h in heads)>0,
              'Compact radio preserves FLAME training and aggregation path')
    print('MEMORY OPTIMIZATION REGRESSIONS: PASS')

if __name__=='__main__':
    main()
