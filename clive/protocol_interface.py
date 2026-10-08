"""Protocol integration contract for CLIVE.

CLIVE never advances a protocol from mobility ticks. Protocol timers are represented as
experiment-time deadlines serviced by the node process event loop; CPU-heavy radio work is
isolated in a separate radio process. This avoids protocol worker threads and their GIL
contention while keeping protocol timing independent from mobility trace ticks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProtocolInterface(ABC):
    """Minimal interface implemented by FLAME (or any protocol under test)."""

    def __init__(self, node):
        self.node = node

    @abstractmethod
    def start(self) -> None:
        """Initialize protocol state after the shared experiment epoch begins."""

    @abstractmethod
    def on_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        """Handle one datagram delivered by NodeSocket."""

    def next_deadline(self):
        """Return the next absolute experiment-time deadline in seconds, or None."""
        return None

    def on_timer(self, now_s: float) -> None:
        """Handle a due protocol timer. The default protocol has no timer work."""
        return None

    def close(self) -> None:
        """Release protocol resources. Protocols should not create Python worker threads."""
        return None


class NullProtocol(ProtocolInterface):
    """No-traffic protocol used for emulator/process/scalability validation."""

    def start(self) -> None:
        return None

    def on_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        return None
