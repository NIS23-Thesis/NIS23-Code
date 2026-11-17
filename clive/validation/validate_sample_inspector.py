"""CLIVE-only training-image evidence regression; no FLAME source changes."""
from __future__ import annotations
import base64
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestrator import Orchestrator
from sample_history import SampleHistory, sample_digest
from visualizer import RunData


def check():
    with tempfile.TemporaryDirectory(prefix='clive-sample-') as folder:
        run = Path(folder) / 'run-samples'; run.mkdir()
        plan = Orchestrator().prepare({'num_ch': 2, 'num_sn': 2, 'duration': 10.0,
                                        'seed': 42, 'startup_delay_s': 0.2,
                                        'mobility': {'tick_interval': 0.5, 'area_width_m': 100,
                                                     'area_height_m': 100}})
        plan.save(str(run / 'plan.json'))
        addresses = {node.name: list(node.address) for node in plan.nodes}
        one = bytes([0, 30, 255, 19] * 196 + [3])
        two = bytes([100] * 784 + [7])
        src = SampleHistory(run, 'SN-1'); dst = SampleHistory(run, 'CH-1')
        src.record('collected', one, time_s=.1, sample_index=1, destination=addresses['CH-1'])
        src.record('collected', two, time_s=.2, sample_index=2, destination=addresses['CH-1'])
        dst.record('received', one, time_s=.3, source=addresses['SN-1'],
                   data_id=1, sequence_number=10, newly_stored=True)
        dst.record('trained', time_s=.31, revision=2, count=1,
                   samples=[{'sha256': sample_digest(one), 'source': addresses['SN-1']}])
        rd = RunData(run)
        received = rd.samples(node='CH-1', status='received')
        assert received['total'] == 1, received
        image = received['items'][0]
        assert image['digit'] == 3 and image['source'] == 'SN-1'
        assert len(base64.b64decode(image['pixels_b64'])) == 784
        assert base64.b64decode(image['pixels_b64']) + bytes([image['digit']]) == one
        assert image['receipts'][0]['sequence_number'] == 10
        trained = rd.samples(node='CH-1', status='trained', revision=2)
        assert trained['total'] == 1 and trained['items'][0]['used_in'] == [{'ch':'CH-1','revision':2}]
        not_confirmed = rd.samples(node='CH-1', status='unconfirmed')
        assert not_confirmed['total'] == 1 and not_confirmed['items'][0]['digit'] == 7
        page = rd.samples(node='all', status='all', offset=1, limit=1)
        assert page['total'] == 2 and len(page['items']) == 1
        older = Path(folder)/'run-older'; older.mkdir()
        plan.save(str(older / 'plan.json'))
        legacy=RunData(older).samples()
        assert legacy['total'] == 0 and legacy['capture_available'] is False
        try: rd.samples(node='../flame', status='all')
        except ValueError: pass
        else: raise AssertionError('unsafe node name accepted')
    print('PASS: exact raw MNIST images, collected/received distinction, training revision, paging, legacy runs')

if __name__=='__main__': check()
