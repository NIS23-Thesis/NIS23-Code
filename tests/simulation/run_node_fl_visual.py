"""Interactive FLAME Cluster Head/Sensor Node FL-visualization runner.

Run this file in one terminal per node.  If ``--role`` is omitted, the
terminal prompts for ``sensor`` or ``cluster-head``.  A Cluster Head sends
periodic HELLO broadcasts, while Sensor Nodes automatically JOIN the first
Cluster Head they discover and can send PNG/JPEG files with ``send``.

The runner intentionally uses the existing FLAME CommunicationModule,
NeighborModule, SocketInterface, and file-transfer manifest helpers.  It is
an operator-facing simulation harness, not a second protocol implementation.
"""

from __future__ import annotations

import argparse
import readline
import shlex
import sys
import threading
import time
import os
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

from PIL import Image


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from flame.communication import CommunicationModule, PacketType, SocketInterface
from flame.federated_learning import (
    Aggregation,
    DatasetManager,
    DatasetStorage,
    FederatedLearningModule,
    MNISTTrainer,
    ModelManager,
    Trainer,
)
from flame.neighbor import AssociationState, NeighborModule, NodeRole

DEFAULT_DISCOVERY_PORT = 50010
DEFAULT_HELLO_INTERVAL=5.0


def packet_type_name(value: Any) -> str:
    if isinstance(value, PacketType):
        return value.name
    return str(value)


def endpoint_name(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple) and len(value) == 2:
        return f"{value[0]}:{value[1]}"
    return str(value)


class TerminalOutput:
    """Serialize output from the command loop and communication threads."""

    PROMPT = "flame> "

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._input_active = False

    def begin_input(self) -> None:
        with self._lock:
            self._input_active = True

    def end_input(self) -> None:
        with self._lock:
            self._input_active = False

    def write(self, message: str) -> None:
        with self._lock:
            if self._input_active and sys.stdin.isatty() and sys.stdout.isatty():
                # Keep the active readline buffer visible after a background
                # communication event writes a notification to the terminal.
                current_input = readline.get_line_buffer()
                sys.stdout.write(
                    f"\r\033[K{message}\n{self.PROMPT}{current_input}"
                )
                sys.stdout.flush()
            else:
                print(message, flush=True)

    def event(self, event: dict) -> None:
        """Print communication events in a compact, traceable format."""
        event_type = event.get("type")
        packet_type = packet_type_name(event.get("packet_type", "EVENT"))

        if event_type == "control_sent":
            self.write(
                f"[SEND] {packet_type} seq={event.get('sequence_number')} "
                f"to={endpoint_name(event.get('destination'))}"
            )
        elif event_type == "message_delivered":
            self.write(
                f"[RECV] {packet_type} seq={event.get('sequence_number')} "
                f"from={endpoint_name(event.get('source'))}"
            )
        elif event_type == "fragment_sent":
            self.write(
                f"[SEND] {packet_type} data_id={event.get('message_id')} "
                f"seq={event.get('sequence_number')} "
                f"block={event.get('block_number')} "
                f"window={event.get('window_start')}-"
                f"{event.get('window_end')} "
                f"size={event.get('window_size')} "
                f"reason={event.get('reason')} "
                f"to={endpoint_name(event.get('destination'))}"
            )
        elif event_type == "fragment_received":
            self.write(
                f"[RECV] {packet_type} data_id={event.get('message_id')} "
                f"seq={event.get('sequence_number')} "
                f"block={event.get('block_number')}/"
                f"{event.get('last_block_number')} "
                f"from={endpoint_name(event.get('source'))}"
            )
        elif event_type in {"ack_sent", "ack_received"}:
            direction = "SEND" if event_type == "ack_sent" else "RECV"
            peer_key = "destination" if event_type == "ack_sent" else "source"
            self.write(
                f"[{direction}] {packet_type} data_id={event.get('message_id')} "
                f"seq={event.get('sequence_number')} "
                f"window={event.get('window_start')}-"
                f"{event.get('window_end')} "
                f"size={event.get('window_size', '-')} "
                f"blocks={event.get('received_blocks')} "
                f"{('to' if direction == 'SEND' else 'from')}="
                f"{endpoint_name(event.get(peer_key))}"
            )
        elif event_type == "message_reassembled":
            self.write(
                f"[REASSEMBLED] {packet_type} data_id={event.get('message_id')} "
                f"seq={event.get('sequence_number')} "
                f"fragments={event.get('fragment_count')} "
                f"from={endpoint_name(event.get('source'))}"
            )
        elif event_type == "window_advanced":
            self.write(
                f"[WINDOW] {packet_type} data_id={event.get('message_id')} "
                f"seq={event.get('sequence_number')} "
                f"advanced next={event.get('window_start')}-"
                f"{event.get('window_end')} "
                f"size={event.get('window_size')} "
                f"to={endpoint_name(event.get('destination'))}"
            )
        elif event_type == "transfer_started":
            self.write(
                f"[TRANSFER] started {packet_type} "
                f"data_id={event.get('message_id')} "
                f"fragments={event.get('fragment_count')}"
            )
        elif event_type == "transfer_complete":
            self.write(
                f"[TRANSFER] complete {packet_type} "
                f"data_id={event.get('message_id')} "
                f"to={endpoint_name(event.get('destination'))}"
            )
        elif event_type == "transfer_failed":
            self.write(
                f"[TRANSFER] FAILED {packet_type} "
                f"data_id={event.get('message_id')} "
                f"reason={event.get('reason')}"
            )
        elif event_type == "reassembly_expired":
            self.write(
                f"[REASSEMBLY] expired {packet_type} "
                f"data_id={event.get('message_id')} "
                f"from={endpoint_name(event.get('source'))}"
            )
        elif event_type == "datagram_error":
            self.write(
                f"[ERROR] datagram from={endpoint_name(event.get('source'))}: "
                f"{event.get('error')}"
            )


