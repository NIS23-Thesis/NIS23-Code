"""Run the *validation-repository FLAME implementation* over CLIVE v2 NodeSocket.

The adapter owns the protocol's application clock but NOT another socket or worker
thread.  CommunicationModule is driven by CLIVE's on_datagram()/on_timer() hooks;
CLIVE therefore applies mobility, loss, bandwidth, and packet-delay variation to
all FLAME control, data, fragment and ACK datagrams.
"""
from __future__ import annotations

from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from typing import Any

# clive.py is commonly launched from clive/, where the sibling flame/ package
# otherwise is not importable by the spawned worker processes.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol_interface import ProtocolInterface
from nodesocket import BROADCAST_ADDRESS
from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType
from flame.initialization.config import NodeConfig
from flame.initialization.initialization_module import InitializationModule
from flame.neighbor.neighbor_module import AssociationState, NeighborModule, NodeRole
from flame.sensing import DataAcquisition, SensingModule
from flame.federated_learning import (
    Aggregation, DatasetManager, DatasetStorage, FederatedLearningModule,
    ModelManager, Trainer,
)


DEFAULTS = {
    "model_id": 1,
    "hello_interval_s": 5.0,
    "sample_interval_s": 30.0,
    "service_interval_s": 0.02,
    "join_retry_s": 1.0,
    "neighbor_timeout_s": 30.0,
    "max_offline_samples": 64,
    "sample_source": "mnist",        # real 785-byte MNIST sample, or synthetic for a smoke test
    "mnist_samples_per_sensor": 128,
    "trainer": "mnist",             # real WAFL-MLP; mean_pixel ONLY for fast transport tests
    "local_epochs": 1,
    "batch_size": 32,
    "learning_rate": 0.001,
    "expected_observations": 10,
    "training_threshold": 0.8,
    "aggregation_threshold": 2,
    "aggregation_learning_rate": 0.5,
    "initial_window_size": 2,
    "max_window_size": 8,
    "ack_timeout": 0.25,
    "ack_delay": 0.04,
    "reassembly_timeout": 5.0,
    "completed_retention": 2.0,
    "max_retries": 3,
    "send_buffer_capacity": 128,
    "receive_buffer_capacity": 128,
    "dataset_capacity": 512,
    "model_retry_limit": 2,          # adapter-level reattempts after a failed FLAME transfer
    "model_retry_backoff_s": 2.0,    # avoids immediate repeated failure after churn
    "repeat_sensor_samples": False, # never silently repeat 128 MNIST observations
    "mnist_distribution": "spatial_clusters",  # use initial topology, not SN ID
    "evaluate_accuracy": False,  # opt in for full MNIST evaluation at CH shutdown
    "save_final_model": False,  # opt in to persist final model weights at CH shutdown
    "track_learning": False,  # CLIVE-only held-out accuracy snapshots after training/aggregation
    "learning_eval_samples": 512,  # fixed held-out MNIST subset, NOT training samples
    "learning_eval_every_updates": 1,  # measuring every update costs compute time
}


