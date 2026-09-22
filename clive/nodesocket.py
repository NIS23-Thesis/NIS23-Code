"""
CLIVE NodeSocket.

Provides the network interface used by an emulated Node.

NodeSocket:
- sends and receives raw bytes through UDP
- translates overlay and underlay addresses
- determines node distance from the mobility trace
- applies reachability, bandwidth, packet loss, delay, and IPDV
- records network-level metrics

NodeSocket does not know or interpret the protocol carried by the bytes.
"""

from __future__ import annotations

import math
import random
import socket
import time
import threading
from typing import Dict, List, Tuple

from orchestrator import EmulationPlan, NodeSpec


Address = Tuple[str, int]


RSSI_BANDS = (
    (None, -85.0, 0.00, 1.00, None, False),
    (-85.0, -75.0, 0.20, 0.10, 25.0, True),
    (-75.0, -65.0, 0.44, 0.02, 5.0, True),
    (-65.0, None, 1.00, 0.00, 1.0, True),
)


def link_conditions(distance_m, cfg):
    if distance_m == float("inf"):
        return {
            "reachable": False,
            "bandwidth_bps": 0.0,
            "plr": 1.0,
            "ipdv_ms": None,
        }

    rssi = (
        cfg.rssi0_dbm
        - 10
        * cfg.pathloss_exponent
        * math.log10(max(distance_m, 1e-6) / cfg.d0_m)
    )

    for lo, hi, bw, plr, ipdv, reachable in RSSI_BANDS:
        if (lo is None or rssi > lo) and (hi is None or rssi <= hi):
            return {
                "reachable": reachable,
                "bandwidth_bps": cfg.max_data_rate_bps * bw,
                "plr": plr,
                "ipdv_ms": ipdv,
            }

    return {
        "reachable": False,
        "bandwidth_bps": 0.0,
        "plr": 1.0,
        "ipdv_ms": None,
    }


class TokenBucket:

    def __init__(self, rate_bps):
        self.rate_bps = float(rate_bps)
        self.tokens = self.rate_bps
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def set_rate(self, rate_bps):
        with self.lock:
            self.rate_bps = float(rate_bps)
            self.tokens = min(
                self.tokens,
                max(self.rate_bps, 1.0),
            )

    def consume(self, bits):
        with self.lock:
            if self.rate_bps <= 0:
                return None

            now = time.monotonic()

            elapsed = now - self.last
            self.tokens = min(
                self.rate_bps,
                self.tokens + elapsed * self.rate_bps,
            )

            self.last = now

            if self.tokens >= bits:
                self.tokens -= bits
                return 0.0

            wait = (bits - self.tokens) / self.rate_bps

            self.tokens = 0.0
            self.last = now

            return wait


def energy_tx(bits, distance_m, cfg):
    return bits * (
        cfg.e_elec_j_per_bit
        + cfg.e_amp_j_per_bit_m2
        * (distance_m ** cfg.pathloss_exponent)
    )


def energy_rx(bits, cfg):
    return bits * cfg.e_elec_j_per_bit


class MetricsCollector:

    def __init__(self, node_name):
        self.node_name = node_name

        self.packets_sent = 0
        self.bits_sent = 0
        self.energy_sent_j = 0.0

        self.packets_received = 0
        self.bits_received = 0
        self.energy_received_j = 0.0

        self.packets_dropped = 0

        self.packet_log = []

    def record_send(
        self,
        destination,
        bits,
        distance_m,
        energy_j,
        sent,
        delay_s,
    ):
        self.packets_sent += 1
        self.bits_sent += bits
        self.energy_sent_j += energy_j

        self.packet_log.append({
            "direction": "send",
            "source": self.node_name,
            "destination": destination,
            "bits": bits,
            "distance_m": round(distance_m, 3),
            "delay_s": round(delay_s, 6),
            "sent": sent,
        })

    def record_drop(self):
        self.packets_dropped += 1

    def record_receive(self, bits, energy_j, source):
        self.packets_received += 1
        self.bits_received += bits
        self.energy_received_j += energy_j

        self.packet_log.append({
            "direction": "receive",
            "source": source,
            "destination": self.node_name,
            "bits": bits,
        })

    def snapshot(self):
        return {
            "node_name": self.node_name,
            "packets_sent": self.packets_sent,
            "bits_sent": self.bits_sent,
            "energy_sent_j": self.energy_sent_j,
            "packets_received": self.packets_received,
            "bits_received": self.bits_received,
            "energy_received_j": self.energy_received_j,
            "packets_dropped": self.packets_dropped,
            "packet_log": self.packet_log,
        }


