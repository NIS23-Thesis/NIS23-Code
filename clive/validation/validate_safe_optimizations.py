"""Regression guards for read-only CLIVE visualizer optimizations.

This test intentionally runs without editing or importing FLAME internals.
It checks completeness and exact packet ambiguity counts, not just speed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator import Orchestrator
from packet_trace import build_traces
from visualizer import RunData


def packet_event(kind, at, packet_id, *, recipient='CH-2', digest='same'):
    return {
        'event': kind, 'time_s': at,
        'node': 'CH-1' if kind != 'receive' else recipient,
        'source': 'CH-1', 'target': recipient,
        'stream': 'packet', 'packet_type': 4,
        'sequence_number': 7, 'packet_id': packet_id,
        'payload_fingerprint': digest, 'is_broadcast': False,
        'archive_id': f'{kind}/{at}/{packet_id}', 'reason': None,
    }


def test_packet_correlation():
    tx = [packet_event('transmit', 0.001 * i, i) for i in range(3)]
    rx = packet_event('receive', 0.02, None)
    result = build_traces(tx + [rx], 0, 0.1)
    ambiguous = [packet for packet in result['packets'] if packet['status'] == 'ambiguous']
    assert result['total'] == 4 and len(ambiguous) == 1, result
    assert ambiguous[0]['possible_match_count'] == 3, result
    assert result['ambiguous_receives'] == 1
    # A byte-level match must distinguish repeated sequence numbers.
    unique = [packet_event('transmit', .04 + i * .001, i + 10, digest=f'unique-{i}')
              for i in range(5)]
    observed = packet_event('receive', .05, None, digest='unique-3')
    exact = build_traces(unique + [observed], 0, .2)
    assert exact['orphan_receives'] == 0 and sum(p['receiver_count'] for p in exact['packets']) == 1
    assert build_traces(unique + [observed], 0, .2, offset=2, limit=2)['total'] == 5


def test_live_log_tail_and_metrics_cache():
    with tempfile.TemporaryDirectory(prefix='clive-safe-optimization-') as directory:
        root = Path(directory) / 'run'
        root.mkdir()
        plan = Orchestrator().prepare({
            'num_ch': 2, 'num_sn': 1, 'duration': 4., 'seed': 810,
            'startup_delay_s': .1,
            'mobility': {'tick_interval': .5, 'area_width_m': 60.,
                         'area_height_m': 60.},
        })
        plan.save(str(root / 'plan.json'))
        log = root / 'debug' / 'packets' / 'CH-1.jsonl'
        log.parent.mkdir(parents=True)
        base = {'event': 'transmit', 'node_name': 'CH-1', 'time_s': .5,
                'counters': {'packets_sent': 1},
                'common_header': {'packet_type': 0, 'sequence_number': 22}}
        lines = [dict(base, time_s=.01 * (i + 1)) for i in range(255)]
        complete = b''.join((json.dumps(row) + '\n').encode() for row in lines)
        trailing = json.dumps(dict(base, time_s=3.05)).encode()
        with log.open('wb') as out:
            out.write(complete + trailing[:len(trailing)//2])
        run = RunData(root)
        initial = run.events(from_s=0, to_s=4, limit=1000, category='network')
        assert initial['matching_count'] == 255, initial['matching_count']
        assert run._window_index[str(log)]['offset'] == len(complete), 'partial line was skipped'
        before = run.metrics()
        assert before['totals']['packets_sent'] == 255
        assert run.metrics() == before, 'repeat metrics changed without any new records'
        # Appending the remainder of a partially written line must make it visible.
        with log.open('ab') as out:
            out.write(trailing[len(trailing)//2:] + b'\n')
        completed = run.events(from_s=0, to_s=4, limit=1000, category='network')
        assert completed['matching_count'] == 256, completed['matching_count']
        after = run.metrics()
        assert after['totals']['packets_sent'] == 256
        assert run.metrics() == after
        assert run.events(from_s=3, to_s=4, limit=1000, category='network')['matching_count'] == 1
        # Truncation/rotation must invalidate the old sparse on-disk index.
        log.write_text(json.dumps(dict(base, time_s=.4)) + '\n')
        rotated = run.events(from_s=0, to_s=4, limit=1000, category='network')
        assert rotated['matching_count'] == 1, rotated['matching_count']


def main():
    test_packet_correlation()
    test_live_log_tail_and_metrics_cache()
    print('CLIVE safe optimization regressions PASS')


if __name__ == '__main__':
    main()
