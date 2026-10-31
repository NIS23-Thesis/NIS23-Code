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
    return settings


def _stable_seed(seed: int, node_name: str) -> int:
    data = f"FLAME-v1:{seed}:{node_name}".encode("ascii")
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def _mean_pixel_train(observations):
    """Cheap, explicitly non-ML workload for verifying real FLAME packets."""
    return {"mean_pixel": sum(sum(x.payload[:-1]) / 784 / 255 for x in observations) / len(observations)}


class _MNISTSource:
    """Stable, non-IID per-SN MNIST sampling; keep only a small subset resident."""

    def __init__(self, node_name: str, seed: int, settings: dict):
        self.samples = []
        self.cursor = 0
        self.rng = random.Random(_stable_seed(seed, node_name))
        count = int(settings["mnist_samples_per_sensor"])
        if count < 1:
            raise ValueError("mnist_samples_per_sensor must be >= 1")
        digit = (int(node_name.split("-")[-1]) - 1) % 10
        if settings["sample_source"] == "synthetic":
            # A wire-format-compatible test sample, not genuine MNIST data.
            self.samples = [bytes([digit * 18 + (i % 3)] * 784) + bytes([digit])
                            for i in range(count)]
        else:
            from mnist_cache import class_samples
            self.samples = class_samples(
                ROOT / "mnist" / "mnist.npz", digit, self.rng, count
            )

    def collect(self):
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
        self._cached_update = None
        self._update_exclude = None
        self._sent_updates = set()  # (peer address, local update_id)
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
        else:
            self.fl = None
            self.acquisition = DataAcquisition(
                _MNISTSource(node.spec.name, node.plan.config.seed, self.s).collect
            )

    def _record(self, name: str, **details):
        self.stats[name] += 1
        self.node.events.append({"type": "flame_" + name, "time_s": self.node.elapsed(), **details})

    def _on_comm_event(self, event):
        name = event["type"]
        self.stats[name] += 1
        if name in ("datagram_error", "transfer_failed", "transfer_complete", "message_reassembled"):
            self.node.events.append({
                "type": "flame_" + name, "time_s": self.node.elapsed(),
                **{key: value for key, value in event.items() if key != "type"},
            })

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
        if self.closed:
            return
        if self._next_hello is not None and now_s >= self._next_hello:
            self._send_hello()
            self._next_hello = now_s + self.s["hello_interval_s"]
        if self._next_sample is not None and now_s >= self._next_sample:
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
        if self._next_leave is not None and now_s >= self._next_leave:
            if self._is_joined():
                self.comms.send_message({
                    "packet_type": PacketType.LEAVE, "role": 0, "leave_reason": 0,
                }, destination=self._joined_ch)
                self._record("leave_sent", destination=self._joined_ch)
            self._left = True
            self._next_leave = None
            self._next_sample = None
        if now_s >= self._next_service:
            self._next_service = now_s + self.s["service_interval_s"]
            self.comms._service_send_buffer()
            self.comms._service_reliability()
            if self.sensing is not None and self._is_joined():
                self.sensing.flush_sample_buffer()
            self.neighbors.check_expired_neighbors()
            if not self.is_ch:
                self._service_join(now_s)
                self._flush_offline_samples()
            else:
                self._send_pending_models()
            # FLAME's standalone implementation stores diagnostic queues; bound them
            # here because CLIVE already persists counters and optional event logs.
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
        candidates = sorted(
            (n for n in self.neighbors.get_neighbors()
             if n.node_role == NodeRole.CLUSTER_HEAD
             and n.association_state != AssociationState.INACTIVE
             and n.model_id == self.model_id),
            key=lambda n: (n.ip_address, n.port),
        )
        if not candidates:
            return
        ch = candidates[0]
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
        self.comms.try_enqueue_message({
            "packet_type": PacketType.SENSOR_DATA,
            "role": 0, "sensor_type": 1, "payload": payload,
        }, destination=self._joined_ch)
        self._record("sensor_queued", destination=self._joined_ch)
        return True

    def _flush_offline_samples(self):
        while self._offline_samples and self._is_joined():
            if not self._queue_payload(self._offline_samples[0]):
                break
            self._offline_samples.popleft()

    def _on_message(self, packet):
        kind = packet["packet_type"]
        source = packet.get("source_address")
        if source is not None:
            n = self.neighbors.get_neighbor(*source)
            if n is not None and n.association_state != AssociationState.INACTIVE:
                n.expiration_time = time.time() + self.s["neighbor_timeout_s"]
        if kind == PacketType.HELLO:
            if not self.is_ch and not self._is_joined():
                self._service_join(self.node.elapsed())
            elif self.is_ch:
                self._send_pending_models()
        elif kind == PacketType.JOIN_ACCEPT and not self.is_ch:
            was_joined = self._is_joined()
            if source == self._join_target and packet["rejected"] == 0 and self._is_joined() is False:
                # NeighborModule has already processed JOIN_ACCEPT in _handle_parsed_packet.
                n = self.neighbors.get_neighbor(*source)
                if n and n.association_state == AssociationState.ASSOCIATED:
                    self._joined_ch = source
            if not was_joined and self._is_joined():
                self._joined_ch = source
                self.sensing = SensingModule(self.acquisition, self.comms, source)
                self._record("joined", destination=source)
                self._flush_offline_samples()
        elif kind == PacketType.SENSOR_DATA and self.is_ch:
            n = self.neighbors.get_neighbor(*source) if source else None
            if n is None or n.association_state != AssociationState.ASSOCIATED:
                self._record("unassociated_sensor_rejected", source=source)
                return
            try:
                prior = self.fl.model_manager.get_update_id()
                self.fl.handle_message(packet)
                self._record("sensor_received", source=source)
                if self.fl.model_manager.get_update_id() > prior:
                    self._record("trained", update_id=self.fl.model_manager.get_update_id())
                    self._collect_model_update()
            except Exception as exc:
                self._record("fl_error", reason=str(exc))
                raise
        elif kind == PacketType.MODEL_UPDATE and self.is_ch:
            n = self.neighbors.get_neighbor(*source) if source else None
            if n is None or n.node_role != NodeRole.CLUSTER_HEAD or n.model_id != self.model_id:
                self._record("invalid_model_peer", source=source)
                return
            try:
                before = self.fl.model_manager.get_update_id()
                self.fl.handle_message(packet)
                self._record("model_received", source=source)
                if self.fl.model_manager.get_update_id() > before:
                    self._record("aggregated", update_id=self.fl.model_manager.get_update_id())
                    self._collect_model_update(exclude=source)
            except Exception as exc:
                self._record("fl_error", reason=str(exc))
                raise
        elif kind == PacketType.LEAVE and not self.is_ch and source == self._joined_ch:
            self._joined_ch = None

    def _collect_model_update(self, exclude=None):
        for message in self.fl.pop_outbound_updates():
            self._cached_update = message
            self._update_exclude = exclude or message.get("_exclude_source")
            self._sent_updates.clear()
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
            # Don't enqueue unbounded duplicated transfers if many contacts occur.
            if len(self.comms.reliability_manager.outbound_transfers) >= 6:
                break
            self.comms.send_message(self._cached_update, destination=peer)
            self._sent_updates.add((peer, update_id))
            self._record("model_sent", destination=peer, update_id=update_id)

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
        if self.is_ch:
            result["model_update_id"] = self.fl.model_manager.get_update_id()
            result["observations"] = self.fl.dataset_manager.get_observation_count()
        else:
            result["offline_samples_remaining"] = len(self._offline_samples)
        if self.node.results_dir:
            from orchestrator import atomic_json
            path = Path(self.node.results_dir) / "flame" / f"{self.node.spec.name}.json"
            atomic_json(str(path), result)
