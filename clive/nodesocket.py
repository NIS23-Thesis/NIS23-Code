"""CLIVE NodeSocket with process-isolated radio/TX execution.

Architecture:
- The node/protocol runtime owns the receive UDP socket and runs in one OS process.
- A dedicated radio child process owns transmission scheduling and all CPU-heavy link/radio
  calculations (distance, RSSI, bandwidth, PLR, IPDV, TX energy, broadcast fan-out).
- There are no Python worker threads in the emulation runtime. Node and radio therefore do
  not contend for one process GIL; each process has its own interpreter/GIL.

The module remains the network-emulation boundary used by FLAME. It does not implement
FLAME discovery, membership, fragmentation, acknowledgements, retransmission, routing,
or forwarding.
"""
from __future__ import annotations

import errno
import hashlib
import heapq
import ipaddress
import json
import math
import multiprocessing as mp
import os
import random
import select
import socket
import time
import traceback
import threading
from collections import defaultdict
from itertools import count

from orchestrator import DEFAULT_PORT, finite

Address = tuple[str, int]
BROADCAST_IP = "255.255.255.255"
BROADCAST_ADDRESS = (BROADCAST_IP, DEFAULT_PORT)
MAX_DATAGRAM = 65507

RSSI_UNREACHABLE_DBM = -85.0
RSSI_POOR_DBM = -75.0
RSSI_GOOD_DBM = -65.0


def link_conditions(distance_m, cfg, max_range_m=None):
    finite(distance_m, "distance_m", 0)
    effective_distance = max(float(distance_m), float(cfg.d0_m))
    rssi = cfg.rssi0_dbm - 10.0 * cfg.pathloss_exponent * math.log10(
        effective_distance / cfg.d0_m
    )

    if cfg.range_mode == "explicit":
        range_limit = float(max_range_m if max_range_m is not None else cfg.tx_range_m(None))
        if distance_m > range_limit:
            fraction, plr, ipdv_ms = 0.0, 1.00, None
        else:
            ratio = 0.0 if range_limit <= 0 else float(distance_m) / range_limit
            if ratio <= 15.0 / 70.0:
                fraction, plr, ipdv_ms = 1.00, 0.00, 1.0
            elif ratio <= 32.0 / 70.0:
                fraction, plr, ipdv_ms = 0.44, 0.02, 5.0
            else:
                fraction, plr, ipdv_ms = 0.20, 0.10, 25.0
    else:
        range_limit = cfg.rssi_reachability_range_m()
        if rssi <= RSSI_UNREACHABLE_DBM:
            fraction, plr, ipdv_ms = 0.0, 1.00, None
        elif rssi <= RSSI_POOR_DBM:
            fraction, plr, ipdv_ms = 0.20, 0.10, 25.0
        elif rssi < RSSI_GOOD_DBM:
            fraction, plr, ipdv_ms = 0.44, 0.02, 5.0
        else:
            fraction, plr, ipdv_ms = 1.00, 0.00, 1.0

    return {
        "reachable": fraction > 0.0,
        "rssi_dbm": rssi,
        "bandwidth_bps": cfg.max_data_rate_bps * fraction,
        "plr": plr,
        "ipdv_ms": ipdv_ms,
        "max_range_m": range_limit,
        "range_mode": cfg.range_mode,
    }


def _fast_link_from_squared_distance(distance_sq, cfg, sender_range, *, need_debug=False):
    """Internal allocation-light link calculation used by the radio worker.

    Returns (distance, reachable, bandwidth_bps, plr, ipdv_ms, rssi_dbm). For normal
    evaluation with explicit ranges, unreachable peers avoid sqrt/log10 entirely because
    only aggregate drop counters are needed.
    """
    if cfg.range_mode == "explicit":
        limit = float(sender_range)
        if distance_sq > limit * limit:
            if not need_debug:
                return None, False, 0.0, 1.0, None, None
            distance = math.sqrt(distance_sq)
            effective = max(distance, float(cfg.d0_m))
            rssi = cfg.rssi0_dbm - 10.0 * cfg.pathloss_exponent * math.log10(effective / cfg.d0_m)
            return distance, False, 0.0, 1.0, None, rssi

        distance = math.sqrt(distance_sq)
        ratio = 0.0 if limit <= 0 else distance / limit
        if ratio <= 15.0 / 70.0:
            fraction, plr, ipdv_ms = 1.00, 0.00, 1.0
        elif ratio <= 32.0 / 70.0:
            fraction, plr, ipdv_ms = 0.44, 0.02, 5.0
        else:
            fraction, plr, ipdv_ms = 0.20, 0.10, 25.0
        rssi = None
        if need_debug:
            effective = max(distance, float(cfg.d0_m))
            rssi = cfg.rssi0_dbm - 10.0 * cfg.pathloss_exponent * math.log10(effective / cfg.d0_m)
        return distance, True, cfg.max_data_rate_bps * fraction, plr, ipdv_ms, rssi

    distance = math.sqrt(distance_sq)
    effective = max(distance, float(cfg.d0_m))
    rssi = cfg.rssi0_dbm - 10.0 * cfg.pathloss_exponent * math.log10(effective / cfg.d0_m)
    if rssi <= RSSI_UNREACHABLE_DBM:
        fraction, plr, ipdv_ms = 0.0, 1.00, None
    elif rssi <= RSSI_POOR_DBM:
        fraction, plr, ipdv_ms = 0.20, 0.10, 25.0
    elif rssi < RSSI_GOOD_DBM:
        fraction, plr, ipdv_ms = 0.44, 0.02, 5.0
    else:
        fraction, plr, ipdv_ms = 1.00, 0.00, 1.0
    return distance, fraction > 0.0, cfg.max_data_rate_bps * fraction, plr, ipdv_ms, rssi


