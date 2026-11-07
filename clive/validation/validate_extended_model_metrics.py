"""Regression coverage for CLIVE-only classification metrics, confidence and playback."""
from __future__ import annotations
import math
import sys
import tempfile
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent))
from classification_metrics import classification_metrics

# On this data, one of two true digit-1 examples is misclassified as digit 0.
result=classification_metrics([0, 1, 1, 2], [0, 0, 1, 2])
assert math.isclose(result['accuracy'], .75)
assert math.isclose(result['per_class'][1]['recall'], .5)
assert math.isclose(result['per_class'][0]['precision'], .5)
assert len(result['per_class']) == 10
assert len(result['confusion_matrix'])==10
assert result['weighted_f1']>=0
try:
    classification_metrics([0],[10])
except ValueError: pass
else: raise AssertionError('Expected range validation for MNIST digits')

# Real MNIST checkpoint: observational inference does not change source state.
import numpy as np
from flame.federated_learning.mnist_trainer import MNISTMLP
from learning_history import LearningHistory
mnist=ROOT.parent/'mnist'/'mnist.npz'
assert mnist.exists(),f'Missing {mnist}'
with tempfile.TemporaryDirectory() as temp:
    recorder=LearningHistory(mnist,Path(temp)/'history.jsonl',seed=810,samples=64)
    model=MNISTMLP()
    state=model.state_dict()
    original={k:v.detach().clone() for k,v in state.items()}
    entry=recorder.record(state,node='CH-1',time_s=.000041,revision=1,cause='local_training',observations=3)
    assert entry['time_s']==.000041
    assert 0<=entry['accuracy']<=1
    assert all(0<=entry[k]<=1 for k in ['macro_precision','macro_recall','macro_f1','weighted_f1'])
    assert len(entry['example_confidence'])==10
    assert len(entry['per_class'])==10
    for i,example in enumerate(entry['example_confidence']):
        assert example['true_digit']==i
        assert len(example['confidence'])==10
        assert abs(sum(example['confidence'])-1)<.0001
    assert all((original[k]==state[k]).all().item() for k in state)
    assert json.loads((Path(temp)/'history.jsonl').read_text())['accuracy']==entry['accuracy']

html=(ROOT/'visualizer/static/index.html').read_text()
js=(ROOT/'visualizer/static/app.js').read_text()
learning=(ROOT/'visualizer/static/learning.html').read_text()
assert 'id="guidedPlayback"> Guided steps' in html and 'checked' not in html.split('id="guidedPlayback"',1)[1].split('</label>',1)[0]
assert 'guidedPlayback: false' in js and '.step = "0.000001"' in js
assert 'id="showModelFlow"' in html and 'id="showDirectionArrows"' in html
assert 'id="metricSelect"' in learning and 'id="exampleDigit"' in learning
print('CLIVE extended model metrics and precision controls: PASS')
