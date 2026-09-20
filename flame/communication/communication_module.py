from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from message import PacketType
except ImportError:
    from .message import PacketType

try:
    from datagram_buffer import DatagramBuffer
    from parser import parse_datagram, serialize_message
    from reliability import ReliabilityManager
except ImportError:
    from .datagram_buffer import DatagramBuffer
    from .parser import parse_datagram, serialize_message
    from .reliability import ReliabilityManager


@dataclass
class ReceiveBuffer:
    items: List[dict] = field(default_factory=list)

    def put(self, item: dict) -> None:
        self.items.append(item)

    def get_all(self) -> List[dict]:
        items = list(self.items)
        self.items.clear()
        return items


class CommunicationModule:
    def __init__(
        self,
        socket_interface: Any,
        *,
        version: int = 1,
        buffer_size: int = 128,
        poll_interval: float = 0.01,
        auto_start: bool = True,
        poll_socket: bool = True,
        route_handler: Optional[Callable[[dict], None]] = None,
        reliability_manager: Optional[ReliabilityManager] = None,
    ) -> None:
        self.socket_interface = socket_interface
        self.version = version
        self.poll_interval = poll_interval
        self.poll_socket = poll_socket
        self.datagram_buffer = DatagramBuffer(max_size=buffer_size)
        self.receive_buffer = ReceiveBuffer()
        self.neighbor_buffer = ReceiveBuffer()
        self.sensor_buffer = ReceiveBuffer()
        self.fl_buffer = ReceiveBuffer()
        self.ack_buffer = ReceiveBuffer()
        self.route_handler = route_handler
        self.reliability_manager = reliability_manager or ReliabilityManager()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if auto_start:
            self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def send_message(self, message_obj: Any, destination: Optional[Tuple[str, int]] = None) -> bytes:
        payload = serialize_message(message_obj)
        if destination is None:
            destination = self._resolve_destination(message_obj)
        if destination is None:
            raise ValueError("Destination address is required for send_message().")
        self.socket_interface.sendto(payload, destination)
        return payload

    def receive_datagram(self, raw_bytes: bytes) -> None:
        self.datagram_buffer.put(raw_bytes)

    def ingest_socket_once(self) -> None:
        packet = self.socket_interface.recvfrom()
        if packet is None:
            return
        raw_bytes, _address = packet
        self.datagram_buffer.put(raw_bytes, _address)

    def _drain_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.poll_socket:
                self.ingest_socket_once()

            drained = self._drain_once()
            self._service_reliability()
            if not drained:
                time.sleep(self.poll_interval)

    def _drain_once(self) -> bool:
        did_work = False

        while not self.datagram_buffer.empty():
            raw_bytes = self.datagram_buffer.get()
            if raw_bytes is None:
                break

            source_address: Optional[Tuple[str, int]] = None
            if isinstance(raw_bytes, tuple):
                raw_bytes, source_address = raw_bytes

            did_work = True
            try:
                parsed = parse_datagram(raw_bytes)
            except Exception as exc:
                self.receive_buffer.put(
                    {
                        "type": "error",
                        "error": str(exc),
                        "raw": raw_bytes,
                    }
                )
                continue

            packet = parsed.payload
            self.receive_buffer.put(packet)
            self._route_packet(packet, source_address=source_address)

        return did_work

    def _route_packet(self, packet: dict, source_address: Optional[Tuple[str, int]] = None) -> None:
        packet_type = packet.get("packet_type")

        if packet_type in (PacketType.HELLO, PacketType.JOIN, PacketType.JOIN_ACCEPT, PacketType.LEAVE):
            self.neighbor_buffer.put(packet)
        elif packet_type == PacketType.SENSOR_DATA:
            self.sensor_buffer.put(packet)
        elif packet_type == PacketType.MODEL_UPDATE:
            self.fl_buffer.put(packet)
        elif packet_type in (PacketType.SENSOR_DATA_ACK, PacketType.MODEL_UPDATE_ACK):
            self.ack_buffer.put(packet)

        if self.route_handler is not None:
            self.route_handler(packet)

        if source_address is not None and self.reliability_manager is not None:
            self._observe_reliability(packet, source_address)

    def _observe_reliability(self, packet: dict, source_address: Tuple[str, int]) -> None:
        packet_type = packet.get("packet_type")
        message_id = packet.get("data_id", packet.get("update_id"))
        block_number = packet.get("block_number")

        if packet_type in (PacketType.SENSOR_DATA, PacketType.MODEL_UPDATE):
            if message_id is None or block_number is None:
                return
            self.reliability_manager.process_received_fragment(
                source_address=str(source_address),
                packet_type=packet_type.name,
                message_id=message_id,
                block_number=block_number,
                fragment=packet,
            )

    def _service_reliability(self) -> None:
        if self.reliability_manager is None:
            return

        for key in list(self.reliability_manager.outbound_transfers.keys()):
            destination_address, packet_type, message_id = key
            if self.reliability_manager.is_timeout(destination_address, packet_type, message_id):
                self.reliability_manager.process_timeout(destination_address, packet_type, message_id)

    def _resolve_destination(self, message_obj: Any) -> Optional[Tuple[str, int]]:
        if isinstance(message_obj, dict):
            return message_obj.get("destination") or message_obj.get("destination_address")
        return getattr(message_obj, "destination", None) or getattr(message_obj, "destination_address", None)

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