class NodeSocket:

    def __init__(
        self,
        node_spec: NodeSpec,
        plan: EmulationPlan,
        seed: int = 0,
    ):
        self.spec = node_spec
        self.plan = plan
        self.trace = plan.trace
        self.cfg = plan.link_config

        self.tick = 0

        self.metrics = MetricsCollector(node_spec.name)

        self.nodes = {
            node.name: node
            for node in plan.nodes
        }

        self._underlay_of = {
            node.name: (
                node.underlay_ip,
                node.underlay_port,
            )
            for node in plan.nodes
        }

        self._overlay_of = {
            node.name: (
                node.overlay_ip,
                node.overlay_port,
            )
            for node in plan.nodes
        }

        self._name_of_underlay = {
            (
                node.underlay_ip,
                node.underlay_port,
            ): node.name
            for node in plan.nodes
        }

        self._name_of_overlay = {
            (
                node.overlay_ip,
                node.overlay_port,
            ): node.name
            for node in plan.nodes
        }

        self._bucket = TokenBucket(
            self.cfg.max_data_rate_bps
        )

        self._rng = random.Random(
            seed + sum(ord(c) for c in node_spec.name)
        )

        self._sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

        self._sock.bind(
            (
                node_spec.underlay_ip,
                node_spec.underlay_port,
            )
        )

        self._sock.setblocking(False)

    def set_tick(self, tick):
        self.tick = tick

    def _distance_to(self, destination_name):
        positions = self.trace.get(self.tick, {})

        mine = positions.get(self.spec.name)
        other = positions.get(destination_name)

        if mine is None or other is None:
            return float("inf")

        return math.hypot(
            other[0] - mine[0],
            other[1] - mine[1],
        )

    def _resolve_destination(self, destination: Address):
        name = self._name_of_overlay.get(destination)

        if name is None:
            raise ValueError(
                f"Unknown overlay address: {destination}"
            )

        return name

    def sendto(self, data: bytes, destination: Address):
        """
        Send raw bytes through the emulated network.
        """

        destination_name = self._resolve_destination(destination)

        if destination_name == self.spec.name:
            return False

        distance = self._distance_to(destination_name)

        conditions = link_conditions(
            distance,
            self.cfg,
        )

        bits = len(data) * 8

        energy = energy_tx(
            bits,
            distance if math.isfinite(distance) else 0.0,
            self.cfg,
        )

        if not conditions["reachable"]:
            self.metrics.record_drop()
            self.metrics.record_send(
                destination,
                bits,
                distance,
                energy,
                False,
                0.0,
            )
            return False

        self._bucket.set_rate(
            conditions["bandwidth_bps"]
        )

        wait = self._bucket.consume(bits)

        if wait is None:
            self.metrics.record_drop()
            self.metrics.record_send(
                destination,
                bits,
                distance,
                energy,
                False,
                0.0,
            )
            return False

        propagation_delay = (
            distance
            / self.cfg.propagation_speed_mps
        )

        ipdv = 0.0

        if conditions["ipdv_ms"] is not None:
            ipdv = (
                self._rng.gauss(
                    0.0,
                    conditions["ipdv_ms"] / 3.0,
                )
                / 1000.0
            )

        delay = max(
            0.0,
            self.cfg.base_delay_s
            + wait
            + propagation_delay
            + ipdv,
        )

        if self._rng.random() < conditions["plr"]:
            self.metrics.record_drop()
            self.metrics.record_send(
                destination,
                bits,
                distance,
                energy,
                False,
                delay,
            )
            return False

        if delay > 0:
            time.sleep(delay)

        self._sock.sendto(
            data,
            self._underlay_of[destination_name],
        )

        self.metrics.record_send(
            destination,
            bits,
            distance,
            energy,
            True,
            delay,
        )

        return True

    def recvfrom(self, bufsize=65535):
        """
        Receive raw bytes and return the source's overlay address.

        Returns None when no packet is currently available.
        """

        try:
            data, underlay_address = self._sock.recvfrom(
                bufsize
            )
        except BlockingIOError:
            return None

        source_name = self._name_of_underlay.get(
            underlay_address
        )

        if source_name is None:
            source_address = underlay_address
        else:
            source_address = self._overlay_of[
                source_name
            ]

        bits = len(data)

        self.metrics.record_receive(
            bits=bits,
            energy_j=energy_rx(
                bits,
                self.cfg,
            ),
            source=source_address,
        )

        return data, source_address

    def close(self):
        self._sock.close()