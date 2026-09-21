from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from message import PacketType
except ImportError:
    from .message import PacketType


Address = Tuple[str, int]

MIN_WINDOW_SIZE = 2
DEFAULT_INITIAL_WINDOW_SIZE = 2
DEFAULT_MAX_WINDOW_SIZE = 8
DEFAULT_ACK_TIMEOUT = 0.25
DEFAULT_ACK_DELAY = 0.04
DEFAULT_REASSEMBLY_TIMEOUT = 5.0
DEFAULT_COMPLETED_RETENTION = 2.0
DEFAULT_MAX_RETRIES = 3


def prepare_fragments(fragments):
    """Index `(block, last_block, payload)` tuples by block number."""
    return {
        block_number: fragment
        for block_number, _last_block_number, fragment in fragments
    }


@dataclass(frozen=True)
class TransferKey:
    peer_address: Address
    packet_type: PacketType
    message_id: int
    sequence_number: int


@dataclass(frozen=True)
class AckInfo:
    message_id: int
    sequence_number: int
    window_start: int
    window_end: int
    received_blocks: Tuple[int, ...]


@dataclass
class TransmissionAction:
    key: TransferKey
    reason: str
    datagrams: List[Tuple[int, bytes]] = field(default_factory=list)
    completed: bool = False
    failed: bool = False
    window_advanced: bool = False
    window_start: Optional[int] = None
    window_end: Optional[int] = None


@dataclass
class InboundResult:
    key: TransferKey
    accepted: bool
    duplicate: bool = False
    acknowledgements: List[AckInfo] = field(default_factory=list)
    complete_fragments: Optional[List[dict]] = None


@dataclass
class OutboundTransfer:
    key: TransferKey
    fragments: Dict[int, bytes]
    window_start: int
    window_end: int
    window_size: int
    min_window_size: int
    max_window_size: int
    max_retries: int
    acknowledged_blocks: set[int] = field(default_factory=set)
    retry_count: int = 0
    deadline: Optional[float] = None
    loss_in_window: bool = False
    reduced_window_size: Optional[int] = None
    last_ack_signature: Optional[Tuple[int, ...]] = None

    @property
    def last_block_number(self) -> int:
        return len(self.fragments) - 1

    def current_blocks(self) -> List[int]:
        return list(range(self.window_start, self.window_end + 1))


@dataclass
class InboundTransfer:
    key: TransferKey
    last_block_number: int
    metadata_value: int
    window_start: int
    window_end: int
    window_size: int
    min_window_size: int
    max_window_size: int
    created_at: float
    last_activity: float
    fragments: Dict[int, dict] = field(default_factory=dict)
    ack_deadline: Optional[float] = None
    loss_in_window: bool = False
    reduced_window_size: Optional[int] = None
    ack_history: Dict[Tuple[int, int], AckInfo] = field(default_factory=dict)

    def current_blocks(self) -> List[int]:
        return list(range(self.window_start, self.window_end + 1))


@dataclass
class CompletedInboundTransfer:
    expires_at: float
    ack_history: Dict[Tuple[int, int], AckInfo]