def _configuration(plan):
    settings = dict(DEFAULTS)
    settings.update(plan.flame_settings)
    unknown = set(settings) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown flame configuration keys: {sorted(unknown)}")
    for name in ("hello_interval_s", "sample_interval_s", "service_interval_s", "join_retry_s"):
        if not isinstance(settings[name], (float, int)) or settings[name] <= 0:
            raise ValueError(f"flame.{name} must be positive")
    if settings["trainer"] not in ("mnist", "mean_pixel"):
        raise ValueError("flame.trainer must be mnist or mean_pixel")
    if settings["sample_source"] not in ("mnist", "synthetic"):
        raise ValueError("flame.sample_source must be mnist or synthetic")
    if settings["max_offline_samples"] < 1:
        raise ValueError("max_offline_samples must be positive")
    if type(settings["model_retry_limit"]) is not int or settings["model_retry_limit"] < 0:
        raise ValueError("model_retry_limit must be a non-negative integer")
    if not isinstance(settings["model_retry_backoff_s"], (int, float)) or settings["model_retry_backoff_s"] <= 0:
        raise ValueError("model_retry_backoff_s must be positive")
    if type(settings["repeat_sensor_samples"]) is not bool:
        raise ValueError("repeat_sensor_samples must be true or false")
    if settings["mnist_distribution"] not in ("spatial_clusters", "sensor_id"):
        raise ValueError("mnist_distribution must be spatial_clusters or sensor_id")
    for option in ("evaluate_accuracy", "save_final_model", "track_learning"):
        if type(settings[option]) is not bool:
            raise ValueError(f"{option} must be true or false")
    if (settings["evaluate_accuracy"] or settings["save_final_model"] or settings["track_learning"]) and settings["trainer"] != "mnist":
        raise ValueError("MNIST accuracy/model-weight export requires flame.trainer=mnist")
    if type(settings["learning_eval_samples"]) is not int or not 32 <= settings["learning_eval_samples"] <= 10000:
        raise ValueError("learning_eval_samples must be an integer between 32 and 10000")
    if type(settings["learning_eval_every_updates"]) is not int or settings["learning_eval_every_updates"] < 1:
        raise ValueError("learning_eval_every_updates must be a positive integer")
    return settings


def _stable_seed(seed: int, node_name: str) -> int:
    data = f"FLAME-v1:{seed}:{node_name}".encode("ascii")
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def _mean_pixel_train(observations):
    """Cheap, explicitly non-ML workload for verifying real FLAME packets."""
    return {"mean_pixel": sum(sum(x.payload[:-1]) / 784 / 255 for x in observations) / len(observations)}


class _MNISTSource:
    """Stable, non-IID per-SN MNIST sampling; keep only a small subset resident."""

    def __init__(self, node_name: str, seed: int, settings: dict, *, initial_position=None, area_size=None):
        self.samples = []
        self.cursor = 0
        self.rng = random.Random(_stable_seed(seed, node_name))
        count = int(settings["mnist_samples_per_sensor"])
        if count < 1:
            raise ValueError("mnist_samples_per_sensor must be >= 1")
        distribution = settings.get("mnist_distribution", "sensor_id")
        if distribution == "spatial_clusters":
            if initial_position is None or area_size is None:
                raise ValueError("spatial MNIST distribution requires the node's initial position and area")
            from mnist_topology import spatial_digit
            digit = spatial_digit(initial_position, area_size)
        elif distribution == "sensor_id":
            digit = (int(node_name.split("-")[-1]) - 1) % 10
        else:
            raise ValueError(f"Unknown MNIST distribution: {distribution}")
        self.digit = digit
        self.distribution = distribution
        if settings["sample_source"] == "synthetic":
            # A wire-format-compatible test sample, not genuine MNIST data.
            self.samples = [bytes([digit * 18 + (i % 3)] * 784) + bytes([digit])
                            for i in range(count)]
        else:
            from mnist_cache import class_samples
            self.samples = class_samples(
                ROOT / "mnist" / "mnist.npz", digit, self.rng, count
            )

    def has_next(self, *, repeat=False):
        return bool(self.samples) and (repeat or self.cursor < len(self.samples))

    def collect(self):
        # The adapter checks has_next() before calling into DataAcquisition.
        # Retain modulo indexing for an explicitly requested transport stress run.
        sample = self.samples[self.cursor % len(self.samples)]
        self.cursor += 1
        return sample


