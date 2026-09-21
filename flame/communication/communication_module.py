from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    from datagram_buffer import DatagramBuffer
    from message import PacketType, fragment_payload, reassemble_fragments
    from parser import ROLE_RESTRICTIONS, parse_datagram, serialize_message
    from reliability import AckInfo, ReliabilityManager, TransferKey, TransmissionAction
except ImportError:
    from .datagram_buffer import DatagramBuffer
    from .message import PacketType, fragment_payload, reassemble_fragments
    from .parser import ROLE_RESTRICTIONS, parse_datagram, serialize_message
    from .reliability import AckInfo, ReliabilityManager, TransferKey, TransmissionAction


Address = Tuple[str, int]

RECEIVER_ROLE_RESTRICTIONS = {
    PacketType.HELLO: {0, 1},
    PacketType.JOIN: {1},
    PacketType.JOIN_ACCEPT: {0},
    PacketType.LEAVE: {1},
    PacketType.SENSOR_DATA: {1},
    PacketType.SENSOR_DATA_ACK: {0},
    PacketType.MODEL_UPDATE: {1},
    PacketType.MODEL_UPDATE_ACK: {1},
}


class ReceiveBuffer:
    """Small thread-safe FIFO used at the application boundary."""

    def __init__(self) -> None:
        self._items: Deque[dict] = deque()
        self._lock = threading.Lock()

    @property
    def items(self) -> List[dict]:
        with self._lock:
            return list(self._items)

    def put(self, item: dict) -> None:
        with self._lock:
            self._items.append(item)

    def get_all(self) -> List[dict]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class CommunicationModule:
    """Coordinates FLAME serialization, reliability, UDP I/O, and delivery."""

    def __init__(
        self,
        socket_interface: Any,
        *,
        role: Optional[int] = None,
        version: int = 1,
        buffer_size: int = 128,
        poll_interval: float = 0.01,
        auto_start: bool = True,
        poll_socket: bool = True,
        route_handler: Optional[Callable[[dict], None]] = None,
        event_handler: Optional[Callable[[dict], None]] = None,
        reliability_manager: Optional[ReliabilityManager] = None,
        initial_window_size: int = 2,
        max_window_size: int = 8,
        ack_timeout: float = 0.25,
        ack_delay: float = 0.04,
        reassembly_timeout: float = 5.0,
        completed_retention: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        if role not in (None, 0, 1):
            raise ValueError("Role must be 0, 1, or None.")
        if not 0 <= version <= 0x0F:
            raise ValueError("Version must fit within 4 bits.")

        self.socket_interface = socket_interface
        self.role = role
        self.version = version
        self.poll_interval = poll_interval
        self.poll_socket = poll_socket
        self.datagram_buffer = DatagramBuffer(max_size=buffer_size)
        self.receive_buffer = ReceiveBuffer()
        self.neighbor_buffer = ReceiveBuffer()
        self.sensor_buffer = ReceiveBuffer()
        self.fl_buffer = ReceiveBuffer()
        self.ack_buffer = ReceiveBuffer()
        self.error_buffer = ReceiveBuffer()
        self.event_buffer = ReceiveBuffer()
        self.route_handler = route_handler
        self.event_handler = event_handler
        self.reliability_manager = reliability_manager or ReliabilityManager(
            initial_window_size=initial_window_size,
            min_window_size=2,
            max_window_size=max_window_size,
            ack_timeout=ack_timeout,
            ack_delay=ack_delay,
            reassembly_timeout=reassembly_timeout,
            completed_retention=completed_retention,
            max_retries=max_retries,
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._sequence_lock = threading.Lock()
        self._next_sequence = 0

        if auto_start:
            self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._drain_loop,
            name="flame-communication",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _allocate_sequence(self) -> int:
        with self._sequence_lock:
            sequence = self._next_sequence
            self._next_sequence = (self._next_sequence + 1) & 0xFFFF
            return sequence

    @staticmethod
    def _as_dict(message_obj: Any) -> Dict[str, Any]:
        if isinstance(message_obj, dict):
            return dict(message_obj)
        try:
            return dict(vars(message_obj))
        except TypeError as exc:
            raise TypeError("Message must be a dictionary or an object with attributes.") from exc

    @staticmethod
    def _packet_type(value: Any) -> PacketType:
        if isinstance(value, PacketType):
            return value
        if isinstance(value, str):
            return PacketType[value]
        return PacketType(value)

    def _validate_outgoing_role(self, packet_type: PacketType, role: int) -> None:
        if role not in ROLE_RESTRICTIONS.get(packet_type, {0, 1}):
            raise ValueError(f"role {role} is not permitted to send {packet_type.name}")
        if self.role is not None and role != self.role:
            raise ValueError(
                f"Message role {role} does not match CommunicationModule role {self.role}."
            )

    @staticmethod
    def _validate_destination(destination: Address) -> Address:
        if (
            not isinstance(destination, tuple)
            or len(destination) != 2
            or not isinstance(destination[0], str)
            or not isinstance(destination[1], int)
            or not 0 <= destination[1] <= 0xFFFF
        ):
            raise ValueError("Destination must be a (host, port) tuple.")
        return destination

    def send_message(
        self,
        message_obj: Any,
        destination: Optional[Address] = None,
    ) -> bytes:
        packet = self._as_dict(message_obj)
        if "packet_type" not in packet:
            raise ValueError("Message requires a packet_type.")
        packet_type = self._packet_type(packet["packet_type"])
        packet["packet_type"] = packet_type
        packet.setdefault("version", self.version)
        if packet["version"] != self.version:
            raise ValueError("Message version does not match the CommunicationModule version.")

        if destination is None:
            destination = self._resolve_destination(packet)
        if destination is None:
            raise ValueError("Destination address is required for send_message().")
        destination = self._validate_destination(destination)

        role = packet.get("role", self.role)
        if role is None:
            role = 0
        packet["role"] = role
        self._validate_outgoing_role(packet_type, role)

        sequence_number = packet.get("sequence_number")
        if sequence_number is None:
            sequence_number = self._allocate_sequence()
        if not isinstance(sequence_number, int) or not 0 <= sequence_number <= 0xFFFF:
            raise ValueError("Sequence number must fit within 16 bits.")
        packet["sequence_number"] = sequence_number

        if packet_type in (PacketType.SENSOR_DATA, PacketType.MODEL_UPDATE):
            return self._send_reliable_message(packet, destination)

        raw = serialize_message(packet)
        self._send_raw(raw, destination)
        self._emit(
            "control_sent",
            packet_type=packet_type,
            sequence_number=sequence_number,
            destination=destination,
        )
        return raw

    def _send_reliable_message(self, packet: Dict[str, Any], destination: Address) -> bytes:
        packet_type = packet["packet_type"]
        id_field = "data_id" if packet_type == PacketType.SENSOR_DATA else "update_id"
        if id_field not in packet:
            raise ValueError(f"{packet_type.name} requires {id_field}.")
        message_id = packet[id_field]
        if not isinstance(message_id, int) or not 0 <= message_id <= 0xFF:
            raise ValueError("Reliable message identifier must fit within 8 bits.")

        payload = packet.get("payload", b"")
        fragments = fragment_payload(payload)
        prepared: Dict[int, bytes] = {}
        last_block_number = fragments[-1][1]
        for block_number, _last_block, fragment in fragments:
            fragment_packet = dict(packet)
            fragment_packet["block_number"] = block_number
            fragment_packet["last_block_number"] = last_block_number
            fragment_packet["payload"] = fragment
            prepared[block_number] = serialize_message(fragment_packet)

        with self._state_lock:
            key = self.reliability_manager.register_transfer(
                destination_address=destination,
                packet_type=packet_type,
                message_id=message_id,
                sequence_number=packet["sequence_number"],
                fragments=prepared,
            )
            action = self.reliability_manager.start_transfer(key)

        self._emit(
            "transfer_started",
            packet_type=packet_type,
            message_id=message_id,
            sequence_number=packet["sequence_number"],
            destination=destination,
            fragment_count=len(prepared),
        )
        self._handle_transmission_action(action)
        return prepared[0]

    def _send_raw(self, raw: bytes, destination: Address) -> None:
        with self._send_lock:
            self.socket_interface.sendto(raw, destination)

    def receive_datagram(
        self,
        raw_bytes: bytes,
        source_address: Optional[Address] = None,
    ) -> None:
        self.datagram_buffer.put(raw_bytes, source_address)

    def ingest_socket_once(self) -> bool:
        if (
            self.poll_socket
            and self._thread
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            return False

        with self._receive_lock:
            packet = self.socket_interface.recvfrom()
        if packet is None:
            return False
        raw_bytes, address = packet
        self.datagram_buffer.put(raw_bytes, address)
        return True

    def _drain_loop(self) -> None:
        while not self._stop_event.is_set():
            did_work = False
            try:
                if self.poll_socket:
                    did_work = self.ingest_socket_once() or did_work
                did_work = self._drain_once() or did_work
                did_work = self._service_reliability() or did_work
            except OSError as exc:
                if not self._stop_event.is_set():
                    self._record_error(str(exc), b"")
            except Exception as exc:
                self._record_error(f"communication loop error: {exc}", b"")

            if not did_work:
                time.sleep(self.poll_interval)

    def _drain_once(self) -> bool:
        did_work = False
        while True:
            item = self.datagram_buffer.get()
            if item is None:
                break
            did_work = True
            source_address: Optional[Address] = None
            raw_bytes = item
            if isinstance(item, tuple):
                raw_bytes, source_address = item

            try:
                parsed = parse_datagram(raw_bytes, expected_version=self.version)
                self._handle_parsed_packet(dict(parsed.payload), source_address)
            except Exception as exc:
                self._record_error(str(exc), raw_bytes, source_address)
        return did_work

    def _handle_parsed_packet(
        self,
        packet: dict,
        source_address: Optional[Address],
    ) -> None:
        packet_type = packet["packet_type"]
        if (
            self.role is not None
            and self.role not in RECEIVER_ROLE_RESTRICTIONS.get(packet_type, {0, 1})
        ):
            raise ValueError(
                f"role {self.role} is not permitted to receive {packet_type.name}"
            )
        if packet_type in (PacketType.SENSOR_DATA_ACK, PacketType.MODEL_UPDATE_ACK):
            if source_address is None:
                raise ValueError("ACK packets require a source address.")
            self._consume_ack(packet, source_address)
            return

        if packet_type in (PacketType.SENSOR_DATA, PacketType.MODEL_UPDATE):
            if source_address is None:
                raise ValueError("Reliable data packets require a source address.")
            self._consume_reliable_fragment(packet, source_address)
            return

        if source_address is not None:
            packet["source_address"] = source_address
        self._deliver(packet)

    def _consume_reliable_fragment(self, packet: dict, source_address: Address) -> None:
        with self._state_lock:
            result = self.reliability_manager.receive_fragment(source_address, packet)

        self._emit(
            "fragment_duplicate" if result.duplicate else "fragment_received",
            packet_type=packet["packet_type"],
            message_id=result.key.message_id,
            sequence_number=result.key.sequence_number,
            block_number=packet["block_number"],
            last_block_number=packet["last_block_number"],
            source=source_address,
        )

        for ack in result.acknowledgements:
            self._send_ack(result.key, ack)

        if result.complete_fragments is None:
            return

        complete = dict(result.complete_fragments[0])
        complete["payload"] = reassemble_fragments(result.complete_fragments)
        complete["block_number"] = 0
        complete["source_address"] = source_address
        self._emit(
            "message_reassembled",
            packet_type=complete["packet_type"],
            message_id=result.key.message_id,
            sequence_number=result.key.sequence_number,
            fragment_count=len(result.complete_fragments),
            source=source_address,
        )
        self._deliver(complete)

    def _send_ack(self, key: TransferKey, ack: AckInfo) -> None:
        if key.packet_type == PacketType.SENSOR_DATA:
            packet_type = PacketType.SENSOR_DATA_ACK
            id_field = "data_id"
        else:
            packet_type = PacketType.MODEL_UPDATE_ACK
            id_field = "update_id"

        packet = {
            "packet_type": packet_type,
            "version": self.version,
            "role": 1,
            "sequence_number": ack.sequence_number,
            id_field: ack.message_id,
            "window_start": ack.window_start,
            "window_end": ack.window_end,
            "received_blocks": list(ack.received_blocks),
        }
        raw = serialize_message(packet)
        self._send_raw(raw, key.peer_address)
        self._emit(
            "ack_sent",
            packet_type=packet_type,
            message_id=ack.message_id,
            sequence_number=ack.sequence_number,
            window_start=ack.window_start,
            window_end=ack.window_end,
            received_blocks=list(ack.received_blocks),
            destination=key.peer_address,
        )

    def _consume_ack(self, packet: dict, source_address: Address) -> None:
        ack_type = packet["packet_type"]
        if ack_type == PacketType.SENSOR_DATA_ACK:
            data_type = PacketType.SENSOR_DATA
            message_id = packet["data_id"]
        else:
            data_type = PacketType.MODEL_UPDATE
            message_id = packet["update_id"]

        diagnostic = dict(packet)
        diagnostic["source_address"] = source_address
        self.ack_buffer.put(diagnostic)
        self._emit(
            "ack_received",
            packet_type=ack_type,
            message_id=message_id,
            sequence_number=packet["sequence_number"],
            window_start=packet["window_start"],
            window_end=packet["window_end"],
            received_blocks=list(packet["received_blocks"]),
            source=source_address,
        )

        with self._state_lock:
            action = self.reliability_manager.process_ack(
                source_address=source_address,
                packet_type=data_type,
                message_id=message_id,
                sequence_number=packet["sequence_number"],
                window_start=packet["window_start"],
                window_end=packet["window_end"],
                received_blocks=packet["received_blocks"],
            )
        self._handle_transmission_action(action)

    def _handle_transmission_action(self, action: TransmissionAction) -> None:
        if action.failed:
            self._emit(
                "transfer_failed",
                packet_type=action.key.packet_type,
                message_id=action.key.message_id,
                sequence_number=action.key.sequence_number,
                destination=action.key.peer_address,
                reason=action.reason,
            )
            return
        if action.completed:
            self._emit(
                "transfer_complete",
                packet_type=action.key.packet_type,
                message_id=action.key.message_id,
                sequence_number=action.key.sequence_number,
                destination=action.key.peer_address,
            )
            return
        if not action.datagrams:
            return

        if action.window_advanced:
            self._emit(
                "window_advanced",
                packet_type=action.key.packet_type,
                message_id=action.key.message_id,
                sequence_number=action.key.sequence_number,
                window_start=action.window_start,
                window_end=action.window_end,
                destination=action.key.peer_address,
            )

        for block_number, raw in action.datagrams:
            self._send_raw(raw, action.key.peer_address)
            self._emit(
                "fragment_sent",
                packet_type=action.key.packet_type,
                message_id=action.key.message_id,
                sequence_number=action.key.sequence_number,
                block_number=block_number,
                window_start=action.window_start,
                window_end=action.window_end,
                destination=action.key.peer_address,
                reason=action.reason,
            )

    def _service_reliability(self) -> bool:
        with self._state_lock:
            outbound_actions = self.reliability_manager.service_outbound()
            pending_acks = self.reliability_manager.service_inbound()
            expired = self.reliability_manager.cleanup()

        for action in outbound_actions:
            self._handle_transmission_action(action)
        for key, ack in pending_acks:
            self._send_ack(key, ack)
        for key in expired:
            self._emit(
                "reassembly_expired",
                packet_type=key.packet_type,
                message_id=key.message_id,
                sequence_number=key.sequence_number,
                source=key.peer_address,
            )
        return bool(outbound_actions or pending_acks or expired)

    def _deliver(self, packet: dict) -> None:
        self.receive_buffer.put(packet)
        packet_type = packet["packet_type"]
        if packet_type in (
            PacketType.HELLO,
            PacketType.JOIN,
            PacketType.JOIN_ACCEPT,
            PacketType.LEAVE,
        ):
            self.neighbor_buffer.put(packet)
        elif packet_type == PacketType.SENSOR_DATA:
            self.sensor_buffer.put(packet)
        elif packet_type == PacketType.MODEL_UPDATE:
            self.fl_buffer.put(packet)

        if self.route_handler is not None:
            self.route_handler(packet)
        self._emit(
            "message_delivered",
            packet_type=packet_type,
            sequence_number=packet["sequence_number"],
            source=packet.get("source_address"),
        )

    def _record_error(
        self,
        error: str,
        raw: bytes,
        source_address: Optional[Address] = None,
    ) -> None:
        item = {
            "type": "error",
            "error": error,
            "raw": raw,
            "source_address": source_address,
        }
        self.error_buffer.put(item)
        self._emit("datagram_error", error=error, source=source_address)

    def _emit(self, event_type: str, **fields: Any) -> None:
        event = {"type": event_type, **fields}
        self.event_buffer.put(event)
        if self.event_handler is not None:
            try:
                self.event_handler(event)
            except Exception:
                # Observability must not break protocol progress.
                pass

    @staticmethod
    def _resolve_destination(message_obj: Any) -> Optional[Address]:
        if isinstance(message_obj, dict):
            return message_obj.get("destination") or message_obj.get("destination_address")
        return getattr(message_obj, "destination", None) or getattr(
            message_obj,
            "destination_address",
            None,
        )

    def pop_received(self) -> List[dict]:
        return self.receive_buffer.get_all()

    def pop_neighbor_messages(self) -> List[dict]:
        return self.neighbor_buffer.get_all()

    def pop_sensor_messages(self) -> List[dict]:
        return self.sensor_buffer.get_all()

    def pop_fl_messages(self) -> List[dict]:
        return self.fl_buffer.get_all()

    def pop_ack_messages(self) -> List[dict]:
        return self.ack_buffer.get_all()

    def pop_errors(self) -> List[dict]:
        return self.error_buffer.get_all()

    def pop_events(self) -> List[dict]:
        return self.event_buffer.get_all()