class TokenBucket:
    """Bit-based token bucket used to enforce the emulated transmission rate."""

    def __init__(self, capacity_bits=MAX_DATAGRAM * 8, now=None):
        finite(capacity_bits, "capacity_bits", 0, strictly=True)
        self.capacity = float(capacity_bits)
        self.tokens = 0.0
        self.updated = time.monotonic() if now is None else float(now)
        self.rate = None

    def wait_time(self, bits, rate_bps, now=None):
        finite(bits, "bits", 0)
        finite(rate_bps, "rate_bps", 0, strictly=True)
        if bits > self.capacity:
            raise ValueError("packet exceeds token bucket capacity")
        now = time.monotonic() if now is None else float(now)
        if now < self.updated:
            raise ValueError("token bucket clock moved backward")
        previous_rate = rate_bps if self.rate is None else self.rate
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * previous_rate)
        self.updated = now
        self.rate = rate_bps
        return max(0.0, (bits - self.tokens) / rate_bps)

    def consume(self, bits, rate_bps, now=None):
        wait = self.wait_time(bits, rate_bps, now)
        if wait > 1e-9:
            return False
        self.tokens = max(0.0, self.tokens - bits)
        return True


def energy_tx(bits, distance_m, cfg):
    finite(bits, "bits", 0)
    finite(distance_m, "distance_m", 0)
    return bits * (
        cfg.e_elec_j_per_bit
        + cfg.e_amp_j_per_bit_m2 * float(distance_m) ** cfg.pathloss_exponent
    )


def energy_rx(bits, cfg):
    finite(bits, "bits", 0)
    return bits * cfg.e_elec_j_per_bit


def parse_common_header(data):
    if len(data) < 4:
        return None
    return {
        "protocol_version": data[0] >> 4,
        "packet_type": data[0] & 0x0F,
        "role": data[1] >> 7,
        "padding": data[1] & 0x7F,
        "sequence_number": int.from_bytes(data[2:4], "big"),
    }


class MetricsCollector:
    """Single-process packet metrics and optional JSONL event stream.

    The emulation runtime deliberately avoids Python worker threads, so this collector does
    not need a threading lock. A node process and its radio child maintain separate collectors;
    NodeSocket merges their snapshots when metrics are requested.
    """

    FIELDS = (
        "packets_attempted",
        "bits_attempted",
        "packets_admitted",
        "bits_admitted",
        "packets_sent",
        "bits_sent",
        "energy_sent_j",
        "packets_received",
        "bits_received",
        "energy_received_j",
        "packets_dropped",
        "delivery_attempts",
        "delivery_drops",
        "kernel_datagrams_sent",
        "socket_errors",
    )

    def __init__(self, node_name, log_path=None):
        self.node_name = node_name
        self.totals = {name: 0 for name in self.FIELDS}
        self.by_packet_type = defaultdict(lambda: {name: 0 for name in self.FIELDS})
        self.stream = open(log_path, "x", encoding="utf-8", buffering=1) if log_path else None
        self.unknown_sources_rejected = 0

    def count(self, *, header=None, counters=None):
        """Update aggregate counters without constructing a diagnostic event record."""
        if not counters:
            return
        key = "unknown" if header is None else str(header["packet_type"])
        per_type = self.by_packet_type[key]
        for name, value in counters.items():
            self.totals[name] += value
            per_type[name] += value

    def record(self, kind, *, header=None, counters=None, **fields):
        # Normal evaluation has no packet JSONL stream. Avoid allocating a large event dict
        # for every recipient of every broadcast when nobody will read it.
        self.count(header=header, counters=counters)
        if self.stream is None:
            return
        record = {
            "event": kind,
            "node_name": self.node_name,
            "common_header": header,
            "counters": dict(counters or {}),
            **fields,
        }
        self.stream.write(json.dumps(record, allow_nan=False) + "\n")

    def reject_unknown(self):
        self.unknown_sources_rejected += 1

    def snapshot(self):
        return {
            "schema_version": 5,
            "node_name": self.node_name,
            **self.totals,
            "by_packet_type": {key: dict(value) for key, value in self.by_packet_type.items()},
            "unknown_sources_rejected": self.unknown_sources_rejected,
            "accounting": (
                "packets_admitted = accepted into software queue; packets_sent/bits_sent = "
                "actual modeled radio emissions; kernel_datagrams_sent = successful kernel "
                "sendto calls (not verified delivery); delivery_drops = per-recipient modeled loss"
            ),
        }

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None


