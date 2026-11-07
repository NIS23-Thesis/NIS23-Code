"""Verify that historical playback events survive raw-packet cache eviction.

CLIVE only. Does not import or modify FLAME runtime.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestrator import Orchestrator
from visualizer import RunData


def main():
    with tempfile.TemporaryDirectory(prefix='clive-history-window-') as tmp:
        run_dir = Path(tmp) / 'run-history'
        run_dir.mkdir()
        plan = Orchestrator().prepare({
            'num_ch': 2, 'num_sn': 1, 'duration': 15.0, 'seed': 810,
            'startup_delay_s': 0.2,
            'mobility': {'tick_interval': 0.5, 'area_width_m': 100, 'area_height_m': 100},
        })
        plan.save(str(run_dir / 'plan.json'))
        packets = run_dir / 'debug/packets'
        protocols = run_dir / 'debug/events'
        packets.mkdir(parents=True)
        protocols.mkdir(parents=True)
        with (packets / 'CH-1.jsonl').open('w') as f:
            for i in range(3000):
                json.dump({'event': 'transmit', 'node_name': 'CH-1',
                           'time_s': 0.001 + i * 0.003, 'common_header':
                           {'packet_type': 4, 'sequence_number': i % 65536}}, f)
                f.write('\n')
        with (protocols / 'CH-1.jsonl').open('w') as f:
            for row in [
                {'type':'flame_joined','time_s':0.25,'node_name':'CH-1'},
                {'type':'flame_trained','time_s':2.15,'node_name':'CH-1'},
                {'type':'flame_transfer_complete','time_s':3.21,'node_name':'CH-1'},
            ]:
                f.write(json.dumps(row)+'\n')
        data = RunData(run_dir, max_events=30)
        # Fill/evict the live cache, as high-volume panel demos do.
        data.events()
        assert len(data._events) == 30
        result = data.events(from_s=0, to_s=3.5, category='all', limit=160)
        assert result['matching_count'] > 1100, result
        assert len(result['events']) == 160
        assert result['hidden_count'] > 0
        assert result['events'][-1]['time_s'] <= 3.5
        key = data.events(from_s=0, to_s=3.5, category='key', limit=160)
        assert key['matching_count'] == 3, key
        assert [e['event'] for e in key['events']] == [
            'flame_joined', 'flame_trained', 'flame_transfer_complete']
        filtered = data.events(from_s=0, to_s=3.5, category='all', search='trained')
        assert filtered['matching_count']==1 and filtered['events'][0]['event']=='flame_trained'
        # Logs can grow during an active run; the index must discover appended records.
        with (protocols / 'CH-1.jsonl').open('a') as f:
            f.write(json.dumps({'type':'flame_aggregated','time_s':6.4,'node_name':'CH-1'})+'\n')
        after = data.events(from_s=5, to_s=7, category='key')
        assert after['matching_count'] == 1 and after['events'][0]['event']=='flame_aggregated'
        print('CLIVE history window/index regression PASS')

if __name__ == '__main__':
    main()
