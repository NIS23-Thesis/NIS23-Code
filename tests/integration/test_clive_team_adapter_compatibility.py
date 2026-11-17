"""CLIVE-only regressions against the team's unmodified FLAME implementation."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clive"))

from flame_protocol import FlameProtocol
from flame.communication.message import PacketType
from flame.federated_learning import (
    Aggregation, DatasetManager, DatasetStorage,
    FederatedLearningModule, ModelManager, Trainer,
)
from flame.federated_learning.model_manager import serialize_model_state
from flame.neighbor.neighbor_module import AssociationState, NodeRole

CH = ("127.27.0.2", 27062)
SN = ("127.27.0.4", 27062)


def adapter(*, update_id=0, aggregation_threshold=2):
    instance = object.__new__(FlameProtocol)
    instance.is_ch = True
    instance.model_id = 1
    instance.s = {"trainer": "mnist", "dataset_capacity": 512}
    instance.capacity_settings = {"dataset": 512}
    instance.stats = Counter()
    instance.node = SimpleNamespace(elapsed=lambda: 4.0, events=[], connection_events=[])
    instance.neighbors = SimpleNamespace(get_neighbor=lambda *args: SimpleNamespace(
        association_state=AssociationState.ASSOCIATED, node_role=NodeRole.CLUSTER_HEAD,
        model_id=1))
    instance._record_learning = Mock()
    instance._unique_observation_keys = set()
    instance._unique_model_keys = set()
    instance._useful_sensor_bytes = 0
    instance._useful_model_bytes = 0
    instance._received_digit_counts = [0] * 10
    instance._received_digit_unclassified = 0
    instance._cached_update = None
    instance._update_exclude = None
    dataset = DatasetManager(DatasetStorage(capacity=512))
    mm = ModelManager(1, model_state={"w": 1.0})
    mm._update_id = update_id  # Exhaustion is expensive to reach in a unit test.
    trained = Mock(return_value={"w": 2.0})
    trainer = Trainer(dataset, expected_observations=1,
                      training_threshold=1.0, train_function=trained)
    agg = Aggregation(mm, aggregation_threshold=aggregation_threshold)
    instance.fl = FederatedLearningModule(dataset, trainer, mm, agg)
    return instance, trained


def sensor(data_id=1):
    return {"packet_type": PacketType.SENSOR_DATA, "data_id": data_id,
            "sensor_type": 1, "sequence_number": data_id,
            "payload": bytes(784) + b"\x03", "source_address": SN}


def model(update_id=1, payload=None):
    return {"packet_type": PacketType.MODEL_UPDATE, "update_id": update_id,
            "model_id": 1, "sequence_number": update_id,
            "payload": payload if payload is not None
                      else serialize_model_state({"w": 3.0}),
            "source_address": CH}


def test_sensor_revision_limit_retains_observation_without_training_or_crashing():
    instance, train_fn = adapter(update_id=255)
    instance._handle_sensor_observation(sensor(), SN)
    assert instance.fl.dataset_manager.get_observation_count() == 1
    assert train_fn.call_count == 0
    assert instance.fl.model_manager.get_update_id() == 255
    assert instance.stats["unique_sensor_observations_received"] == 1
    assert instance.stats["model_revision_limit_reached"] == 1
    assert len([e for e in instance.node.events if
                e["type"] == "flame_model_revision_limit_reached"]) == 1
    # Another valid observation remains usable as evidence, with no event spam.
    instance._handle_sensor_observation(sensor(2), SN)
    assert instance.fl.dataset_manager.get_observation_count() == 2
    assert train_fn.call_count == 0
    assert instance.stats["model_revision_limit_reached"] == 1


def test_sensor_revision_254_can_still_reach_255_then_stops():
    instance, train_fn = adapter(update_id=254)
    instance._collect_model_update = Mock()  # transmission not relevant to training ceiling
    instance._handle_sensor_observation(sensor(), SN)
    assert instance.fl.model_manager.get_update_id() == 255
    assert train_fn.call_count == 1
    instance._handle_sensor_observation(sensor(2), SN)
    assert train_fn.call_count == 1
    assert instance.stats["model_revision_limit_reached"] == 1


def test_peer_revision_limit_rejects_without_buffering_or_crashing():
    instance, _ = adapter(update_id=255)
    instance._handle_peer_model(model(), CH)
    assert len(instance.fl.aggregation._buffer) == 0
    assert len(instance.fl.aggregation._seen_updates) == 0
    assert instance.fl.model_manager.get_update_id() == 255
    assert instance.stats["model_rejected_revision_limit"] == 1
    assert instance.stats["model_revision_limit_reached"] == 1


def test_team_flame_model_acceptance_and_duplicate_reporting():
    instance, _ = adapter(update_id=1)
    good = model()
    instance._handle_peer_model(good, CH)
    assert instance.stats["unique_model_updates_received"] == 1
    assert instance.stats["model_received"] == 1
    assert instance.stats["rejected_model_update_events"] == 0
    assert instance._useful_model_bytes == len(good["payload"])
    instance._handle_peer_model(good, CH)
    assert instance.stats["unique_model_updates_received"] == 1
    assert instance.stats["duplicate_model_update_events"] == 1
    assert instance.stats["model_received"] == 1


def test_team_flame_invalid_payload_is_not_counted_as_received_model():
    instance, _ = adapter(update_id=1)
    instance._handle_peer_model(model(payload=b"bad bytes"), CH)
    assert instance.stats["unique_model_updates_received"] == 0
    assert instance.stats["rejected_model_update_events"] == 1
    assert instance.stats["duplicate_model_update_events"] == 0
    assert not instance.fl.aggregation._seen_updates