def _merge_metric_snapshots(node_name, *snapshots):
    result = {
        "schema_version": 5,
        "node_name": node_name,
        **{name: 0 for name in MetricsCollector.FIELDS},
        "by_packet_type": {},
        "unknown_sources_rejected": 0,
        "accounting": (
            "packets_admitted = accepted into software queue; packets_sent/bits_sent = actual "
            "modeled radio emissions; kernel_datagrams_sent = successful kernel sendto calls "
            "(not verified delivery); delivery_drops = per-recipient modeled loss"
        ),
    }
    by_type = defaultdict(lambda: {name: 0 for name in MetricsCollector.FIELDS})
    for snap in snapshots:
        if not snap:
            continue
        for name in MetricsCollector.FIELDS:
            result[name] += snap.get(name, 0)
        result["unknown_sources_rejected"] += snap.get("unknown_sources_rejected", 0)
        for packet_type, values in snap.get("by_packet_type", {}).items():
            for name in MetricsCollector.FIELDS:
                by_type[str(packet_type)][name] += values.get(name, 0)
    result["by_packet_type"] = {key: dict(value) for key, value in sorted(by_type.items())}
    return result


class _RadioEngine:
    """Single-threaded radio/TX engine that runs only inside the radio child process."""

    def __init__(self, spec, plan, seed, status_conn, log_path=None):
        self.spec = spec
        self.plan = plan
        self.cfg = plan.link_config
        self.trace = plan.open_trace()
        self.status = status_conn
        self._address_of = {node.name: node.address for node in plan.nodes}
        self._node_index = {node.name: i for i, node in enumerate(plan.nodes)}
        self._broadcast_targets_by_port = {}
        for node in plan.nodes:
            if node.name == spec.name:
                continue
            self._broadcast_targets_by_port.setdefault(node.port, []).append(node.name)
        self._broadcast_targets_by_port = {k: tuple(v) for k, v in self._broadcast_targets_by_port.items()}
        self._packet_debug = self.metrics.stream is not None if hasattr(self, "metrics") else bool(log_path)
        self._epoch_mono = time.monotonic() + plan.epoch_start - time.time()
        self._heap = []
        self._pending = {}
        self._order = count()
        self._closed = False
        seed_bytes = hashlib.sha256(f"{seed}:{spec.name}".encode()).digest()[:8]
        self._rng = random.Random(int.from_bytes(seed_bytes, "big"))
        self._buckets = {}
        self._shared_bucket = TokenBucket(self.cfg.bucket_capacity_bytes * 8)
        self._broadcast_pair_finish = {}
        self.metrics = MetricsCollector(spec.name, log_path)
        self._packet_debug = self.metrics.stream is not None

        # TX gets its own socket and its own source port. Every virtual node already has a
        # unique loopback IP, so receivers identify the sender by source IP and return the
        # canonical NodeSpec address to the protocol.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((spec.ip, 0))

    def set_epoch(self, epoch_start):
        finite(epoch_start, "epoch_start", 0, strictly=True)
        if self._pending:
            raise RuntimeError("cannot change epoch with pending transmissions")
        self.plan.epoch_start = epoch_start
        self._epoch_mono = time.monotonic() + epoch_start - time.time()
        self._buckets.clear()
        self._shared_bucket = TokenBucket(self.cfg.bucket_capacity_bytes * 8)
        self._broadcast_pair_finish.clear()

    def elapsed(self):
        return time.monotonic() - self._epoch_mono

    def _current_mobility_tick(self):
        return max(0, int(self.elapsed() / self.plan.mobility_config.tick_interval))

    def _distance_to(self, name, tick=None):
        tick = self._current_mobility_tick() if tick is None else tick
        if not 0 <= tick < len(self.trace):
            raise RuntimeError(f"mobility trace has no current tick {tick}")
        frame = self.trace.frame_flat(tick)
        source_i = self._node_index[self.spec.name] * 2
        target_i = self._node_index[name] * 2
        dx = frame[source_i] - frame[target_i]
        dy = frame[source_i + 1] - frame[target_i + 1]
        return math.hypot(dx, dy)

    def _time_to_next_tick(self):
        quantum = self.plan.mobility_config.tick_interval
        elapsed = self.elapsed()
        next_boundary = (math.floor(max(0.0, elapsed) / quantum) + 1) * quantum
        return max(0.0, next_boundary - elapsed)

    def _packet_is_lost(self, plr):
        finite(plr, "plr", 0)
        if plr > 1:
            raise ValueError("plr must be <= 1")
        rolled = self._rng.randrange(1000)
        threshold = plr * 1000.0
        return rolled < threshold, rolled, threshold

    def _sample_ipdv_s(self, max_ipdv_ms):
        if not self.cfg.ipdv_enabled or max_ipdv_ms is None:
            return 0.0
        finite(max_ipdv_ms, "max_ipdv_ms", 0)
        return self._rng.gauss(0.0, max_ipdv_ms / 3.0) / 1000.0

    def _event(self, req, kind, counters=None, **fields):
        self.metrics.record(
            kind,
            header=req["header"],
            counters=counters,
            packet_id=req["id"],
            time_s=self.elapsed(),
            destination=list(req["destination"]),
            is_broadcast=req["broadcast"],
            **fields,
        )

    def accept(self, req):
        self._pending[req["id"]] = req
        self._push(time.monotonic(), "tx", req, None)

    def _push(self, when, action, req, target):
        heapq.heappush(self._heap, (when, next(self._order), action, req, target))

    def _finish(self, req):
        if self._pending.pop(req["id"], None) is not None:
            self.status.send({"type": "request_done", "id": req["id"], "bytes": len(req["data"])})

    def _request_targets(self, req):
        if req["broadcast"]:
            return self._broadcast_targets_by_port.get(req["destination"][1], ())
        target = req.get("target")
        return () if target is None else (target,)

    def _links_for_request(self, req, tick):
        """Return compact tuples: (name, distance, reachable, bw, plr, ipdv_ms, rssi)."""
        names = self._request_targets(req)
        if not names:
            return []
        frame = self.trace.frame_flat(tick)
        source_i = self._node_index[self.spec.name] * 2
        sx, sy = frame[source_i], frame[source_i + 1]
        sender_range = self.cfg.tx_range_m(self.spec)
        links = []
        append = links.append
        need_debug = self._packet_debug
        for name in names:
            j = self._node_index[name] * 2
            dx = sx - frame[j]
            dy = sy - frame[j + 1]
            distance, reachable, bandwidth, plr, ipdv_ms, rssi = _fast_link_from_squared_distance(
                dx * dx + dy * dy, self.cfg, sender_range, need_debug=need_debug
            )
            append((name, distance, reachable, bandwidth, plr, ipdv_ms, rssi))
        return links

    def _radio_rate_and_distance(self, req, links):
        if not req["broadcast"]:
            if not links:
                return self.cfg.max_data_rate_bps, 0.0, 0.0
            _, distance, _, link_rate, _, _, _ = links[0]
            airtime_rate = link_rate or self.cfg.max_data_rate_bps
            return airtime_rate, distance or 0.0, link_rate
        energy_distance = max((link[1] or 0.0 for link in links if link[2]), default=0.0)
        return self.cfg.max_data_rate_bps, energy_distance, None

    def _transmit(self, req):
        tick = self._current_mobility_tick()
        links = self._links_for_request(req, tick)
        airtime_rate, energy_distance, unicast_link_rate = self._radio_rate_and_distance(req, links)
        now = time.monotonic()
        if self.elapsed() >= self.plan.config.duration_s:
            self._cancel_pending("simulation_end")
            return

        reachable_count = sum(1 for link in links if link[2])
        if reachable_count == 0:
            if not links:
                self.metrics.count(header=req["header"], counters={"packets_dropped": 1})
                if self._packet_debug:
                    self._event(req, "local_drop", reason="no_receivers")
            elif self._packet_debug:
                for name, distance, reachable, bandwidth, plr, ipdv_ms, rssi in links:
                    self.metrics.record(
                        "delivery_attempt", header=req["header"], counters={"delivery_attempts": 1},
                        packet_id=req["id"], time_s=self.elapsed(), source=self.spec.name,
                        destination=list(self._address_of[name]), distance_m=distance, rssi_dbm=rssi,
                        bandwidth_bps=bandwidth, plr=plr, ipdv_ms=ipdv_ms, mobility_tick=tick,
                    )
                    self._drop_delivery(req, name, "unreachable", conditions=(bandwidth, plr, ipdv_ms, rssi),
                                        distance_m=distance)
            else:
                self.metrics.count(
                    header=req["header"],
                    counters={"delivery_attempts": len(links), "delivery_drops": len(links)},
                )
            self._finish(req)
            return

        bucket_key = (("broadcast", req["destination"][1]) if req["broadcast"] else req.get("target"))
        bucket = self._buckets.setdefault(
            bucket_key, TokenBucket(self.cfg.bucket_capacity_bytes * 8, now=now)
        )
        link_wait = bucket.wait_time(req["bits"], airtime_rate, now)
        radio_wait = self._shared_bucket.wait_time(req["bits"], self.cfg.max_data_rate_bps, now)
        wait = max(link_wait, radio_wait)
        if wait > 1e-9:
            next_tick_wait = self._time_to_next_tick()
            if next_tick_wait > 1e-9:
                wait = min(wait, next_tick_wait)
            self._push(now + wait, "tx", req, None)
            return

        if not bucket.consume(req["bits"], airtime_rate, now):
            raise RuntimeError("link token accounting invariant failed")
        if not self._shared_bucket.consume(req["bits"], self.cfg.max_data_rate_bps, now):
            raise RuntimeError("shared token accounting invariant failed")

        req["emitted"] = True
        queue_and_tx_delay = now - req["queued_at_mono"]
        tx_energy = energy_tx(req["bits"], energy_distance, self.cfg)
        counters = {"packets_sent": 1, "bits_sent": req["bits"], "energy_sent_j": tx_energy}
        if self._packet_debug:
            first_distance = links[0][1] or 0.0
            self._event(
                req, "transmit", counters,
                distance_m=(energy_distance if req["broadcast"] else first_distance),
                energy_distance_m=energy_distance,
                energy_distance_policy=("farthest_reachable_receiver" if req["broadcast"] else "unicast_peer"),
                mobility_tick=tick,
                bandwidth_bps=(None if req["broadcast"] else unicast_link_rate),
                airtime_rate_bps=airtime_rate,
                queue_and_transmission_delay_s=queue_and_tx_delay,
            )
        else:
            self.metrics.count(header=req["header"], counters=counters)
            self.metrics.count(header=req["header"], counters={"delivery_attempts": len(links)})

        drop_count = 0
        for name, distance, reachable, bandwidth, plr, ipdv_ms, rssi in links:
            if self._packet_debug:
                self.metrics.record(
                    "delivery_attempt", header=req["header"], counters={"delivery_attempts": 1},
                    packet_id=req["id"], time_s=self.elapsed(), source=self.spec.name,
                    destination=list(self._address_of[name]), distance_m=distance, rssi_dbm=rssi,
                    bandwidth_bps=bandwidth, plr=plr, ipdv_ms=ipdv_ms, mobility_tick=tick,
                )
            lost, rolled, threshold = self._packet_is_lost(plr)
            if lost:
                reason = "unreachable" if not reachable else "plr"
                if self._packet_debug:
                    self._drop_delivery(
                        req, name, reason, rolled=rolled, threshold=threshold,
                        conditions=(bandwidth, plr, ipdv_ms, rssi), distance_m=distance,
                    )
                else:
                    drop_count += 1
                continue

            # Reachable links always have a real distance.
            distance = float(distance)
            if req["broadcast"]:
                pair_rate = bandwidth
                sender_serialization = req["bits"] / airtime_rate
                prior_finish = self._broadcast_pair_finish.get(name, now - sender_serialization)
                pair_finish = max(now - sender_serialization, prior_finish) + req["bits"] / pair_rate
                self._broadcast_pair_finish[name] = pair_finish
                pairwise_bandwidth_delay = max(0.0, pair_finish - now)
            else:
                pairwise_bandwidth_delay = 0.0

            variation = self._sample_ipdv_s(ipdv_ms)
            propagation_delay = distance / self.cfg.propagation_speed_mps
            extra_delay = pairwise_bandwidth_delay + self.cfg.base_delay_s + propagation_delay + variation
            extra_delay = max(pairwise_bandwidth_delay, extra_delay)
            req["outstanding"] += 1
            self._push(now + extra_delay, "deliver", req, name)
            if self._packet_debug:
                self._event(
                    req, "scheduled_delivery", target=list(self._address_of[name]),
                    distance_m=distance, rssi_dbm=rssi, bandwidth_bps=bandwidth, plr=plr,
                    ipdv_s=variation, propagation_delay_s=propagation_delay,
                    pairwise_bandwidth_delay_s=pairwise_bandwidth_delay,
                    post_transmission_delay_s=extra_delay,
                )
        if drop_count:
            self.metrics.count(header=req["header"], counters={"delivery_drops": drop_count})
        if not req["outstanding"]:
            self._finish(req)

    def _drop_delivery(self, req, name, reason, *, rolled=None, threshold=None,
                       conditions=None, distance_m=None):
        if not self._packet_debug:
            self.metrics.count(header=req["header"], counters={"delivery_drops": 1})
            return
        fields = {"target": list(self._address_of[name]), "reason": reason}
        if rolled is not None:
            fields["plr_roll"] = rolled
        if threshold is not None:
            fields["plr_threshold"] = threshold
        if conditions is not None:
            bandwidth, plr, ipdv_ms, rssi = conditions
            fields.update(rssi_dbm=rssi, bandwidth_bps=bandwidth, plr=plr, ipdv_ms=ipdv_ms)
        if distance_m is not None:
            fields["distance_m"] = distance_m
        self._event(req, "delivery_drop", {"delivery_drops": 1}, **fields)

    def _deliver(self, req, name):
        try:
            self._sock.sendto(req["data"], self._address_of[name])
        except OSError as exc:
            self.metrics.count(header=req["header"], counters={"delivery_drops": 1, "socket_errors": 1})
            if self._packet_debug:
                self._event(req, "delivery_drop", target=list(self._address_of[name]),
                            reason=f"socket_error:{exc.errno}")
                self._event(req, "socket_error", error=str(exc))
            raise
        else:
            if self._packet_debug:
                self._event(req, "kernel_send", {"kernel_datagrams_sent": 1},
                            target=list(self._address_of[name]))
            else:
                self.metrics.count(header=req["header"], counters={"kernel_datagrams_sent": 1})
        req["outstanding"] -= 1
        if req["outstanding"] == 0:
            self._finish(req)

    def _cancel_pending(self, reason):
        seen_deliveries = set()
        for _, _, action, req, name in self._heap:
            if action == "deliver":
                key = (req["id"], name)
                if key not in seen_deliveries:
                    seen_deliveries.add(key)
                    self._drop_delivery(req, name, reason)
        for req in list(self._pending.values()):
            if not req["emitted"]:
                self._event(req, "local_drop", {"packets_dropped": 1}, reason=reason)
            self.status.send({"type": "request_done", "id": req["id"], "bytes": len(req["data"])})
        self._heap.clear()
        self._pending.clear()

    def process_due(self, *, max_actions=256):
        processed = 0
        now = time.monotonic()
        while self._heap and self._heap[0][0] <= now and processed < max_actions:
            _, _, action, req, target = heapq.heappop(self._heap)
            if action == "tx":
                self._transmit(req)
            elif action == "deliver":
                self._deliver(req, target)
            else:
                raise RuntimeError(f"unknown radio action {action!r}")
            processed += 1
            now = time.monotonic()
        return processed

    def next_wait(self):
        # Connection.poll() wakes immediately when the node sends a command. When idle, a
        # longer ceiling avoids thousands of pointless process wakeups per second.
        if not self._heap:
            return 0.25
        return max(0.0, min(0.25, self._heap[0][0] - time.monotonic()))

    def close(self, reason="socket_closed"):
        if self._closed:
            return
        self._closed = True
        self._cancel_pending(reason)
        self._sock.close()
        self.trace.close()
        self.metrics.close()


