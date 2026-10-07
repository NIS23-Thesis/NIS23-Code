"""Small protocol-interface example used only by validation.

This is not FLAME. Its sender timer runs in a protocol-owned thread at 70 ms, intentionally
independent of the mobility trace quantum. It exists to prove that CLIVE does not call a
protocol ``step(tick)`` or otherwise clock protocol execution from mobility ticks.
"""
from __future__ import annotations

import threading
import time

from nodesocket import BROADCAST_ADDRESS
from protocol_interface import ProtocolInterface


class IndependentProbeProtocol(ProtocolInterface):
    PACKET_TYPE = 15  # Reserved here only for non-FLAME validation traffic.
    INTERVAL_S = 0.07

    def __init__(self, node):
        super().__init__(node)
        self._closed = threading.Event()
        self._thread = None
        self._sequence = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_sender,
            name=f"probe-{self.node.spec.name}",
            daemon=True,
        )
        self._thread.start()

    def _run_sender(self):
        deadline = time.monotonic()
        while not self._closed.is_set() and self.node.is_active():
            now = time.monotonic()
            if now < deadline:
                self._closed.wait(min(deadline - now, 0.05))
                continue
            header = bytes([self.PACKET_TYPE, int(self.node.spec.role) << 7])
            payload = header + self._sequence.to_bytes(2, "big")
            try:
                accepted = self.node.sendto(payload, BROADCAST_ADDRESS)
            except RuntimeError:
                break
            self.node.events.append({
                "type": "protocol_send",
                "time_s": self.node.elapsed(),
                "accepted_bytes": accepted,
                "sequence": self._sequence,
            })
            self._sequence = (self._sequence + 1) & 0xFFFF
            deadline += self.INTERVAL_S
            # Skip missed periods rather than generating a catch-up burst.
            if deadline < time.monotonic():
                deadline = time.monotonic() + self.INTERVAL_S

    def on_datagram(self, data, source) -> None:
        self.node.events.append({
            "type": "protocol_receive",
            "time_s": self.node.elapsed(),
            "source": list(source),
            "bytes": len(data),
        })

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