class FLVisualTerminalOutput(TerminalOutput):
    """Keep data-transfer and FL events while hiding HELLO packet events."""

    def event(self, event: dict) -> None:
        if packet_type_name(event.get("packet_type", "EVENT")) == "HELLO":
            return
        super().event(event)


class InteractiveNode:
    def __init__(
        self,
        *,
        role: int,
        host: str,
        port: int,
        discovery_port: int,
        model_id: int,
        hello_interval: float,
        neighbor_csv: Optional[str],
        data_dir: Path,
        expected_observations: int,
        training_threshold: float,
        aggregation_threshold: int,
    ) -> None:
        self.role = role
        self.host = host
        self.discovery_port = discovery_port
        self.model_id = model_id
        self.hello_interval = hello_interval
        self.neighbor_csv = neighbor_csv
        self.data_dir = data_dir
        self.expected_observations = expected_observations
        self.training_threshold = training_threshold
        self.aggregation_threshold = aggregation_threshold
        self.send_dir = data_dir / "send"
        self.received_dir = data_dir / "received"
        self.send_dir.mkdir(parents=True, exist_ok=True)
        if role == 1:
            self.received_dir.mkdir(parents=True, exist_ok=True)
        self.output = FLVisualTerminalOutput()
        self.neighbor = NeighborModule()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._monitor: Optional[threading.Thread] = None
        self._active_cluster_head: Optional[tuple[str, int]] = None
        self._next_join_id = 0
        self._next_mnist_data_id = 0
        self.fl_module: Optional[FederatedLearningModule] = None
        self._fl_trace_events: Queue[dict] = Queue()

        if role == 1:
            self.socket = SocketInterface(
                host=host,
                port=port,
                timeout=0.05,
                broadcast=True,
            )
            self.discovery_socket = SocketInterface(
                host="0.0.0.0",
                port=discovery_port,
                timeout=0.05,
                reuse_address=True,
                reuse_port=True,
            )
        else:
            self.socket = SocketInterface(host=host, port=port, timeout=0.05)
            self.discovery_socket = SocketInterface(
                host="0.0.0.0",
                port=discovery_port,
                timeout=0.05,
                reuse_address=True,
                reuse_port=True,
            )

        if role == 1:
            try:
                mnist_train_function = MNISTTrainer()
                dataset_manager = DatasetManager(DatasetStorage())
                model_manager = ModelManager(
                    model_id,
                    mnist_train_function.get_model_state(),
                )
                trainer = Trainer(
                    dataset_manager,
                    expected_observations=expected_observations,
                    training_threshold=training_threshold,
                    train_function=mnist_train_function,
                )
                self.fl_module = FederatedLearningModule(
                    dataset_manager,
                    trainer,
                    model_manager,
                    Aggregation(
                        model_manager,
                        aggregation_threshold=aggregation_threshold,
                    ),
                )
            except RuntimeError as exc:
                self.output.write(f"[FL] unavailable: {exc}")

        self.communication = CommunicationModule(
            self.socket,
            discovery_socket_interface=self.discovery_socket,
            role=role,
            neighbor_module=self.neighbor,
            route_handler=(
                self._handle_fl_message
                if self.fl_module is not None
                else None
            ),
            event_handler=self.output.event,
            poll_interval=0.01,
        )

    @property
    def role_name(self) -> str:
        return "Cluster Head" if self.role == 1 else "Sensor Node"

    def start(self) -> None:
        self.output.write(
            f"[{self.role_name}] data socket listening on "
            f"{endpoint_name(self.socket.address)}"
        )
        self.output.write(f"[{self.role_name}] send folder: {self.send_dir}")
        if self.role == 1:
            self.output.write(
                f"[{self.role_name}] received folder: {self.received_dir}"
            )
            if self.fl_module is not None:
                self.output.write(
                    f"[FL] WAFL-MLP ready; train after "
                    f"{self.expected_observations * self.training_threshold:.1f}+ "
                    "new observations"
                )
        if self.role == 1:
            self.output.write(
                f"[{self.role_name}] discovery broadcast active on "
                f"255.255.255.255:{self.discovery_port} "
                f"interval={self.hello_interval}s"
            )
            self._worker = threading.Thread(
                target=self._cluster_head_loop,
                name="flame-hello-thread",
                daemon=True,
            )
            self._monitor = threading.Thread(
                target=self._cluster_head_monitor,
                name="flame-cluster-head-monitor",
                daemon=True,
            )
        else:
            self.output.write(
                f"[{self.role_name}] discovery listener active on "
                f"0.0.0.0:{self.discovery_port}"
            )
            self._worker = threading.Thread(
                target=self._sensor_loop,
                name="flame-sensor-monitor",
                daemon=True,
            )
        self._worker.start()
        if self._monitor is not None:
            self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)
        if self._monitor and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=1.0)
        self.communication.stop()

        if self.role == 1:
            csv_path = self.neighbor_csv or self.data_dir / "neighbor_state.csv"
            try:
                exported = self.neighbor.export_csv(csv_path)
                self.output.write(f"[CSV] neighbor state exported to {exported}")
            except OSError as exc:
                self.output.write(f"[ERROR] could not export neighbor CSV: {exc}")

        self.socket.close()
        if self.discovery_socket is not None:
            self.discovery_socket.close()

    def _send_hello(self) -> None:
        self.communication.send_message(
            {
                "packet_type": PacketType.HELLO,
                "role": 1,
                "model_id": self.model_id,
            },
            destination=("255.255.255.255", self.discovery_port),
        )

    def _cluster_head_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._send_hello()
            except OSError as exc:
                self.output.write(f"[ERROR] HELLO send failed: {exc}")
            self._stop.wait(self.hello_interval)

    def _check_cluster_head_expiration(self) -> None:
        if self._active_cluster_head is None:
            return

        cluster_head = self.neighbor.get_neighbor(
            self._active_cluster_head[0],
            self._active_cluster_head[1],
        )

        if cluster_head is None:
            return

        if cluster_head.association_state == AssociationState.INACTIVE:
            old_cluster_head = self._active_cluster_head
            self._active_cluster_head = None

            self.output.write(
                f"[FAILOVER] Cluster Head {endpoint_name(old_cluster_head)} "
                "is inactive; waiting for another Cluster Head."
            )

    def _sensor_loop(self) -> None:
        while not self._stop.is_set():
            for message in self.communication.pop_neighbor_messages():
                self._handle_neighbor_message(message)

            for message in self.communication.pop_sensor_messages():
                if "file_metadata" in message:
                    self._store_received_file(message)
                    self.output.write(
                        f"[FILE] received {message.get('logical_name')} "
                        f"encoding={message.get('encoding')} "
                        f"bytes={message.get('file_length')} "
                        f"data_id={message.get('data_id')} "
                        f"from={endpoint_name(message.get('source_address'))}"
                    )
                else:
                    self.output.write(
                        f"[DATA] received data_id={message.get('data_id')} "
                        f"bytes={len(message.get('payload', b''))}"
                    )

            self.neighbor.check_expired_neighbors()
            self._check_cluster_head_expiration()
            self._stop.wait(0.05)

            self._check_cluster_head_expiration()

            self._stop.wait(0.05)

    def _cluster_head_monitor(self) -> None:
        while not self._stop.is_set():
            for message in self.communication.pop_neighbor_messages():
                self._handle_neighbor_message(message)

            for message in self.communication.pop_sensor_messages():
                if "file_metadata" in message:
                    self._store_received_file(message)
                    self.output.write(
                        f"[FILE] received {message.get('logical_name')} "
                        f"encoding={message.get('encoding')} "
                        f"bytes={message.get('file_length')} "
                        f"data_id={message.get('data_id')} "
                        f"from={endpoint_name(message.get('source_address'))}"
                    )
                else:
                    self.output.write(
                        f"[DATA] received data_id={message.get('data_id')} "
                        f"bytes={len(message.get('payload', b''))}"
                    )

            for message in self.communication.pop_fl_messages():
                self.output.write(
                    f"[FL] MODEL_UPDATE received update_id="
                    f"{message.get('update_id')} "
                    f"from={endpoint_name(message.get('source_address'))}"
                )

            self._drain_fl_trace_events()
            self._send_pending_model_updates()

            self.neighbor.check_expired_neighbors()
            self._stop.wait(0.05)

    def _handle_fl_message(self, message: dict) -> Any:
        """Route a complete FL message and record a concise operator trace."""
        if self.fl_module is None:
            return None

        result = self.fl_module.handle_message(message)
        packet_type = message.get("packet_type")
        if packet_type == PacketType.SENSOR_DATA:
            self._fl_trace_events.put(
                {
                    "type": "observation",
                    "source": message.get("source_address"),
                    "count": self.fl_module.dataset_manager.get_observation_count(),
                    "trained": result is not None,
                    "update_id": self.fl_module.model_manager.get_update_id(),
                }
            )
        elif packet_type == PacketType.MODEL_UPDATE:
            self._fl_trace_events.put(
                {
                    "type": "aggregation",
                    "source": message.get("source_address"),
                    "aggregated": result is not None,
                    "update_id": self.fl_module.model_manager.get_update_id(),
                }
            )
        return result

    def _drain_fl_trace_events(self) -> None:
        while True:
            try:
                event = self._fl_trace_events.get_nowait()
            except Empty:
                return

            source = endpoint_name(event.get("source"))
            if event["type"] == "observation":
                self.output.write(
                    f"[FL] observation accepted count={event['count']}/"
                    f"{self.expected_observations} from={source}"
                )
                if event["trained"]:
                    self.output.write(
                        f"[FL] local training complete model_update_id="
                        f"{event['update_id']}"
                    )
            elif event["type"] == "aggregation" and event["aggregated"]:
                self.output.write(
                    f"[FL] aggregation complete model_update_id="
                    f"{event['update_id']} from={source}"
                )

    def _store_received_file(self, message: dict) -> None:
        if self.role != 1:
            return
        file_name = Path(str(message.get("logical_name", "received-image"))).name
        source = endpoint_name(message.get("source_address"))
        safe_source = source.replace(":", "_").replace("/", "_")
        data_id = message.get("data_id", "unknown")
        output_path = self.received_dir / f"{safe_source}_data{data_id}_{file_name}"
        try:
            output_path.write_bytes(message.get("payload", b""))
            self.output.write(f"[FILE] saved {output_path}")
        except OSError as exc:
            self.output.write(f"[ERROR] could not save {output_path}: {exc}")

    def _send_pending_model_updates(self) -> None:
        if self.role != 1 or self.fl_module is None:
            return

        compatible_neighbors = [
            neighbor
            for neighbor in self.neighbor.get_neighbors()
            if (
                neighbor.node_role == NodeRole.CLUSTER_HEAD
                and neighbor.port != self.socket.address[1]
                and neighbor.association_state != AssociationState.INACTIVE
                and neighbor.model_id == self.model_id
            )
        ]
        if not compatible_neighbors:
            return

        for update in self.fl_module.pop_outbound_updates():
            excluded_source = update.get("_exclude_source")
            for neighbor in compatible_neighbors:
                destination = (neighbor.ip_address, neighbor.port)
                if excluded_source == destination:
                    continue
                try:
                    self.communication.send_message(
                        update,
                        destination=destination,
                    )
                    self.output.write(
                        f"[FL] MODEL_UPDATE update_id={update.get('update_id')} "
                        f"to={endpoint_name(destination)}"
                    )
                except (OSError, TypeError, ValueError) as exc:
                    self.output.write(
                        f"[FL] MODEL_UPDATE send failed to "
                        f"{endpoint_name(destination)}: {exc}"
                    )

    def _send_mnist_image(self, path: Path, label: Optional[int] = None) -> None:
        if self.role != 0:
            self.output.write("[COMMAND] Only Sensor Nodes can send MNIST observations.")
            return
        if not self._is_associated() or self._active_cluster_head is None:
            self.output.write("[COMMAND] Sensor is not ASSOCIATED with a Cluster Head yet.")
            return
        path = self._resolve_image_path(path)
        if label is None:
            try:
                label = int(path.stem)
            except ValueError:
                self.output.write(
                    "[COMMAND] Could not infer the MNIST label; use a numeric "
                    "filename such as 2.png or provide [label]."
                )
                return
        if not 0 <= label <= 9:
            self.output.write("[COMMAND] MNIST label must be between 0 and 9.")
            return

        try:
            with Image.open(path) as image:
                pixels = image.convert("L").resize((28, 28)).tobytes()
        except (OSError, ValueError) as exc:
            self.output.write(f"[COMMAND] Could not read MNIST image: {exc}")
            return

        data_id = self._next_mnist_data_id
        self._next_mnist_data_id = (self._next_mnist_data_id + 1) & 0xFF
        try:
            self.communication.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": data_id,
                    "sensor_type": 1,
                    "payload": pixels + bytes([label]),
                },
                destination=self._active_cluster_head,
            )
        except (OSError, TypeError, ValueError) as exc:
            self.output.write(f"[COMMAND] MNIST observation was not sent: {exc}")
            return

        self.output.write(
            f"[COMMAND] queued MNIST observation data_id={data_id} "
            f"label={label} to={endpoint_name(self._active_cluster_head)}"
        )

    def _handle_neighbor_message(self, message: dict) -> None:
        packet_type = message.get("packet_type")
        source = message.get("source_address")
        if packet_type == PacketType.HELLO and self.role == 0:
            self._handle_hello(message, source)
        elif packet_type == PacketType.JOIN_ACCEPT and self.role == 0:
            neighbor = self.neighbor.get_neighbor(source[0], source[1])
            state = neighbor.association_state.value if neighbor else "UNKNOWN"
            self.output.write(
                f"[ASSOCIATION] JOIN_ACCEPT from={endpoint_name(source)} "
                f"rejected={message.get('rejected')} state={state}"
            )
        elif packet_type == PacketType.JOIN and self.role == 1:
            self.output.write(
                f"[ASSOCIATION] JOIN received from={endpoint_name(source)} "
                f"join_id={message.get('join_id')}"
            )
        elif packet_type == PacketType.LEAVE:
            self.output.write(f"[ASSOCIATION] LEAVE from={endpoint_name(source)}")

    def _handle_hello(self, message: dict, source: tuple[str, int]) -> None:
        if self._active_cluster_head is not None and source != self._active_cluster_head:
            return

        self._active_cluster_head = source
        neighbor = self.neighbor.get_neighbor(source[0], source[1])
        if neighbor is None:
            return

        if neighbor.association_state == AssociationState.DISCOVERED:
            join_id = self._next_join_id
            self._next_join_id = (self._next_join_id + 1) & 0xFFFF
            self.neighbor.start_join(
                ip_address=source[0],
                port=source[1],
                join_id=join_id,
                model_id=message.get("model_id"),
            )
        elif neighbor.association_state == AssociationState.JOIN_PENDING:
            join_id = neighbor.join_id
        elif neighbor.association_state == AssociationState.ASSOCIATED:
            # The thesis defines JOIN as a membership refresh after a Sensor
            # Node is associated. Reuse the current Join ID so repeated
            # refreshes remain idempotent if a JOIN_ACCEPT is lost.
            join_id = neighbor.join_id
            if join_id is None:
                join_id = self._next_join_id
                self._next_join_id = (self._next_join_id + 1) & 0xFFFF
            self.neighbor.start_join(
                ip_address=source[0],
                port=source[1],
                join_id=join_id,
                model_id=message.get("model_id"),
            )
        else:
            return

        if join_id is None:
            return
        self.output.write(
            f"[ASSOCIATION] sending JOIN to={endpoint_name(source)} "
            f"join_id={join_id}"
        )
        self.communication.send_message(
            {
                "packet_type": PacketType.JOIN,
                "role": 0,
                "model_id": message.get("model_id", self.model_id),
                "join_id": join_id,
            },
            destination=source,
        )

    def _is_associated(self) -> bool:
        if self._active_cluster_head is None:
            return False
        neighbor = self.neighbor.get_neighbor(*self._active_cluster_head)
        return bool(
            neighbor and neighbor.association_state == AssociationState.ASSOCIATED
        )

    @staticmethod
    def _encoding_for_path(path: Path) -> Optional[str]:
        suffix = path.suffix.lower()
        if suffix == ".png":
            return "png"
        if suffix in {".jpg", ".jpeg"}:
            return "jpeg"
        return None

    def _resolve_image_path(self, path: Path) -> Path:
        """Resolve relative image commands from the Sensor's send folder."""
        if path.is_absolute():
            return path
        send_path = self.send_dir / path
        if send_path.is_file():
            return send_path
        sample_path = REPOSITORY_ROOT / "tests" / "simulation" / "images" / path
        if sample_path.is_file():
            return sample_path
        return path

    def _send_images(self, paths: list[Path]) -> None:
        if self.role != 0:
            self.output.write("[COMMAND] Only Sensor Nodes can send images.")
            return
        if not self._is_associated():
            self.output.write("[COMMAND] Sensor is not ASSOCIATED with a Cluster Head yet.")
            return
        if self._active_cluster_head is None:
            return

        specifications = []
        for path in paths:
            path = self._resolve_image_path(path)
            if not path.is_file():
                self.output.write(f"[COMMAND] File not found: {path}")
                return
            try:
                file_bytes = path.read_bytes()
            except OSError as exc:
                self.output.write(f"[COMMAND] Could not read {path}: {exc}")
                return
            send_path = self.send_dir / path.name
            if send_path != path:
                try:
                    send_path.write_bytes(file_bytes)
                except OSError as exc:
                    self.output.write(f"[COMMAND] Could not stage {path}: {exc}")
                    return
            specifications.append(
                {
                    "logical_name": path.name,
                    "file_bytes": file_bytes,
                    "encoding": self._encoding_for_path(path),
                }
            )

        try:
            self.communication.send_file_batch(
                specifications,
                destination=self._active_cluster_head,
                sensor_type=1,
            )
        except (OSError, TypeError, ValueError) as exc:
            self.output.write(f"[COMMAND] Image transfer was not started: {exc}")
            return

        self.output.write(
            f"[COMMAND] queued {len(specifications)} image(s) "
            f"to={endpoint_name(self._active_cluster_head)}; "
            "FLAME assigned the data_id values."
        )

    def _print_neighbors(self) -> None:
        neighbors = self.neighbor.get_neighbors()
        if not neighbors:
            self.output.write("[NEIGHBORS] table is empty")
            return
        for neighbor in neighbors:
            self.output.write(
                f"[NEIGHBORS] {neighbor.ip_address}:{neighbor.port} "
                f"type={neighbor.node_role.value} "
                f"state={neighbor.association_state.value} "
                f"model_id={neighbor.model_id}"
            )

    def _print_status(self) -> None:
        self.output.write(
            f"[STATUS] role={self.role_name} "
            f"socket={endpoint_name(self.socket.address)} "
            f"active_cluster_head={endpoint_name(self._active_cluster_head)} "
            f"associated={self._is_associated()}"
        )
        if self.role == 1 and self.fl_module is not None:
            self.output.write(
                f"[FL] observations="
                f"{self.fl_module.dataset_manager.get_observation_count()}/"
                f"{self.expected_observations} "
                f"model_update_id={self.fl_module.model_manager.get_update_id()}"
            )
        self._print_neighbors()

    def _export_neighbors(self, path_text: Optional[str]) -> None:
        path = Path(path_text) if path_text else Path(
            f"neighbor_state_{self.socket.address[1]}.csv"
        )
        try:
            output_path = self.neighbor.export_csv(path)
            self.output.write(f"[CSV] exported neighbor table to {output_path}")
        except OSError as exc:
            self.output.write(f"[CSV] export failed: {exc}")

    def command_loop(self) -> None:
        self.output.write(
            "Commands: help, status, neighbors, send <image>, "
            "send-batch <image> [image...], send-mnist <image> <label>, "
            "export [csv_path], quit"
        )
        while not self._stop.is_set():
            self.output.begin_input()
            try:
                raw_command = input(TerminalOutput.PROMPT)
            except (EOFError, KeyboardInterrupt):
                self.output.end_input()
                self.output.write("[COMMAND] stopping")
                break
            self.output.end_input()
            if not raw_command.strip():
                continue
            try:
                parts = shlex.split(raw_command)
            except ValueError as exc:
                self.output.write(f"[COMMAND] invalid quoting: {exc}")
                continue

            command = parts[0].lower()
            if command in {"quit", "exit"}:
                break
            if command == "help":
                self.output.write(
                    "send <path> sends one PNG/JPEG; "
                    "send-batch <path> ... sends multiple files; "
                    "send-mnist <path> [label] sends a 785-byte labeled "
                    "MNIST observation; a numeric filename such as 2.png "
                    "supplies the label when [label] is omitted; "
                    "FLAME assigns data_id values; export [path] writes the "
                    "existing neighbor table."
                )
            elif command == "status":
                self._print_status()
            elif command == "neighbors":
                self._print_neighbors()
            elif command == "export":
                self._export_neighbors(parts[1] if len(parts) > 1 else None)
            elif command == "send":
                if len(parts) != 2:
                    self.output.write("Usage: send <image_path>")
                    continue
                self._send_images([Path(parts[1])])
            elif command == "send-batch":
                if len(parts) < 2:
                    self.output.write("Usage: send-batch <image_path> [image_path...]")
                    continue
                self._send_images([Path(path) for path in parts[1:]])
            elif command == "send-mnist":
                if len(parts) not in {2, 3}:
                    self.output.write("Usage: send-mnist <image_path> [label]")
                    continue
                label = None
                if len(parts) == 3:
                    try:
                        label = int(parts[2])
                    except ValueError:
                        self.output.write("Usage: send-mnist <image_path> [label]")
                        continue
                self._send_mnist_image(Path(parts[1]), label)
            elif command == "hello" and self.role == 1:
                try:
                    self._send_hello()
                except OSError as exc:
                    self.output.write(f"[ERROR] HELLO send failed: {exc}")
            else:
                self.output.write("[COMMAND] unknown command; type help")