def _radio_worker_main(spec, plan, seed, command_conn, status_conn, log_path):
    engine = None
    try:
        engine = _RadioEngine(spec, plan, seed, status_conn, log_path)
        status_conn.send({"type": "ready", "pid": os.getpid()})
        running = True
        while running:
            # Keep control responsive but do not let an unlimited command burst starve due TX.
            for _ in range(128):
                if not command_conn.poll():
                    break
                msg = command_conn.recv()
                op = msg.get("op")
                if op == "set_epoch":
                    engine.set_epoch(msg["epoch_start"])
                    status_conn.send({"type": "epoch_set"})
                elif op == "send":
                    engine.accept(msg["request"])
                elif op == "snapshot":
                    status_conn.send({
                        "type": "snapshot",
                        "request_id": msg["request_id"],
                        "metrics": engine.metrics.snapshot(),
                    })
                elif op == "close":
                    reason = msg.get("reason", "socket_closed")
                    engine.close(reason)
                    status_conn.send({"type": "final", "metrics": engine.metrics.snapshot()})
                    running = False
                    break
                else:
                    raise RuntimeError(f"unknown radio command {op!r}")

            if not running:
                break

            engine.process_due()
            if engine.elapsed() >= engine.plan.config.duration_s and engine._pending:
                engine._cancel_pending("simulation_end")

            timeout = engine.next_wait()
            # Sleep inside Connection.poll so the process waits without any Python thread.
            if not command_conn.poll(timeout):
                continue

    except EOFError:
        pass
    except BaseException:
        try:
            status_conn.send({"type": "fatal", "error": traceback.format_exc()})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        if engine is not None and not engine._closed:
            try:
                reason = "simulation_end" if engine.elapsed() >= engine.plan.config.duration_s else "worker_exit"
                engine.close(reason)
                try:
                    status_conn.send({"type": "final", "metrics": engine.metrics.snapshot()})
                except (BrokenPipeError, EOFError, OSError):
                    pass
            except Exception:
                pass
        command_conn.close()
        status_conn.close()


