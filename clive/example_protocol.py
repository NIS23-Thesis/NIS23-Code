"""Small protocol-interface example used only by validation.

This is not FLAME. It uses the node process's event-driven protocol timer API instead of a
Python thread. The 70 ms schedule is intentionally independent of the mobility trace quantum.
"""
from __future__ import annotations

from nodesocket import BROADCAST_ADDRESS
from protocol_interface import ProtocolInterface


class IndependentProbeProtocol(ProtocolInterface):
    PACKET_TYPE = 15
    INTERVAL_S = 0.07

    def __init__(self, node):
        super().__init__(node)
        self._sequence = 0
        self._next_send_s = None
        self._closed = False

    def start(self) -> None:
        # Absolute experiment-time schedule: 0.00, 0.07, 0.14, ... . Scheduling jitter
        # therefore does not permanently shift future logical probe deadlines.
        self._next_send_s = max(0.0, self.node.elapsed())

    def next_deadline(self):
        if self._closed or self._next_send_s is None:
            return None
        if self._next_send_s >= self.node.plan.config.duration_s:
            return None
        return self._next_send_s

    def on_timer(self, now_s: float) -> None:
        if self._closed or self._next_send_s is None:
            return
        if self._next_send_s >= self.node.plan.config.duration_s:
            return
        header = bytes([self.PACKET_TYPE, int(self.node.spec.role) << 7])
        payload = header + self._sequence.to_bytes(2, "big")
        accepted = self.node.sendto(payload, BROADCAST_ADDRESS)
        self.node.events.append({
            "type": "protocol_send",
            "time_s": self.node.elapsed(),
            "scheduled_time_s": self._next_send_s,
            "lateness_s": max(0.0, now_s - self._next_send_s),
            "accepted_bytes": accepted,
            "sequence": self._sequence,
        })
        self._sequence = (self._sequence + 1) & 0xFFFF
        self._next_send_s += self.INTERVAL_S

    def on_datagram(self, data, source) -> None:
        self.node.events.append({
            "type": "protocol_receive",
            "time_s": self.node.elapsed(),
            "source": list(source),
            "bytes": len(data),
        })

    def close(self) -> None:
        self._closed = True
