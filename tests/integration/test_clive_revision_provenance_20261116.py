"""CLIVE integration tests: revision digit snapshots and peer-origin display.

Retained from the original CLIVE regression tests while FLAME-specific
expectations are covered exclusively by the team's supplied test suite.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clive"))
from flame.federated_learning import ModelManager
from flame.federated_learning.dataset_storage import DatasetEntry
from flame_protocol import FlameProtocol
from visualizer import RunData


def fake_adapter(tmp_path):
    fp = object.__new__(FlameProtocol)
    fp._learning_events = 0
    fp.learning_history = None
    fp._received_digit_counts = [0] * 10
    fp._received_digit_unclassified = 0
    fp._last_training_digit_counts = [0] * 10
    fp._last_training_used_observation_keys = set()
    fp.s = {"learning_eval_every_updates": 1}
    fp.node = SimpleNamespace(
        results_dir=tmp_path, elapsed=lambda: 2.5,
        spec=SimpleNamespace(name="CH-2"),
        plan=SimpleNamespace(nodes=(SimpleNamespace(name="CH-1", ip="10.27.0.2", port=27062),)),
    )
    manager = ModelManager(1, model_state={"w": 0.5})
    manager.update_from_trainer({"w": 1.0})
    trainer = SimpleNamespace(_used_observations=set())
    samples = []
    dataset = SimpleNamespace(get_observation_count=lambda: len(samples), get_observations=lambda: list(samples))
    fp.fl = SimpleNamespace(model_manager=manager, trainer=trainer, dataset_manager=dataset)
    return fp, manager, trainer, samples


def read_revision_records(tmp_path):
    path=tmp_path / "flame" / "CH-2-learning.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def sample(digit, data_id):
    return DatasetEntry(data_id, 1, data_id, bytes(784)+bytes([digit]), ("10.27.0.4", 27062))


def test_training_digit_counts_are_snapshots_with_exact_consumed_inputs(tmp_path):
    fp, manager, trainer, samples = fake_adapter(tmp_path)
    samples.extend([sample(3, 1), sample(8, 2)])
    fp._received_digit_counts[3] = 1
    fp._received_digit_counts[8] = 1
    trainer._used_observations.update((s.source_address, s.data_id, s.sequence_number) for s in samples)
    fp._record_learning("local_training")
    rows=read_revision_records(tmp_path)
    assert rows[0]["measurement"] == "revision_event"
    assert rows[0]["digit_counts_before_training"][3] == 1
    assert rows[0]["digit_counts_before_training"][8] == 1
    assert rows[0]["digit_counts_in_training"][3] == 1
    assert rows[0]["training_input_observations"] == 2
    assert rows[0]["digit_counts_since_previous_training"][8] == 1

    samples.append(sample(5, 3)); fp._received_digit_counts[5] = 1
    trainer._used_observations.add((samples[-1].source_address, samples[-1].data_id, samples[-1].sequence_number))
    manager.update_from_trainer({"w": 1.5})
    fp._record_learning("local_training")
    rows=read_revision_records(tmp_path)
    assert len(rows)==2
    assert rows[1]["revision"] == 2
    assert rows[1]["digit_counts_before_training"][3] == 1
    assert rows[1]["digit_counts_in_training"] == [0,0,0,0,0,1,0,0,0,0]
    assert rows[1]["digit_counts_since_previous_training"] == [0,0,0,0,0,1,0,0,0,0]
    # Original snapshot is immutable, not a pointer to the lifetime counts.
    assert rows[0]["digit_counts_before_training"][5] == 0


def test_aggregation_revision_records_peer_without_fabricating_digits(tmp_path):
    fp, _, _, _ = fake_adapter(tmp_path)
    fp._record_learning("peer_aggregation", peer=("10.27.0.2",27062), peer_update_id=19)
    row=read_revision_records(tmp_path)[0]
    assert row["cause"] == "peer_aggregation"
    assert row["trigger_peer"] == "CH-1"
    assert row["trigger_update_id"] == 19
    assert row["received_model_update"] is True
    assert "digit_counts_before_training" not in row
    assert "digit_counts_in_training" not in row


def test_learning_api_and_page_show_revision_provenance(tmp_path):
    from orchestrator import Orchestrator
    run=tmp_path/"run-1"
    (run/"flame").mkdir(parents=True)
    plan=Orchestrator().prepare({"num_ch": 2,"num_sn": 1, "duration": 1.0, "seed": 8,
                                 "mobility": {"tick_interval": .5,"area_width_m": 100,"area_height_m": 100}})
    plan.save(str(run/"plan.json"))
    with (run/"flame"/"CH-1-learning.jsonl").open("w") as fh:
        fh.write(json.dumps({"measurement": "revision_event", "revision": 2,
                             "cause": "local_training", "time_s": .25, "node": "CH-1",
                             "digit_counts_before_training": [0,0,0,2,0,0,0,0,1,0]})+"\n")
        fh.write(json.dumps({"measurement": "revision_event", "revision": 3,
                             "cause": "peer_aggregation", "time_s": .5, "node": "CH-1",
                             "received_model_update": True, "trigger_peer": "CH-2", "trigger_update_id": 4})+"\n")
    result={n['name']:n for n in RunData(run).learning()['nodes']}
    assert len(result['CH-1']['points'])==2
    assert result['CH-1']['points'][0]['digit_counts_before_training'][3]==2
    assert result['CH-1']['points'][1]['trigger_peer']=='CH-2'
    html=(ROOT/'clive/visualizer/static/learning.html').read_text()
    js=(ROOT/'clive/visualizer/static/learning.js').read_text()
    assert '<th>Evidence</th>' in html
    assert 'function revisionEvidence(point)' in js
    assert 'Received MODEL_UPDATE' in js
    assert 'Used for this training' in js


def test_failed_optional_evaluator_falls_back_to_revision_evidence(tmp_path):
    fp, _, _, samples = fake_adapter(tmp_path)
    samples.append(sample(3, 1))
    fp._received_digit_counts[3] = 1
    fp.learning_history = SimpleNamespace(record=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('test measurement failed')))
    errors=[]
    fp._record=lambda event, **fields: errors.append((event, fields))
    fp._record_learning("local_training")
    row = read_revision_records(tmp_path)[0]
    assert row['measurement'] == 'revision_event'
    assert row['digit_counts_before_training'][3] == 1
    assert row['evaluation_error'] == 'test measurement failed'
    assert errors[0][0] == 'learning_evaluation_error'