class _RadioThread(threading.Thread):
    """Optional compact radio runtime; executes the SAME _radio_worker_main.

    Separating radio into a thread saves a full Python interpreter per node.
    The GIL may affect timing under CPU saturation: use process mode for timing
    baselines and compare before publishing experimental results.
    """
    def __init__(self, *, name, target, args):
        super().__init__(name=name, target=target, args=args, daemon=True)

    @property
    def exitcode(self):
        return None if self.is_alive() else 0

    def terminate(self):
        # Threads cannot be forcibly killed safely. NodeSocket has already
        # sent 'close'; the entire node process is supervisor-owned and exits.
        pass


class _CombinedMetrics:
    def __init__(self, owner):
        self.owner = owner

    def snapshot(self):
        return self.owner._metrics_snapshot()


class NodeSocket:
    """Socket-like endpoint with RX in the node process and TX/radio in a child process."""

    def __init__(self, node_spec, plan, seed=0, *, log_path=None):
        node_spec.validate()
        plan.validate()
        if node_spec not in plan.nodes:
            raise ValueError("node spec is not a member of the plan")

        self.spec = node_spec
        self.plan = plan
        self.cfg = plan.link_config
        self._address_of = {node.name: node.address for node in plan.nodes}
        self._name_of_address = {address: name for name, address in self._address_of.items()}
        self._name_of_ip = {node.ip: node.name for node in plan.nodes}
        self._epoch_mono = time.monotonic() + plan.epoch_start - time.time()
        self._closed = False
        self._fatal = None
        self._radio_final_metrics = None
        self._radio_messages = []
        self._snapshot_seq = count(1)
        self._pending = {}
        self._queued_bytes = 0
        self._ids = count(1)

        node_log_path = None
        radio_log_path = None
        if log_path:
            base, ext = os.path.splitext(log_path)
            node_log_path = base + ".node" + ext
            radio_log_path = base + ".radio" + ext
        self._node_metrics = MetricsCollector(node_spec.name, node_log_path)
        self.metrics = _CombinedMetrics(self)

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind(node_spec.address)
            self._sock.setblocking(False)
        except BaseException:
            self._sock.close()
            self._node_metrics.close()
            raise

        method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        self._mp_context = mp.get_context(method)
        child_cmd_recv, parent_cmd_send = self._mp_context.Pipe(duplex=False)
        parent_status_recv, child_status_send = self._mp_context.Pipe(duplex=False)
        self._command = parent_cmd_send
        self._status = parent_status_recv
        worker_args = (node_spec, plan, seed, child_cmd_recv, child_status_send, radio_log_path)
        self.radio_mode = plan.config.radio_execution
        if self.radio_mode == "thread":
            self._radio = _RadioThread(
                name=f"CLIVE-Radio-{node_spec.name}",
                target=_radio_worker_main, args=worker_args,
            )
        else:
            self._radio = self._mp_context.Process(
                name=f"CLIVE-Radio-{node_spec.name}",
                target=_radio_worker_main, args=worker_args,
            )
        self._radio.start()
        # A forked process inherited its own handles; a thread uses the exact
        # same handles, so closing them here would break the thread's IPC.
        if self.radio_mode == "process":
            child_cmd_recv.close()
            child_status_send.close()
        self._wait_radio_ready()

    def _wait_radio_ready(self):
        deadline = time.monotonic() + max(5.0, self.plan.config.readiness_timeout_s)
        while time.monotonic() < deadline:
            if self._status.poll(0.05):
                msg = self._status.recv()
                if msg.get("type") == "ready":
                    self.radio_pid = msg.get("pid")
                    return
                if msg.get("type") == "fatal":
                    raise RuntimeError("radio process failed during startup:\n" + msg.get("error", ""))
                self._radio_messages.append(msg)
            if self._radio.exitcode is not None:
                raise RuntimeError(f"radio process exited during startup ({self._radio.exitcode})")
        raise TimeoutError(f"radio process for {self.spec.name} did not become ready")

    def set_epoch(self, epoch_start):
        finite(epoch_start, "epoch_start", 0, strictly=True)
        if self._pending:
            raise RuntimeError("cannot change epoch with pending transmissions")
        self.plan.epoch_start = epoch_start
        self._epoch_mono = time.monotonic() + epoch_start - time.time()
        self._command.send({"op": "set_epoch", "epoch_start": epoch_start})
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            self._drain_radio_status()
            for i, msg in enumerate(self._radio_messages):
                if msg.get("type") == "epoch_set":
                    self._radio_messages.pop(i)
                    return
            if self._status.poll(0.01):
                continue
        raise TimeoutError("radio process did not acknowledge experiment epoch")

    def elapsed(self):
        return time.monotonic() - self._epoch_mono

    def _current_mobility_tick(self):
        return max(0, int(self.elapsed() / self.plan.mobility_config.tick_interval))

    def fileno(self):
        return self._sock.fileno()

    def _drain_radio_status(self):
        while self._status.poll():
            try:
                msg = self._status.recv()
            except EOFError:
                break
            kind = msg.get("type")
            if kind == "request_done":
                req = self._pending.pop(msg.get("id"), None)
                if req is not None:
                    self._queued_bytes = max(0, self._queued_bytes - req["bytes"])
            elif kind == "fatal":
                self._fatal = msg.get("error", "radio child failed")
            elif kind == "final":
                self._radio_final_metrics = msg.get("metrics")
            else:
                self._radio_messages.append(msg)

    def check_health(self):
        self._drain_radio_status()
        if self._fatal is not None:
            raise RuntimeError("NodeSocket radio child failed:\n" + self._fatal)
        if not self._closed and self._radio.exitcode is not None:
            raise RuntimeError(f"NodeSocket radio child exited unexpectedly ({self._radio.exitcode})")

    def wait_readable(self, timeout):
        self.check_health()
        if timeout <= 0:
            return False
        readable, _, _ = select.select([self._sock, self._status], [], [], timeout)
        if self._status in readable:
            self._drain_radio_status()
            self.check_health()
        return self._sock in readable

    def _normalize_destination(self, destination):
        if (
            not isinstance(destination, tuple)
            or len(destination) != 2
            or not isinstance(destination[0], str)
            or type(destination[1]) is not int
            or not 1 <= destination[1] <= 65535
        ):
            raise ValueError("destination must be (IPv4 address, port)")
        try:
            ipaddress.IPv4Address(destination[0])
        except ipaddress.AddressValueError as exc:
            raise ValueError("destination must contain a valid IPv4 address") from exc
        return destination

    def _node_event(self, req, kind, counters=None, **fields):
        self._node_metrics.record(
            kind,
            header=req["header"],
            counters=counters,
            packet_id=req["id"],
            time_s=self.elapsed(),
            destination=list(req["destination"]),
            is_broadcast=req["broadcast"],
            **fields,
        )

    def sendto(self, data, destination):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        data = bytes(data)
        if len(data) > MAX_DATAGRAM:
            raise OSError(errno.EMSGSIZE, "IPv4 UDP payload exceeds 65507 bytes")
        destination = self._normalize_destination(destination)
        broadcast = destination[0] == BROADCAST_IP
        if broadcast:
            # Do not serialize an O(N) target-name list over IPC for every broadcast. The radio
            # child already knows the immutable node table and expands the broadcast locally.
            target = None
        else:
            target = self._name_of_address.get(destination)
            if target is None:
                raise ValueError(f"unknown localhost node address: {destination}")

        self.check_health()
        if self._closed:
            raise OSError(errno.EBADF, "NodeSocket is closed")
        if not 0 <= self.elapsed() < self.plan.config.duration_s:
            raise RuntimeError("send outside experiment time window")

        req = {
            "id": next(self._ids),
            "data": data,
            "destination": destination,
            "broadcast": broadcast,
            "target": target,
            "header": parse_common_header(data),
            "bits": len(data) * 8,
            "queued_at_mono": time.monotonic(),
            "emitted": False,
            "outstanding": 0,
        }
        self._node_event(req, "attempt", {"packets_attempted": 1, "bits_attempted": req["bits"]})
        self._drain_radio_status()
        if (
            len(self._pending) >= self.plan.config.max_queue_packets
            or self._queued_bytes + len(data) > self.plan.config.max_queue_bytes
        ):
            self._node_event(req, "local_drop", {"packets_dropped": 1}, reason="queue_full")
            raise BlockingIOError(errno.ENOBUFS, "emulated send queue is full")

        self._pending[req["id"]] = {"bytes": len(data)}
        self._queued_bytes += len(data)
        self._node_event(req, "admit", {"packets_admitted": 1, "bits_admitted": req["bits"]})
        try:
            self._command.send({"op": "send", "request": req})
        except (BrokenPipeError, EOFError, OSError):
            self._pending.pop(req["id"], None)
            self._queued_bytes = max(0, self._queued_bytes - len(data))
            self.check_health()
            raise RuntimeError("radio child command pipe is unavailable")
        return len(data)

    def recvfrom(self, bufsize=65535):
        if type(bufsize) is not int or bufsize <= 0:
            raise ValueError("bufsize must be a positive integer")
        self.check_health()
        for _ in range(self.plan.config.receive_batch_size):
            try:
                data, address = self._sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                return None
            name = self._name_of_ip.get(address[0])
            if name is None:
                self._node_metrics.reject_unknown()
                continue
            if not 0 <= self.elapsed() < self.plan.config.duration_s:
                continue
            canonical_source = self._address_of[name]
            self._node_metrics.record(
                "receive",
                header=parse_common_header(data),
                counters={
                    "packets_received": 1,
                    "bits_received": len(data) * 8,
                    "energy_received_j": energy_rx(len(data) * 8, self.cfg),
                },
                time_s=self.elapsed(),
                source=list(canonical_source),
                actual_source=list(address),
                mobility_tick=self._current_mobility_tick(),
            )
            return data[:bufsize], canonical_source
        return None

    def _request_radio_snapshot(self, timeout=2.0):
        if self._radio_final_metrics is not None:
            return self._radio_final_metrics
        self.check_health()
        request_id = next(self._snapshot_seq)
        self._command.send({"op": "snapshot", "request_id": request_id})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain_radio_status()
            for i, msg in enumerate(self._radio_messages):
                if msg.get("type") == "snapshot" and msg.get("request_id") == request_id:
                    self._radio_messages.pop(i)
                    return msg.get("metrics", {})
            if self._status.poll(min(0.02, max(0.0, deadline - time.monotonic()))):
                continue
        raise TimeoutError("radio metrics snapshot timed out")

    def _metrics_snapshot(self):
        radio = self._radio_final_metrics
        if radio is None and not self._closed:
            radio = self._request_radio_snapshot()
        return _merge_metric_snapshots(self.spec.name, self._node_metrics.snapshot(), radio)

    def close(self):
        if self._closed:
            return
        self._closed = True
        reason = "socket_closed" if self.elapsed() < self.plan.config.duration_s else "simulation_end"
        try:
            try:
                self._command.send({"op": "close", "reason": reason})
            except (BrokenPipeError, EOFError, OSError):
                pass
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and self._radio.is_alive():
                if self._status.poll(0.05):
                    self._drain_radio_status()
                self._radio.join(timeout=0.01)
            self._drain_radio_status()
            if self._radio.is_alive():
                self._radio.terminate()
                self._radio.join(timeout=1.0)
            if self._radio.exitcode not in (0, None) and self._fatal is None:
                self._fatal = f"radio child exited with code {self._radio.exitcode}"
        finally:
            self._sock.close()
            self._node_metrics.close()
            self._command.close()
            self._status.close()

        if self._fatal is not None:
            raise RuntimeError("NodeSocket radio child failed:\n" + self._fatal)