def choose_role(requested_role: Optional[str]) -> int:
    if requested_role is not None:
        return 1 if requested_role == "cluster-head" else 0
    while True:
        answer = input("Select role [sensor/cluster-head]: ").strip().lower()
        if answer in {"sensor", "sensor-node", "0"}:
            return 0
        if answer in {"cluster-head", "cluster_head", "cluster head", "ch", "1"}:
            return 1
        print("Please choose sensor or cluster-head.", flush=True)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive FLAME simulation node")
    parser.add_argument(
        "--role",
        choices=("sensor", "cluster-head"),
        help="skip the role prompt",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="bind host; 0.0.0.0 enables Cluster Head broadcast HELLO",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="data port; Cluster Head defaults to 50000, Sensor Node to an ephemeral port",
    )
    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    parser.add_argument("--model-id", type=int, default=1)
    parser.add_argument("--hello-interval", type=float, default=DEFAULT_HELLO_INTERVAL)
    parser.add_argument("--expected-observations", type=int, default=10)
    parser.add_argument("--training-threshold", type=float, default=0.80)
    parser.add_argument("--aggregation-threshold", type=int, default=1)
    parser.add_argument("--neighbor-csv", help="Cluster Head shutdown CSV path")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help=(
            "node folder; Sensor Nodes use send/ and Cluster Heads use "
            "send/ plus received/"
        ),
    )
    return parser

def get_cluster_head_data_dir() -> Path:
    simulation_dir = Path(__file__).parent

    index = 1
    while True:
        data_dir = simulation_dir / f"cluster_head_{index}"

        if not data_dir.exists():
            return data_dir

        index += 1

def main() -> None:
    args = build_argument_parser().parse_args()
    role = choose_role(args.role)
    port = args.port if args.port is not None else 0
    if args.data_dir:
        data_dir = args.data_dir
    elif role == 1:
        data_dir = get_cluster_head_data_dir()
    else:
        data_dir = Path(__file__).with_name("sensor")
    node = InteractiveNode(
        role=role,
        host=args.host,
        port=port,
        discovery_port=args.discovery_port,
        model_id=args.model_id,
        hello_interval=args.hello_interval,
        neighbor_csv=args.neighbor_csv,
        data_dir=data_dir,
        expected_observations=args.expected_observations,
        training_threshold=args.training_threshold,
        aggregation_threshold=args.aggregation_threshold,
    )
    node.start()
    try:
        node.command_loop()
    finally:
        node.stop()

if __name__ == "__main__":
    main()
