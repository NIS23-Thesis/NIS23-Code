"""CH received-label provenance: neither prediction frequencies nor SN assignments."""
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clive"))

from flame_protocol import FlameProtocol
from visualizer import RunData
from flame.communication.message import PacketType
from flame.neighbor.neighbor_module import AssociationState


def _ch():
    p = object.__new__(FlameProtocol)
    p.s = {"trainer": "mnist", "dataset_capacity": 2}
    p.is_ch = True
    p.stats = Counter()
    p.node = SimpleNamespace(elapsed=lambda: 1.0, events=[], connection_events=[])
    neighbor = SimpleNamespace(association_state=AssociationState.ASSOCIATED)
    p.neighbors = SimpleNamespace(get_neighbor=lambda *args: neighbor)
    p.fl = SimpleNamespace(handle_message=Mock(),
                           model_manager=SimpleNamespace(get_update_id=lambda: 0))
    p._record_learning = Mock()
    p._unique_observation_keys = set()
    p._useful_sensor_bytes = 0
    p._received_digit_counts = [0] * 10
    p._received_digit_unclassified = 0
    return p


def _packet(digit, seq, data_id):
    return {"packet_type": PacketType.SENSOR_DATA, "payload": bytes(784) + bytes([digit]),
            "data_id": data_id, "sensor_type": 1, "sequence_number": seq}


def test_ch_distribution_counts_unique_accepted_labels_and_survives_dataset_evictions():
    p = _ch()
    peer = ("127.0.0.2", 27062)
    for digit,seq in [(3,1),(3,2),(8,3)]:
        p._handle_sensor_observation(_packet(digit,seq,seq),peer)
    # Simulate replay of a fully reassembled message; not a fresh observation.
    p._handle_sensor_observation(_packet(3,1,1),peer)
    assert p._received_digit_counts == [0,0,0,2,0,0,0,0,1,0]
    assert p.stats["unique_sensor_observations_received"] == 3
    assert p.stats["duplicate_sensor_observation_events"] == 1
    # Dataset capacity is 2, yet lifetime counts retain all three observations.
    assert p.stats["observations_evicted_at_least"] == 1
    assert sum(p._received_digit_counts) + p._received_digit_unclassified == 3


def test_invalid_digit_not_counted():
    p = _ch()
    p._handle_sensor_observation(_packet(255,1,1),("127.0.0.2",27062))
    assert p.stats["invalid_mnist_sample_rejected"] == 1
    assert p.stats["unique_sensor_observations_received"] == 0
    assert sum(p._received_digit_counts) == 0
    p.fl.handle_message.assert_not_called()


def test_learning_api_reads_distribution_from_ch_result_without_guessing(tmp_path):
    run = tmp_path / "run-1"
    (run / "flame").mkdir(parents=True)
    from orchestrator import Orchestrator
    plan = Orchestrator().prepare({"num_ch":2,"num_sn":1,"duration":1.0,"seed":810,
                                    "mobility":{"tick_interval":0.5,"area_width_m":100,
                                                "area_height_m":100}})
    plan.save(str(run / "plan.json"))
    (run / "flame" / "CH-1.json").write_text(json.dumps({
        "received_digit_distribution":{"counts":[0,1,0,2,0,0,0,0,3,0],
                                       "unclassified":0,"total_unique_received":6}}))
    (run / "flame" / "CH-2.json").write_text(json.dumps({}))
    nodes={n["name"]:n for n in RunData(run).learning()["nodes"]}
    assert nodes["CH-1"]["received_digit_distribution"]["counts"][8] == 3
    assert nodes["CH-2"]["received_digit_distribution"] is None  # historical run


def test_ui_has_distribution_panel_explanation_and_no_fabricated_legacy_counts():
    html=(ROOT/"clive"/"visualizer"/"static"/"learning.html").read_text()
    js=(ROOT/"clive"/"visualizer"/"static"/"learning.js").read_text()
    css=(ROOT/"clive"/"visualizer"/"static"/"learning-dashboard.css").read_text()
    assert 'id="receivedDigitRows"' in html
    assert 'help-learning-distribution' in html
    assert 'function renderReceivedDigits(node)' in js
    assert 'Not recorded for this run' in js
    assert '.learning-distribution-panel' in css