class FlameProtocol(ProtocolInterface):
    """Actual FLAME communication, neighbor, sensing, and FL modules in CLIVE."""

    def __init__(self, node):
        super().__init__(node)
        self.s = _configuration(node.plan)
        self.role = int(node.spec.role)
        self.is_ch = self.role == 1
        self.model_id = int(self.s["model_id"])
        self.closed = False
        self.learning_history = None
        self._learning_events = 0
        self.stats = Counter()
        self._next_service = 0.0
        self._next_hello = 0.0 if self.is_ch else None
        self._next_sample = self.s["sample_interval_s"] if not self.is_ch else None
        self._next_leave = max(0.0, node.plan.config.duration_s - 0.3) if not self.is_ch else None
        self._left = False
        self._next_join = 0.0
        self._join_nonce = _stable_seed(node.plan.config.seed, node.spec.name) & 0xFFFF
        self._joined_ch = None
        self._join_target = None
        self._discovered_ch = {}  # (ip, port) -> first valid HELLO arrival (insertion order)
        self._cached_update = None
        self._update_exclude = None
        self._sent_updates = set()  # (peer address, local update_id): attempted or completed
        self._model_retry_counts = Counter()
        self._model_retry_after = {}
        self._model_transfer_status = {}  # (peer, update_id): pending / complete / failed / retry_exhausted
        self._offline_samples = deque(maxlen=self.s["max_offline_samples"])
        self.sensing = None
        self.acquisition = None
        self.neighbors = NeighborModule(model_id=self.model_id)
        InitializationModule(NodeConfig(
            role=self.role, host=node.spec.ip, port=node.spec.port,
            expected_observations=int(self.s["expected_observations"]),
            training_threshold=float(self.s["training_threshold"]),
            aggregation_threshold=int(self.s["aggregation_threshold"]),
            initial_window_size=int(self.s["initial_window_size"]),
            max_window_size=int(self.s["max_window_size"]),
            ack_timeout=float(self.s["ack_timeout"]),
            ack_delay=float(self.s["ack_delay"]),
            reassembly_timeout=float(self.s["reassembly_timeout"]),
            completed_retention=float(self.s["completed_retention"]),
            max_retries=int(self.s["max_retries"]),
        )).initialize()
        # IMPORTANT: no real UDP socket and no FLAME communication thread.
        # All data, including ACK and broadcast, passes through NodeSocket.
        self.comms = CommunicationModule(
            socket_interface=node.socket, role=self.role, auto_start=False,
            poll_socket=False, neighbor_module=self.neighbors,
            route_handler=self._on_message,
            event_handler=self._on_comm_event,
            buffer_size=int(self.s["receive_buffer_capacity"]),
            send_buffer_capacity=int(self.s["send_buffer_capacity"]),
            initial_window_size=int(self.s["initial_window_size"]),
            max_window_size=int(self.s["max_window_size"]),
            ack_timeout=float(self.s["ack_timeout"]),
            ack_delay=float(self.s["ack_delay"]),
            reassembly_timeout=float(self.s["reassembly_timeout"]),
            completed_retention=float(self.s["completed_retention"]),
            max_retries=int(self.s["max_retries"]),
        )
        if self.is_ch:
            model_state = None
            if self.s["trainer"] == "mnist":
                import torch
                torch.set_num_threads(1)
                torch.manual_seed(node.plan.config.seed)
                # Avoid importing torch (hundreds of MB in some environments) in SN processes.
                from flame.federated_learning import MNISTTrainer
                training_fn = MNISTTrainer(
                    local_epochs=int(self.s["local_epochs"]),
                    batch_size=int(self.s["batch_size"]),
                    learning_rate=float(self.s["learning_rate"]),
                )
                model_state = training_fn.get_model_state()
            else:
                training_fn = _mean_pixel_train
                model_state = {"mean_pixel": 0.0}
            dataset_manager = DatasetManager(DatasetStorage(capacity=int(self.s["dataset_capacity"])))
            model_manager = ModelManager(self.model_id, model_state=model_state)
            trainer = Trainer(
                dataset_manager,
                expected_observations=int(self.s["expected_observations"]),
                training_threshold=float(self.s["training_threshold"]),
                train_function=training_fn,
            )
            aggregation = Aggregation(
                model_manager,
                aggregation_threshold=int(self.s["aggregation_threshold"]),
                aggregation_learning_rate=float(self.s["aggregation_learning_rate"]),
            )
            self.fl = FederatedLearningModule(dataset_manager, trainer, model_manager, aggregation)
            if self.s["track_learning"] and node.results_dir:
                from learning_history import LearningHistory
                self.learning_history = LearningHistory(
                    ROOT / "mnist" / "mnist.npz",
                    Path(node.results_dir) / "flame" / f"{node.spec.name}-learning.jsonl",
                    seed=node.plan.config.seed, samples=self.s["learning_eval_samples"])
        else:
            self.fl = None
            # The thesis scatters equal-label observations geographically.
            # Read the PRECOMPUTED initial location; this is a fixed dataset
            # assignment, not an oracle giving the protocol future positions.
            with node.plan.open_trace() as trace:
                start_position = trace.position(0, node.spec.name)
            area_size = (node.plan.mobility_config.area_width_m,
                         node.plan.mobility_config.area_height_m)
            self._sample_source = _MNISTSource(
                node.spec.name, node.plan.config.seed, self.s,
                initial_position=start_position, area_size=area_size,
            )
            self.acquisition = DataAcquisition(self._sample_source.collect)

    # Minimal association/model-transfer history is retained even when verbose
    # protocol diagnostics are disabled. It is NOT sent over the radio.
    CONNECTION_EVENTS = frozenset({
        "ch_discovered", "join_sent", "joined", "leave_sent",
        "connection_lost", "member_expired", "join_pending_expired",
        "leave_observed", "sensor_received", "model_sent", "model_received",
        "model_retry_scheduled", "model_retry_exhausted",
    })

    def _record(self, name: str, **details):
        self.stats[name] += 1
        event = {"type": "flame_" + name, "time_s": self.node.elapsed(), **details}
        self.node.events.append(event)
        if name in self.CONNECTION_EVENTS and getattr(self.node, "connection_events", None) is not None:
            self.node.connection_events.append(event)

    def _on_comm_event(self, event):
        """Record FLAME diagnostics; react only to completed/failed model transfers.

        This callback observes FLAME's result. It NEVER modifies the underlying
        fragment, ACK, or sliding-window algorithm.
        """
        name = event["type"]
        self.stats[name] += 1
        if name in ("datagram_error", "transfer_failed", "transfer_complete", "message_reassembled"):
            logged = {
                "type": "flame_" + name,
                "time_s": self.node.elapsed(),
                **{key: value for key, value in event.items() if key != "type"},
            }
            self.node.events.append(logged)
            if name in ("transfer_failed", "transfer_complete") and getattr(self.node, "connection_events", None) is not None:
                self.node.connection_events.append(logged)
        if self.is_ch and name in ("transfer_failed", "transfer_complete"):
            self._track_model_transfer(event)

    def _track_model_transfer(self, event):
        """Schedule an application-level resend after FLAME reports failure.

        Do not mark non-model transfers, such as SENSOR_DATA, as model failures.
        A resent model may increase network traffic; it is not an ACK bug fix.
        """
        if int(event.get("packet_type", -1)) != int(PacketType.MODEL_UPDATE):
            return
        peer, update_id = event.get("destination"), event.get("message_id")
        if peer is None or update_id is None:
            return
        key = (tuple(peer), int(update_id))
        if event["type"] == "transfer_complete":
            self._model_transfer_status[key] = "complete"
            return
        if key not in self._model_transfer_status:
            return

        previous_retries = self._model_retry_counts[key]
        self._model_transfer_status[key] = "failed"
        if previous_retries >= self.s["model_retry_limit"]:
            self._model_transfer_status[key] = "retry_exhausted"
            self._record("model_retry_exhausted", destination=peer,
                         update_id=update_id, reason=event.get("reason"))
            return

        self._model_retry_counts[key] += 1
        delay = self.s["model_retry_backoff_s"] * (2 ** previous_retries)
        self._model_retry_after[key] = self.node.elapsed() + delay
        self._sent_updates.discard(key)
        self._record("model_retry_scheduled", destination=peer, update_id=update_id,
                     attempt=previous_retries + 1, reason=event.get("reason"))

    def start(self):
        self._next_service = self.node.elapsed() + self.s["service_interval_s"]
        if self.is_ch:
            self._next_hello = max(0.0, self.node.elapsed())
        self._record("started", role=self.role, model_id=self.model_id)

    def next_deadline(self):
        if self.closed:
            return None
        times = [self._next_service]
        if self._next_hello is not None:
            times.append(self._next_hello)
        if self._next_sample is not None:
            times.append(self._next_sample)
        if self._next_leave is not None:
            times.append(self._next_leave)
        return min(times)

    def _send_hello(self):
        self.comms.send_message({
            "packet_type": PacketType.HELLO, "role": 1, "model_id": self.model_id,
        }, destination=BROADCAST_ADDRESS)
        self._record("hello_sent")

    def on_timer(self, now_s):
        """One CLIVE tick: announce, sample, depart, then service protocol queues."""
        if self.closed:
            return
        self._maybe_send_hello(now_s)
        self._maybe_collect_sample(now_s)
        self._maybe_leave(now_s)
        self._maybe_service_communication(now_s)

    def _maybe_send_hello(self, now_s):
        if self._next_hello is not None and now_s >= self._next_hello:
            self._send_hello()
            self._next_hello = now_s + self.s["hello_interval_s"]

    def _maybe_collect_sample(self, now_s):
        if self._next_sample is None or now_s < self._next_sample:
            return
        if not self._sample_source.has_next(repeat=self.s["repeat_sensor_samples"]):
            self._record("sample_source_exhausted", samples_generated=self._sample_source.cursor)
            self._next_sample = None
            return  # Still service communications and LEAVE during this tick.

        if self._is_joined() and not self._offline_samples:
            self.sensing.destination = self._joined_ch
            self.sensing.collect_once()
            self._record("sample_collected")
        else:
            if len(self._offline_samples) == self._offline_samples.maxlen:
                self._record("offline_sample_dropped")
            self._offline_samples.append(self.acquisition.collect())
            self._record("sample_buffered")
            self._flush_offline_samples()
        self._next_sample = now_s + self.s["sample_interval_s"]

    def _maybe_leave(self, now_s):
        if self._next_leave is None or now_s < self._next_leave:
            return
        if self._is_joined():
            self.comms.send_message({
                "packet_type": PacketType.LEAVE, "role": 0, "leave_reason": 0,
            }, destination=self._joined_ch)
            self._record("leave_sent", destination=self._joined_ch)
        self._left = True
        self._next_leave = None
        self._next_sample = None

    def _maybe_service_communication(self, now_s):
        if now_s < self._next_service:
            return
        self._next_service = now_s + self.s["service_interval_s"]
        self.comms._service_send_buffer()
        self.comms._service_reliability()
        if self.sensing is not None and self._is_joined():
            self.sensing.flush_sample_buffer()
        previous_states = {(n.ip_address, n.port): n.association_state
                           for n in self.neighbors.get_neighbors()} if self.is_ch else {}
        expired = self.neighbors.check_expired_neighbors()
        # Observe FLAME's transitions without changing its neighbor state.
        if self.is_ch:
            for neighbor in expired:
                if neighbor.node_role != NodeRole.SENSOR_NODE:
                    continue
                previous = previous_states.get((neighbor.ip_address, neighbor.port))
                if previous == AssociationState.ASSOCIATED:
                    self._record("member_expired", source=(neighbor.ip_address, neighbor.port),
                                 reason="membership timeout")
                elif previous == AssociationState.JOIN_PENDING:
                    self._record("join_pending_expired", source=(neighbor.ip_address, neighbor.port),
                                 reason="JOIN request timed out before confirmation")
        elif getattr(self, "_joined_ch", None) is not None and not self._is_joined():
            self._record("connection_lost", destination=self._joined_ch,
                         reason="association became inactive or expired")
        if self.is_ch:
            self._send_pending_models()
        else:
            self._service_join(now_s)
            self._flush_offline_samples()

        # CLIVE persists compact counters and optional logs; release FLAME's
        # in-memory diagnostic queues instead of accumulating the same events twice.
        self.comms.pop_received()
        self.comms.pop_neighbor_messages()
        self.comms.pop_sensor_messages()
        self.comms.pop_fl_messages()
        self.comms.pop_ack_messages()
        self.comms.pop_events()
        for error in self.comms.pop_errors():
            self._record("error", reason=error["error"])

    def on_datagram(self, data: bytes, source: tuple[str, int]):
        self.comms.receive_datagram(data, source)
        self.comms._drain_once()

    def _is_joined(self):
        if self._joined_ch is None:
            return False
        neighbor = self.neighbors.get_neighbor(*self._joined_ch)
        return neighbor is not None and neighbor.association_state == AssociationState.ASSOCIATED

    def _service_join(self, now_s):
        if self._left or self._is_joined():
            return
        self._joined_ch = None
        if now_s < self._next_join:
            return
        # Choose the first compatible CH whose HELLO *this SN actually received*.
        # Arrival order is maintained by _on_message; IP addresses are not priorities.
        # If that CH times out, the next still-active discovered CH can be selected.
        active = {(n.ip_address, n.port): n for n in self.neighbors.get_neighbors()
                  if n.node_role == NodeRole.CLUSTER_HEAD
                  and n.association_state != AssociationState.INACTIVE
                  and n.model_id == self.model_id}
        ch = next((active[address] for address in self._discovered_ch if address in active), None)
        if ch is None:
            return
        dest = (ch.ip_address, ch.port)
        self._join_nonce = (self._join_nonce + 1) & 0xFFFF
        self.neighbors.start_join(ch.ip_address, ch.port, self._join_nonce, model_id=self.model_id)
        self.comms.send_message({
            "packet_type": PacketType.JOIN, "role": 0,
            "join_id": self._join_nonce, "model_id": self.model_id,
        }, destination=dest)
        self._join_target = dest
        self._next_join = now_s + self.s["join_retry_s"]
        self._record("join_sent", destination=dest)

    def _queue_payload(self, payload):
        if self._left or not self._is_joined():
            return False
        if not self.comms.send_buffer_available:
            return False
        queued = self.comms.try_enqueue_message({
            "packet_type": PacketType.SENSOR_DATA,
            "role": 0, "sensor_type": 1, "payload": payload,
        }, destination=self._joined_ch)
        if not queued:
            self._record("sensor_enqueue_blocked", destination=self._joined_ch,
                         reason="FLAME send buffer full")
            return False
        self._record("sensor_queued", destination=self._joined_ch)
        return True

    def _flush_offline_samples(self):
        while self._offline_samples and self._is_joined():
            if not self._queue_payload(self._offline_samples[0]):
                break
            self._offline_samples.popleft()

    def _on_message(self, packet):
        """Route an already parsed/reassembled FLAME message by packet type."""
        kind = packet["packet_type"]
        source = packet.get("source_address")
        if source is not None:
            neighbor = self.neighbors.get_neighbor(*source)
            if neighbor is not None and neighbor.association_state != AssociationState.INACTIVE:
                neighbor.expiration_time = time.time() + self.s["neighbor_timeout_s"]

        if kind == PacketType.HELLO:
            # Only a valid, model-compatible HELLO counts as discovery. Preserve
            # the order of FIRST reception, not packet send time or CH IP sorting.
            if not self.is_ch and source:
                neighbor = self.neighbors.get_neighbor(*source)
                if (neighbor is not None and neighbor.node_role == NodeRole.CLUSTER_HEAD
                        and neighbor.model_id == self.model_id
                        and neighbor.association_state != AssociationState.INACTIVE
                        and source not in self._discovered_ch):
                    self._discovered_ch[source] = self.node.elapsed()
                    self._record("ch_discovered", source=source,
                                 reason="first compatible HELLO received")
            if not self.is_ch and not self._is_joined():
                self._service_join(self.node.elapsed())
            elif self.is_ch:
                self._send_pending_models()
        elif kind == PacketType.JOIN_ACCEPT and not self.is_ch:
            self._handle_join_accept(packet, source)
        elif kind == PacketType.SENSOR_DATA and self.is_ch:
            self._handle_sensor_observation(packet, source)
        elif kind == PacketType.MODEL_UPDATE and self.is_ch:
            self._handle_peer_model(packet, source)
        elif kind == PacketType.LEAVE and self.is_ch and source:
            self._record("leave_observed", source=source)
        elif kind == PacketType.LEAVE and not self.is_ch and source == self._joined_ch:
            self._record("connection_lost", destination=source,
                         reason="received LEAVE")
            self._joined_ch = None

    def _handle_join_accept(self, packet, source):
        was_joined = self._is_joined()
        if (source == self._join_target and packet["rejected"] == 0
                and not self._is_joined()):
            # FLAME's NeighborModule has already processed JOIN_ACCEPT.
            neighbor = self.neighbors.get_neighbor(*source)
            if neighbor and neighbor.association_state == AssociationState.ASSOCIATED:
                self._joined_ch = source
        if not was_joined and self._is_joined():
            self._joined_ch = source
            self.sensing = SensingModule(self.acquisition, self.comms, source)
            self._record("joined", destination=source,
                         packet_type=int(packet["packet_type"]),
                         sequence_number=packet.get("sequence_number"))
            self._flush_offline_samples()

    def _record_learning(self, cause):
        """Record accuracy from the current FLAME state, without mutating it."""
        if self.learning_history is None:
            return
        self._learning_events += 1
        if self._learning_events % self.s["learning_eval_every_updates"]:
            return
        try:
            revision = self.fl.model_manager.get_update_id()
            self.learning_history.record(
                self.fl.model_manager.get_model_state(),
                node=self.node.spec.name, time_s=self.node.elapsed(),
                revision=revision, cause=cause,
                observations=self.fl.dataset_manager.get_observation_count())
        except Exception as exc:
            self._record("learning_evaluation_error", reason=str(exc))

    def _handle_sensor_observation(self, packet, source):
        neighbor = self.neighbors.get_neighbor(*source) if source else None
        if neighbor is None or neighbor.association_state != AssociationState.ASSOCIATED:
            self._record("unassociated_sensor_rejected", source=source)
            return
        try:
            previous_id = self.fl.model_manager.get_update_id()
            self.fl.handle_message(packet)
            self._record("sensor_received", source=source,
                         packet_type=int(packet["packet_type"]),
                         sequence_number=packet.get("sequence_number"),
                         message_id=packet.get("data_id"))
            if self.fl.model_manager.get_update_id() > previous_id:
                self._record("trained", update_id=self.fl.model_manager.get_update_id(),
                             trigger_sequence_number=packet.get("sequence_number"))
                self._record_learning("local_training")
                self._collect_model_update()
        except Exception as error:
            self._record("fl_error", reason=str(error))
            raise

    def _handle_peer_model(self, packet, source):
        neighbor = self.neighbors.get_neighbor(*source) if source else None
        if (neighbor is None or neighbor.node_role != NodeRole.CLUSTER_HEAD
                or neighbor.model_id != self.model_id):
            self._record("invalid_model_peer", source=source)
            return
        try:
            previous_id = self.fl.model_manager.get_update_id()
            self.fl.handle_message(packet)
            self._record("model_received", source=source,
                         packet_type=int(packet["packet_type"]),
                         sequence_number=packet.get("sequence_number"),
                         message_id=packet.get("update_id"))
            if self.fl.model_manager.get_update_id() > previous_id:
                # Peer that triggered this aggregation; other buffered peers may
                # have contributed too. Do not imply it was the sole contributor.
                self._record("aggregated", update_id=self.fl.model_manager.get_update_id(),
                             trigger_sequence_number=packet.get("sequence_number"),
                             trigger_peer=source)
                self._record_learning("peer_aggregation")
                self._collect_model_update(exclude=source)
        except Exception as error:
            self._record("fl_error", reason=str(error))
            raise

    def _collect_model_update(self, exclude=None):
        for message in self.fl.pop_outbound_updates():
            self._cached_update = message
            self._update_exclude = exclude or message.get("_exclude_source")
            self._sent_updates.clear()
            # Old versions still have late ACKs; preserve their status for metrics.
        self._send_pending_models()

    def _send_pending_models(self):
        if not self.is_ch or self._cached_update is None:
            return
        update_id = self._cached_update["update_id"]
        # Only model-compatible, reachable candidate CHs. CLIVE's radio model still
        # decides actual delivery, i.e. membership is not an oracle for reachability.
        for n in self.neighbors.get_neighbors():
            peer = (n.ip_address, n.port)
            if (n.node_role != NodeRole.CLUSTER_HEAD or n.model_id != self.model_id
                    or n.association_state == AssociationState.INACTIVE or peer == self._update_exclude
                    or (peer, update_id) in self._sent_updates):
                continue
            if self.node.elapsed() < self._model_retry_after.get((peer, update_id), 0):
                continue
            # Don't enqueue unbounded duplicated transfers if many contacts occur.
            if len(self.comms.reliability_manager.outbound_transfers) >= 6:
                break
            self.comms.send_message(self._cached_update, destination=peer)
            self._sent_updates.add((peer, update_id))
            self._model_transfer_status[(peer, update_id)] = "pending"
            self._record("model_sent", destination=peer, update_id=update_id,
                         attempt=self._model_retry_counts[(peer, update_id)] + 1)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.comms.stop()  # auto_start=False: does not create/stop an actual worker.
        result = {
            "node": self.node.spec.name,
            "role": "CH" if self.is_ch else "SN",
            "model_id": self.model_id,
            "counters": dict(self.stats),
            "neighbors": [
                {"ip": n.ip_address, "port": n.port, "role": n.node_role.value,
                 "state": n.association_state.value, "model_id": n.model_id}
                for n in self.neighbors.get_neighbors()
            ],
        }
        result["workload"] = {
            "sample_source": self.s["sample_source"],
            "trainer": self.s["trainer"],
            "real_neural_network_training": self.s["trainer"] == "mnist",
            "repeat_sensor_samples": self.s["repeat_sensor_samples"],
            "mnist_digit_assignment": self.s["mnist_distribution"],
            "mnist_digit": None if self.is_ch else self._sample_source.digit,
        }
        if self.is_ch:
            result["model_update_id"] = self.fl.model_manager.get_update_id()
            result["learning_history"] = {
                "enabled": self.learning_history is not None,
                "samples_per_measurement": self.s["learning_eval_samples"] if self.learning_history else None,
                "every_updates": self.s["learning_eval_every_updates"] if self.learning_history else None,
                "measurement": "held-out fixed subset, not final 10k accuracy",
            }
            result["observations_retained"] = self.fl.dataset_manager.get_observation_count()
            result["observations"] = result["observations_retained"]  # backward compatibility
            # Evaluate the final aggregated state, not the trainer's possibly
            # stale last locally-trained weights. Keep this CLIVE-only.
            if self.s["evaluate_accuracy"] or self.s["save_final_model"]:
                from mnist_evaluation import evaluate_and_save
                try:
                    result["model_evaluation"] = evaluate_and_save(
                        self.fl.model_manager.get_model_state(),
                        ROOT / "mnist" / "mnist.npz",
                        Path(self.node.results_dir) / "flame" / f"{self.node.spec.name}-weights.pt",
                        accuracy=self.s["evaluate_accuracy"],
                        save_model=self.s["save_final_model"],
                    )
                except Exception as error:
                    result["model_evaluation"] = {"status": "error", "reason": str(error)}
                    self.stats["model_evaluation_error"] += 1
            states = list(self._model_transfer_status.values())
            result["model_transfer_summary"] = {
                "started_versions": len(states),
                "completed_versions": states.count("complete"),
                "failed_versions": states.count("failed") + states.count("retry_exhausted"),
                "unfinished_versions": states.count("pending"),
                "retry_scheduled": self.stats.get("model_retry_scheduled", 0),
                "note": "sender ACK outcomes; failures may be caused by FLAME or emulated radio",
            }
        else:
            result["offline_samples_remaining"] = len(self._offline_samples)
            result["samples_generated"] = self._sample_source.cursor
        if self.node.results_dir:
            from orchestrator import atomic_json
            path = Path(self.node.results_dir) / "flame" / f"{self.node.spec.name}.json"
            atomic_json(str(path), result)
