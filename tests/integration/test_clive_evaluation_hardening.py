"""CLIVE-side acceptance checks. No modifications to FLAME's implementation."""
import json
import math
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clive"))

from emulator import Emulator
from flame_protocol import FlameProtocol, _configuration
from flame.communication.message import PacketType
from flame.neighbor.neighbor_module import AssociationState


def _plan(**flame):
    return SimpleNamespace(flame_settings=flame)


def test_config_rejects_impossible_mnist_batch_and_nonfinite():
    with pytest.raises(ValueError, match="cannot hold"):
        _configuration(_plan(trainer="mnist", expected_observations=10,
                             training_threshold=.8, dataset_capacity=7))
    assert _configuration(_plan(trainer="mnist", expected_observations=10,
                                training_threshold=.8, dataset_capacity=8))["dataset_capacity"] == 8
    # Non-MNIST transport-only capacity tests are not silently converted to training.
    assert _configuration(_plan(trainer="mean_pixel", expected_observations=1000,
                                dataset_capacity=1))["dataset_capacity"] == 1
    for invalid in (float("nan"), float("inf"), -1, True):
        with pytest.raises(ValueError):
            _configuration(_plan(training_threshold=invalid))
    with pytest.raises(ValueError, match="Unknown"):
        _configuration(_plan(nonexistent_option=123))


@pytest.mark.parametrize("payload", [b"0" * 784 + b"\xff", b"x" * 784, None,
                                      b"x" * 784 + b"\x00" + b"a"])
def test_malformed_mnist_is_rejected_before_flame_dataset(payload):
    protocol = object.__new__(FlameProtocol)
    protocol.s = {"trainer": "mnist", "dataset_capacity": 512}
    protocol.is_ch = True
    protocol.stats = Counter()
    protocol.node = SimpleNamespace(elapsed=lambda: 1.5, events=[], connection_events=[])
    neighbor = SimpleNamespace(association_state=AssociationState.ASSOCIATED)
    protocol.neighbors = SimpleNamespace(get_neighbor=lambda *args: neighbor)
    handler = Mock()
    protocol.fl = SimpleNamespace(handle_message=handler)
    packet = {"packet_type": PacketType.SENSOR_DATA, "payload": payload,
              "data_id": 42, "sensor_type": 1, "sequence_number": 12}
    protocol._handle_sensor_observation(packet, ("127.0.0.2", 27062))
    handler.assert_not_called()
    assert protocol.stats["invalid_mnist_sample_rejected"] == 1


def test_valid_mnist_counts_unique_bytes_without_changing_flame():
    protocol = object.__new__(FlameProtocol)
    protocol.s = {"trainer": "mnist", "dataset_capacity": 5}
    protocol.is_ch = True
    protocol.stats = Counter()
    protocol.node = SimpleNamespace(elapsed=lambda: 1.0, events=[], connection_events=[])
    neighbor = SimpleNamespace(association_state=AssociationState.ASSOCIATED)
    protocol.neighbors = SimpleNamespace(get_neighbor=lambda *args: neighbor)
    handler = Mock()
    protocol.fl = SimpleNamespace(handle_message=handler,
                                  model_manager=SimpleNamespace(get_update_id=lambda: 0))
    protocol._record_learning = Mock()
    protocol._unique_observation_keys = set()
    protocol._useful_sensor_bytes = 0
    protocol._received_digit_counts = [0] * 10
    protocol._received_digit_unclassified = 0
    packet = {"packet_type": PacketType.SENSOR_DATA, "payload": bytes(784) + b"\x04",
              "data_id": 42, "sensor_type": 1, "sequence_number": 12}
    for _ in range(2):
        protocol._handle_sensor_observation(packet, ("127.0.0.2", 27062))
    assert handler.call_count == 2  # No change to the existing FLAME behavior.
    assert protocol.stats["unique_sensor_observations_received"] == 1
    assert protocol.stats["duplicate_sensor_observation_events"] == 1
    assert protocol._useful_sensor_bytes == 785
    assert protocol._received_digit_counts[4] == 1  # Duplicate not double counted
    assert sum(protocol._received_digit_counts) == 1


def test_radio_delivery_counts_broadcast_fanout_without_pdr_over_100():
    row = {name: 0 for name in __import__("nodesocket").MetricsCollector.FIELDS}
    row.update(packets_sent=1, packets_received=3, delivery_attempts=3,
               kernel_datagrams_sent=3, delivery_drops=0)
    m = Emulator._aggregate_metrics({"CH-1": {"success": True, **row}})
    assert m["totals"]["packets_received"] > m["totals"]["packets_sent"]
    assert m["recipient_delivery"]["recipient_handoff_ratio"] == 1
    assert m["recipient_delivery"]["recipient_loss_ratio"] == 0
    assert m["recipient_delivery"]["unresolved_recipient_attempts"] == 0


def test_transfer_cutoff_separates_failed_from_censored_and_counts_samples():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "flame"; p.mkdir()
        (p / "CH-1.json").write_text(json.dumps({
            "node": "CH-1", "role": "CH", "workload": {"trainer": "mnist", "real_neural_network_training": True},
            "counters": {"trained": 2}, "observations_retained": 3,
            "observations_evicted_at_least": 4,
            "application_delivery": {"unique_sensor_observations_received": 7,
                                     "unique_sensor_payload_bytes_received": 7 * 785,
                                     "unique_model_updates_received": 2,
                                     "unique_model_payload_bytes_received": 3000},
            "model_transfer_summary": {"started_versions": 4, "completed_versions": 2,
                                       "failed_versions": 1, "unfinished_versions": 1}}))
        (p / "SN-1.json").write_text(json.dumps({
            "node": "SN-1", "role": "SN", "samples_generated": 8,
            "offline_buffer_drops": 1, "sensing_buffer_drops": 1}))
        em = object.__new__(Emulator)
        em.run_dir = td
        outcome = em._flame_outcome()
        assert outcome["model_transfers_censored_at_cutoff"] == 1
        assert outcome["model_transfers_failed"] == 1
        assert outcome["model_transfer_resolution"]["confirmed_completion_fraction_of_resolved"] == 2/3
        assert outcome["application_delivery"]["unique_useful_payload_bytes_received"] == 7*785+3000
        assert outcome["sensor_observation_accounting"]["generated"] == 8
        assert outcome["sensor_observation_accounting"]["evicted_at_least"] == 4


def test_adapter_leave_uses_actual_flame_serialization_field():
    from flame.communication.parser import serialize_message
    from flame.communication.message import deserialize_leave

    adapter = object.__new__(FlameProtocol)
    adapter._next_leave = 0.5
    adapter._next_sample = 0.5
    adapter._left = False
    adapter._joined_ch = ("127.0.0.2", 27062)
    adapter._is_joined = lambda: True
    adapter._record = Mock()
    transmitted = []
    adapter.comms = SimpleNamespace(send_message=lambda message, destination: transmitted.append(message))
    adapter._maybe_leave(1)
    assert adapter._left and adapter._next_sample is None
    assert transmitted[0]["reason"] == 0
    assert "leave_reason" not in transmitted[0]
    # Regression for the original typo: a nonzero LEAVE reason must survive the wire.
    sample = {**transmitted[0], "reason": 7}
    decoded = deserialize_leave(serialize_message(sample))
    assert decoded["reason"] == 7
