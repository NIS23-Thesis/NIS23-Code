"""Protocol integration contract for CLIVE.

CLIVE never advances a protocol with mobility ticks. A protocol is started once, receives
incoming datagrams through ``on_datagram()``, and is closed once. Any HELLO timers,
retransmission timers, sampling intervals, FL aggregation timers, or worker threads belong
to the protocol implementation itself.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProtocolInterface(ABC):
    """Minimal interface implemented by FLAME (or any protocol under test)."""

    def __init__(self, node):
        self.node = node

    @abstractmethod
    def start(self) -> None:
        """Initialize protocol state/modules after the shared experiment epoch begins."""

    @abstractmethod
    def on_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        """Handle one datagram delivered by NodeSocket."""

    def close(self) -> None:
        """Stop protocol-owned workers/timers and release protocol resources."""


class NullProtocol(ProtocolInterface):
    """No-traffic protocol used for emulator/process/scalability validation."""

    def start(self) -> None:
        return None

    def on_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        return None