class ReliabilityManager:
    """Own reliable-transfer state without performing socket I/O."""

    def __init__(
        self,
        *,
        initial_window_size: int = DEFAULT_INITIAL_WINDOW_SIZE,
        min_window_size: int = MIN_WINDOW_SIZE,
        max_window_size: int = DEFAULT_MAX_WINDOW_SIZE,
        ack_timeout: float = DEFAULT_ACK_TIMEOUT,
        ack_delay: float = DEFAULT_ACK_DELAY,
        reassembly_timeout: float = DEFAULT_REASSEMBLY_TIMEOUT,
        completed_retention: float = DEFAULT_COMPLETED_RETENTION,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if min_window_size < 2:
            raise ValueError("FLAME's minimum window size is 2.")
        if not min_window_size <= initial_window_size <= max_window_size:
            raise ValueError("Initial window size must be between the minimum and maximum.")
        if ack_timeout <= 0 or ack_delay <= 0:
            raise ValueError("ACK timeout and ACK delay must be positive.")
        if ack_delay >= ack_timeout:
            raise ValueError("ACK delay must be shorter than the sender ACK timeout.")
        if reassembly_timeout <= 0 or completed_retention <= 0:
            raise ValueError("Reliability cleanup intervals must be positive.")
        if max_retries < 0:
            raise ValueError("Maximum retries cannot be negative.")
        if completed_retention < ack_timeout * (max_retries + 1):
            raise ValueError(
                "Completed-transfer retention must cover the sender retry horizon."
            )

        self.initial_window_size = initial_window_size
        self.min_window_size = min_window_size
        self.max_window_size = max_window_size
        self.ack_timeout = ack_timeout
        self.ack_delay = ack_delay
        self.reassembly_timeout = reassembly_timeout
        self.completed_retention = completed_retention
        self.max_retries = max_retries

        self.outbound_transfers: Dict[TransferKey, OutboundTransfer] = {}
        self.inbound_transfers: Dict[TransferKey, InboundTransfer] = {}
        self.completed_inbound: Dict[TransferKey, CompletedInboundTransfer] = {}

    @staticmethod
    def _packet_type(packet_type) -> PacketType:
        if isinstance(packet_type, PacketType):
            return packet_type
        if isinstance(packet_type, str):
            return PacketType[packet_type]
        return PacketType(packet_type)

    @classmethod
    def make_key(
        cls,
        peer_address: Address,
        packet_type,
        message_id: int,
        sequence_number: int,
    ) -> TransferKey:
        return TransferKey(
            peer_address=tuple(peer_address),
            packet_type=cls._packet_type(packet_type),
            message_id=message_id,
            sequence_number=sequence_number,
        )

    def register_transfer(
        self,
        destination_address: Address,
        packet_type,
        message_id: int,
        sequence_number: int,
        fragments: Dict[int, bytes],
        *,
        window_size: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> TransferKey:
        packet_type = self._packet_type(packet_type)
        if packet_type not in (PacketType.SENSOR_DATA, PacketType.MODEL_UPDATE):
            raise ValueError("Only SENSOR_DATA and MODEL_UPDATE are reliable transfers.")
        if not 0 <= message_id <= 0xFF:
            raise ValueError("Message ID must fit within 8 bits.")
        if not 0 <= sequence_number <= 0xFFFF:
            raise ValueError("Transfer sequence must fit within 16 bits.")
        if not fragments:
            raise ValueError("A reliable transfer requires at least one fragment.")
        if sorted(fragments) != list(range(len(fragments))):
            raise ValueError("Reliable fragments must be consecutively numbered from zero.")

        size = self.initial_window_size if window_size is None else window_size
        if not self.min_window_size <= size <= self.max_window_size:
            raise ValueError("Window size is outside the configured bounds.")

        key = self.make_key(destination_address, packet_type, message_id, sequence_number)
        if key in self.outbound_transfers:
            raise ValueError("An identical reliable transfer is already active.")

        self.outbound_transfers[key] = OutboundTransfer(
            key=key,
            fragments=dict(fragments),
            window_start=0,
            window_end=min(size - 1, len(fragments) - 1),
            window_size=size,
            min_window_size=self.min_window_size,
            max_window_size=self.max_window_size,
            max_retries=self.max_retries if max_retries is None else max_retries,
        )
        return key

    def start_transfer(self, key: TransferKey, now: Optional[float] = None) -> TransmissionAction:
        transfer = self.outbound_transfers[key]
        return self._transmission_action(
            transfer,
            transfer.current_blocks(),
            reason="initial_window",
            now=now,
        )

    def _transmission_action(
        self,
        transfer: OutboundTransfer,
        blocks: Iterable[int],
        *,
        reason: str,
        now: Optional[float] = None,
        window_advanced: bool = False,
    ) -> TransmissionAction:
        now = time.monotonic() if now is None else now
        block_list = list(blocks)
        transfer.deadline = now + self.ack_timeout
        return TransmissionAction(
            key=transfer.key,
            reason=reason,
            datagrams=[(block, transfer.fragments[block]) for block in block_list],
            window_advanced=window_advanced,
            window_start=transfer.window_start,
            window_end=transfer.window_end,
        )

    def process_ack(
        self,
        source_address: Address,
        packet_type,
        message_id: int,
        sequence_number: int,
        window_start: int,
        window_end: int,
        received_blocks: Iterable[int],
        *,
        now: Optional[float] = None,
    ) -> TransmissionAction:
        key = self.make_key(source_address, packet_type, message_id, sequence_number)
        transfer = self.outbound_transfers.get(key)
        if transfer is None:
            return TransmissionAction(key=key, reason="unknown_ack")
        if (window_start, window_end) != (transfer.window_start, transfer.window_end):
            return TransmissionAction(key=key, reason="stale_or_wrong_window_ack")

        received = tuple(received_blocks)
        if any(block < window_start or block > window_end for block in received):
            return TransmissionAction(key=key, reason="invalid_ack")

        signature = (window_start, window_end, *received)
        if signature == transfer.last_ack_signature:
            return TransmissionAction(key=key, reason="duplicate_ack")
        transfer.last_ack_signature = signature
        transfer.acknowledged_blocks.update(received)

        expected = set(transfer.current_blocks())
        missing = sorted(expected - transfer.acknowledged_blocks)
        if not missing:
            previous_start = transfer.window_start
            previous_end = transfer.window_end
            transfer.deadline = None
            next_start = previous_end + 1

            if next_start > transfer.last_block_number:
                del self.outbound_transfers[key]
                return TransmissionAction(
                    key=key,
                    reason="transfer_complete",
                    completed=True,
                    window_advanced=True,
                    window_start=previous_start,
                    window_end=previous_end,
                )

            if transfer.loss_in_window:
                next_size = transfer.reduced_window_size or transfer.min_window_size
            else:
                next_size = min(transfer.window_size + 1, transfer.max_window_size)

            transfer.window_size = next_size
            transfer.window_start = next_start
            transfer.window_end = min(
                next_start + next_size - 1,
                transfer.last_block_number,
            )
            transfer.retry_count = 0
            transfer.loss_in_window = False
            transfer.reduced_window_size = None
            transfer.last_ack_signature = None
            return self._transmission_action(
                transfer,
                transfer.current_blocks(),
                reason="next_window",
                now=now,
                window_advanced=True,
            )

        transfer.loss_in_window = True
        transfer.reduced_window_size = max(
            transfer.min_window_size,
            transfer.window_size // 2,
        )
        return self._retry_action(transfer, missing, reason="selective_retransmission", now=now)

    def _retry_action(
        self,
        transfer: OutboundTransfer,
        missing_blocks: Iterable[int],
        *,
        reason: str,
        now: Optional[float] = None,
    ) -> TransmissionAction:
        if transfer.retry_count >= transfer.max_retries:
            del self.outbound_transfers[transfer.key]
            return TransmissionAction(
                key=transfer.key,
                reason="retry_exhausted",
                failed=True,
                window_start=transfer.window_start,
                window_end=transfer.window_end,
            )

        transfer.retry_count += 1
        return self._transmission_action(
            transfer,
            missing_blocks,
            reason=reason,
            now=now,
        )

    def service_outbound(self, now: Optional[float] = None) -> List[TransmissionAction]:
        now = time.monotonic() if now is None else now
        actions: List[TransmissionAction] = []
        for transfer in list(self.outbound_transfers.values()):
            if transfer.deadline is None or now < transfer.deadline:
                continue
            transfer.loss_in_window = True
            transfer.reduced_window_size = max(
                transfer.min_window_size,
                transfer.window_size // 2,
            )
            missing = [
                block
                for block in transfer.current_blocks()
                if block not in transfer.acknowledged_blocks
            ]
            actions.append(
                self._retry_action(
                    transfer,
                    missing,
                    reason="timeout_retransmission",
                    now=now,
                )
            )
        return actions

    def receive_fragment(
        self,
        source_address: Address,
        packet: dict,
        *,
        now: Optional[float] = None,
    ) -> InboundResult:
        now = time.monotonic() if now is None else now
        packet_type = self._packet_type(packet["packet_type"])
        message_id = packet.get("data_id", packet.get("update_id"))
        sequence_number = packet["sequence_number"]
        block_number = packet["block_number"]
        last_block_number = packet["last_block_number"]
        metadata_value = packet.get("sensor_type", packet.get("model_id"))
        key = self.make_key(source_address, packet_type, message_id, sequence_number)

        completed = self.completed_inbound.get(key)
        if completed is not None:
            ack = self._ack_for_block(completed.ack_history, block_number)
            return InboundResult(
                key=key,
                accepted=False,
                duplicate=True,
                acknowledgements=[ack] if ack else [],
            )

        transfer = self.inbound_transfers.get(key)
        if transfer is None:
            transfer = InboundTransfer(
                key=key,
                last_block_number=last_block_number,
                metadata_value=metadata_value,
                window_start=0,
                window_end=min(self.initial_window_size - 1, last_block_number),
                window_size=self.initial_window_size,
                min_window_size=self.min_window_size,
                max_window_size=self.max_window_size,
                created_at=now,
                last_activity=now,
            )
            self.inbound_transfers[key] = transfer
        elif (
            transfer.last_block_number != last_block_number
            or transfer.metadata_value != metadata_value
        ):
            raise ValueError("Fragment metadata is inconsistent with the active transfer.")

        transfer.last_activity = now
        if block_number in transfer.fragments:
            ack = self._ack_for_duplicate(transfer, block_number)
            if ack and transfer.window_start <= block_number <= transfer.window_end:
                self._mark_inbound_loss(transfer)
            return InboundResult(
                key=key,
                accepted=False,
                duplicate=True,
                acknowledgements=[ack] if ack else [],
            )

        transfer.fragments[block_number] = dict(packet)
        transfer.ack_deadline = now + self.ack_delay
        acknowledgements = self._advance_complete_inbound_windows(transfer)

        complete_fragments = None
        if len(transfer.fragments) == transfer.last_block_number + 1:
            complete_fragments = [
                transfer.fragments[block]
                for block in range(transfer.last_block_number + 1)
            ]
            self.completed_inbound[key] = CompletedInboundTransfer(
                expires_at=now + self.completed_retention,
                ack_history=dict(transfer.ack_history),
            )
            del self.inbound_transfers[key]

        return InboundResult(
            key=key,
            accepted=True,
            acknowledgements=acknowledgements,
            complete_fragments=complete_fragments,
        )

    def _advance_complete_inbound_windows(self, transfer: InboundTransfer) -> List[AckInfo]:
        acknowledgements: List[AckInfo] = []
        while transfer.window_start <= transfer.last_block_number:
            expected = set(transfer.current_blocks())
            if not expected.issubset(transfer.fragments):
                break

            ack = self._current_ack(transfer)
            transfer.ack_history[(transfer.window_start, transfer.window_end)] = ack
            acknowledgements.append(ack)
            old_end = transfer.window_end

            if transfer.loss_in_window:
                next_size = transfer.reduced_window_size or transfer.min_window_size
            else:
                next_size = min(transfer.window_size + 1, transfer.max_window_size)

            transfer.window_size = next_size
            transfer.window_start = old_end + 1
            transfer.window_end = min(
                transfer.window_start + next_size - 1,
                transfer.last_block_number,
            )
            transfer.loss_in_window = False
            transfer.reduced_window_size = None
            transfer.ack_deadline = None

        return acknowledgements

    def _current_ack(self, transfer: InboundTransfer) -> AckInfo:
        received = tuple(
            block
            for block in transfer.current_blocks()
            if block in transfer.fragments
        )
        return AckInfo(
            message_id=transfer.key.message_id,
            sequence_number=transfer.key.sequence_number,
            window_start=transfer.window_start,
            window_end=transfer.window_end,
            received_blocks=received,
        )

    @staticmethod
    def _ack_for_block(
        history: Dict[Tuple[int, int], AckInfo],
        block_number: int,
    ) -> Optional[AckInfo]:
        for (window_start, window_end), ack in history.items():
            if window_start <= block_number <= window_end:
                return ack
        return None

    def _ack_for_duplicate(
        self,
        transfer: InboundTransfer,
        block_number: int,
    ) -> Optional[AckInfo]:
        historical = self._ack_for_block(transfer.ack_history, block_number)
        if historical is not None:
            return historical
        if transfer.window_start <= block_number <= transfer.window_end:
            return self._current_ack(transfer)
        return None

    @staticmethod
    def _mark_inbound_loss(transfer: InboundTransfer) -> None:
        transfer.loss_in_window = True
        transfer.reduced_window_size = max(
            transfer.min_window_size,
            transfer.window_size // 2,
        )

    def service_inbound(self, now: Optional[float] = None) -> List[Tuple[TransferKey, AckInfo]]:
        now = time.monotonic() if now is None else now
        acknowledgements: List[Tuple[TransferKey, AckInfo]] = []
        for transfer in self.inbound_transfers.values():
            if transfer.ack_deadline is None or now < transfer.ack_deadline:
                continue
            ack = self._current_ack(transfer)
            if ack.received_blocks:
                self._mark_inbound_loss(transfer)
                acknowledgements.append((transfer.key, ack))
            transfer.ack_deadline = None
        return acknowledgements

    def cleanup(self, now: Optional[float] = None) -> List[TransferKey]:
        now = time.monotonic() if now is None else now
        expired_inbound = [
            key
            for key, transfer in self.inbound_transfers.items()
            if now - transfer.last_activity >= self.reassembly_timeout
        ]
        for key in expired_inbound:
            del self.inbound_transfers[key]

        expired_completed = [
            key
            for key, transfer in self.completed_inbound.items()
            if now >= transfer.expires_at
        ]
        for key in expired_completed:
            del self.completed_inbound[key]
        return expired_inbound

    def has_outbound_transfer(self, key: TransferKey) -> bool:
        return key in self.outbound_transfers

    def get_outbound_transfer(self, key: TransferKey) -> Optional[OutboundTransfer]:
        return self.outbound_transfers.get(key)
