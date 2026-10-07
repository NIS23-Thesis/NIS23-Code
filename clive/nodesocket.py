"""CLIVE NodeSocket: thesis-defined single-hop wireless UDP emulation.

This module is the network-emulation boundary used by FLAME.  It deliberately does
*not* implement FLAME discovery, membership, fragmentation, acknowledgements,
retransmission, sliding-window reliability, routing, or forwarding.  Those are protocol
responsibilities above NodeSocket.

NodeSocket applies the CLIVE methodology to UDP datagrams:

* direct localhost loopback UDP addressing (no address translation);
* topology from the shared precomputed mobility trace;
* distance -> RSSI using the log-distance path-loss equation;
* RSSI -> bandwidth / PLR / IPDV using Thesis Table 3-3;
* token-bucket transmission-rate limiting;
* thesis-specified probabilistic packet loss;
* thesis-specified Gaussian IPDV;
* transmit/receive energy estimates from Equations 3-13 and 3-14; and
* total and per-packet-type metrics plus packet-event logging.

A logical broadcast is counted as one protocol/radio transmission.  Receiver outcomes
are evaluated independently from each sender-receiver link because Table 3-3 defines link
conditions pairwise from their distance/RSSI.  The thesis defines no separate broadcast PHY
rate; therefore no artificial fixed broadcast-rate fraction and no weakest-receiver global
broadcast rate are used here.
"""
from __future__ import annotations

import errno
import hashlib
import heapq
import ipaddress
import json
import math
import random
import select
import socket
import threading
import time
from collections import defaultdict, deque
from itertools import count

from orchestrator import DEFAULT_PORT, finite

Address = tuple[str, int]
BROADCAST_IP = "255.255.255.255"
BROADCAST_ADDRESS = (BROADCAST_IP, DEFAULT_PORT)
MAX_DATAGRAM = 65507

# Thesis Table 3-3 thresholds and modifiers.
RSSI_UNREACHABLE_DBM = -85.0
RSSI_POOR_DBM = -75.0
RSSI_GOOD_DBM = -65.0


def link_conditions(distance_m, cfg, max_range_m=None):
    """Return CLIVE link conditions for a sender-to-receiver distance.

    ``range_mode='rssi'`` preserves Thesis Table 3-3 exactly. In
    ``range_mode='explicit'`` the sender's configured transmission range is the hard
    reachability boundary and the same strong/good/poor quality bands are scaled across
    that range. RSSI is still computed and logged for observability.
    """
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
            # Preserve the Table 3-3 relative quality regions while allowing the
            # experimenter to change the absolute sender range.
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



class TokenBucket:
    """Bit-based token bucket used to enforce CLIVE's emulated transmission rate.

    The bucket starts empty.  Consequently, a first packet of L bits on a rate-R link
    requires L/R seconds before it can be emitted.  That token wait is the transmission-rate
    delay; NodeSocket must not add L/R a second time after the tokens have been consumed.
    """

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

        # Credit elapsed time at the rate that applied during that elapsed interval.
        previous_rate = rate_bps if self.rate is None else self.rate
        self.tokens = min(
            self.capacity,
            self.tokens + (now - self.updated) * previous_rate,
        )
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
    """Equation 3-13: Etx = k(Eelec + Eamp * d^n)."""
    finite(bits, "bits", 0)
    finite(distance_m, "distance_m", 0)
    return bits * (
        cfg.e_elec_j_per_bit
        + cfg.e_amp_j_per_bit_m2 * float(distance_m) ** cfg.pathloss_exponent
    )


def energy_rx(bits, cfg):
    """Equation 3-14: Erx = Eelec * k."""
    finite(bits, "bits", 0)
    return bits * cfg.e_elec_j_per_bit


def parse_common_header(data):
    """Parse only the thesis-defined 4-byte common header for logging/metrics.

    Packet validation belongs to FLAME's Parsing Submodule, not NodeSocket.
    """
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
    """Thread-safe CLIVE packet metrics and JSONL event stream."""

    # The first six thesis-required quantities are represented by packets/bits/energy
    # sent and received.  Additional counters distinguish local emulator failures from
    # simulated wireless delivery loss without changing the required metrics.
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
        self.lock = threading.Lock()
        self.totals = {name: 0 for name in self.FIELDS}
        self.by_packet_type = defaultdict(
            lambda: {name: 0 for name in self.FIELDS}
        )
        self.stream = (
            open(log_path, "x", encoding="utf-8", buffering=1)
            if log_path
            else None
        )
        self.unknown_sources_rejected = 0

    def record(self, kind, *, header=None, counters=None, **fields):
        key = "unknown" if header is None else str(header["packet_type"])
        record = {
            "event": kind,
            "node_name": self.node_name,
            "common_header": header,
            # Counter deltas make live visualization possible without waiting for the
            # final metrics.json file. They mirror the same values applied below.
            "counters": dict(counters or {}),
            **fields,
        }
        with self.lock:
            if self.stream:
                self.stream.write(json.dumps(record, allow_nan=False) + "\n")
            for name, value in (counters or {}).items():
                self.totals[name] += value
                self.by_packet_type[key][name] += value

    def reject_unknown(self):
        # Unknown local UDP senders are not part of the emulation plan.  Avoid allowing
        # arbitrary host traffic to grow the packet log indefinitely.
        with self.lock:
            self.unknown_sources_rejected += 1

    def snapshot(self):
        with self.lock:
            return {
                "schema_version": 3,
                "node_name": self.node_name,
                **self.totals,
                "by_packet_type": {
                    key: dict(value) for key, value in self.by_packet_type.items()
                },
                "unknown_sources_rejected": self.unknown_sources_rejected,
                "accounting": (
                    "packets_admitted = accepted into software queue; packets_sent/bits_sent "
                    "= actual modeled radio emissions; kernel_datagrams_sent = successful "
                    "kernel sendto calls (not verified delivery); delivery_drops = "
                    "per-recipient modeled loss"
                ),
            }

    def close(self):
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = None


class NodeSocket:
    """Socket-like CLIVE endpoint implementing the thesis link-emulation model."""

    def __init__(self, node_spec, plan, seed=0, *, log_path=None):
        node_spec.validate()
        plan.validate()
        if node_spec not in plan.nodes:
            raise ValueError("node spec is not a member of the plan")

        self.spec = node_spec
        self.plan = plan
        self.cfg = plan.link_config
        self.trace = plan.trace

        self._address_of = {node.name: node.address for node in plan.nodes}
        self._name_of_address = {address: name for name, address in self._address_of.items()}

        # Convert the wall-clock experiment epoch into the local monotonic clock domain.
        self._epoch_mono = time.monotonic() + plan.epoch_start - time.time()

        self._cv = threading.Condition(threading.RLock())
        self._heap = []
        self._pending = {}
        self._queued_bytes = 0
        self._order = count()
        self._ids = count(1)
        self._closed = False
        self._fatal = None

        # Reproducible but node-specific random stream for PLR and IPDV.
        seed_bytes = hashlib.sha256(f"{seed}:{node_spec.name}".encode()).digest()[:8]
        self._rng = random.Random(int.from_bytes(seed_bytes, "big"))

        # One bucket per logical link plus a sender-wide hardware-rate ceiling.  The
        # latter prevents aggregate transmissions to different peers from exceeding the
        # configured maximum radio data rate.
        self._buckets = {}
        self._shared_bucket = TokenBucket(self.cfg.bucket_capacity_bytes * 8)
        # Sender-side per-recipient reservation for sustained broadcast bandwidth.
        self._broadcast_pair_finish = {}

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind(node_spec.address)
            self._sock.setblocking(False)
            self.metrics = MetricsCollector(node_spec.name, log_path)
        except BaseException:
            self._sock.close()
            raise

        self._sender = threading.Thread(
            target=self._sender_loop,
            daemon=True,
            name=f"clive-tx-{node_spec.name}",
        )
        self._sender.start()

    # ------------------------------------------------------------------
    # Shared experiment clock / mobility
    # ------------------------------------------------------------------

    def set_epoch(self, epoch_start):
        """Arm this endpoint to the supervisor-provided shared experiment epoch."""
        finite(epoch_start, "epoch_start", 0, strictly=True)
        with self._cv:
            if self._pending:
                raise RuntimeError("cannot change epoch with pending transmissions")
            self.plan.epoch_start = epoch_start
            self._epoch_mono = time.monotonic() + epoch_start - time.time()

    def elapsed(self):
        return time.monotonic() - self._epoch_mono

    def _current_mobility_tick(self):
        """Mobility replay index derived from experiment time; never drives protocol code."""
        return max(0, int(self.elapsed() / self.plan.mobility_config.tick_interval))

    def _distance_to(self, name, tick=None):
        tick = self._current_mobility_tick() if tick is None else tick
        if tick not in self.trace:
            raise RuntimeError(f"mobility trace has no current tick {tick}")
        try:
            source = self.trace[tick][self.spec.name]
            target = self.trace[tick][name]
        except KeyError as exc:
            raise RuntimeError(
                f"mobility trace tick {tick} is missing node {exc.args[0]}"
            ) from exc
        return math.hypot(source[0] - target[0], source[1] - target[1])

    def _time_to_next_tick(self):
        quantum = self.plan.mobility_config.tick_interval
        elapsed = self.elapsed()
        next_boundary = (math.floor(max(0.0, elapsed) / quantum) + 1) * quantum
        return max(0.0, next_boundary - elapsed)

    # ------------------------------------------------------------------
    # Socket compatibility / health
    # ------------------------------------------------------------------

    def fileno(self):
        return self._sock.fileno()

    def check_health(self):
        if self._fatal is not None:
            raise RuntimeError("NodeSocket background sender failed") from self._fatal

    def wait_readable(self, timeout):
        self.check_health()
        if timeout <= 0:
            return False
        return bool(select.select([self._sock], [], [], timeout)[0])

    # ------------------------------------------------------------------
    # Thesis stochastic link rules
    # ------------------------------------------------------------------

    def _packet_is_lost(self, plr):
        """Apply the exact Table 4-3 PLR procedure (integer roll 0..999)."""
        finite(plr, "plr", 0)
        if plr > 1:
            raise ValueError("plr must be <= 1")
        rolled = self._rng.randrange(1000)  # 0..999 inclusive
        threshold = plr * 1000.0
        return rolled < threshold, rolled, threshold

    def _sample_ipdv_s(self, max_ipdv_ms):
        """Apply Table 4-3: random.gauss(0.0, max_ipdv/3.0), returned in seconds."""
        if not self.cfg.ipdv_enabled or max_ipdv_ms is None:
            return 0.0
        finite(max_ipdv_ms, "max_ipdv_ms", 0)
        return self._rng.gauss(0.0, max_ipdv_ms / 3.0) / 1000.0

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

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

    def sendto(self, data, destination):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        data = bytes(data)
        if len(data) > MAX_DATAGRAM:
            raise OSError(errno.EMSGSIZE, "IPv4 UDP payload exceeds 65507 bytes")

        destination = self._normalize_destination(destination)
        broadcast = destination[0] == BROADCAST_IP
        if broadcast:
            # FLAME HELLO uses the broadcast address.  CLIVE realizes the broadcast on
            # localhost by evaluating every matching virtual receiver and sending one local UDP datagram per reachable target.
            targets = [
                node.name
                for node in self.plan.nodes
                if node.name != self.spec.name
                and node.port == destination[1]
            ]
        else:
            name = self._name_of_address.get(destination)
            if name is None:
                raise ValueError(f"unknown localhost node address: {destination}")
            targets = [name]

        with self._cv:
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
                "targets": targets,
                "header": parse_common_header(data),
                "bits": len(data) * 8,
                "queued_at_mono": time.monotonic(),
                "emitted": False,
                "outstanding": 0,
            }
            self._event(
                req,
                "attempt",
                {
                    "packets_attempted": 1,
                    "bits_attempted": req["bits"],
                },
            )

            if (
                len(self._pending) >= self.plan.config.max_queue_packets
                or self._queued_bytes + len(data) > self.plan.config.max_queue_bytes
            ):
                self._event(
                    req,
                    "local_drop",
                    {"packets_dropped": 1},
                    reason="queue_full",
                )
                raise BlockingIOError(errno.ENOBUFS, "emulated send queue is full")

            self._pending[req["id"]] = req
            self._queued_bytes += len(data)
            self._event(req, "admit", {"packets_admitted": 1, "bits_admitted": req["bits"]})
            self._push(time.monotonic(), "tx", req, None)

        # Match socket.sendto(): return bytes accepted by the local socket interface.
        return len(data)

    def _push(self, when, action, req, target):
        heapq.heappush(
            self._heap,
            (when, next(self._order), action, req, target),
        )
        self._cv.notify()

    def _finish(self, req):
        if self._pending.pop(req["id"], None) is not None:
            self._queued_bytes -= len(req["data"])

    def _links_for_request(self, req, tick):
        links = []
        sender_range = self.cfg.tx_range_m(self.spec)
        for name in req["targets"]:
            distance = self._distance_to(name, tick)
            links.append(
                {
                    "name": name,
                    "distance_m": distance,
                    "conditions": link_conditions(distance, self.cfg, sender_range),
                }
            )
        return links

    def _radio_rate_and_distance(self, req, links):
        """Return the sender-side serialization rate and TX-energy distance.

        Unicast has one pairwise link, so Table 3-3 directly supplies its effective
        bandwidth.  If that link is unreachable, the attempted frame is still emitted by the
        local radio; the link itself remains logged as 0 bps and every receiver outcome drops.

        Broadcast is one logical transmission, not N unicasts.  The thesis does not define a
        separate broadcast PHY/rate rule, so NodeSocket must not choose a receiver (for
        example, the weakest reachable receiver) to set one global broadcast bandwidth.
        Instead the sender is paced once at the configured radio ceiling and each receiver's
        Table 3-3 bandwidth is applied independently as receiver-specific delivery delay in
        _transmit().  Thus one broadcast counts once while each A->receiver link keeps its own
        thesis-defined conditions.

        Equation 3-13 requires one communication distance for the single TX-energy charge.
        The methodology does not state how to choose d for a one-to-many broadcast.  The
        explicit convention here is the farthest *currently reachable* receiver: one emission
        is charged once at the distance needed to cover all receivers that can presently hear
        it.  This is logged so the assumption is visible in experimental output.
        """
        if not req["broadcast"]:
            if not links:
                return self.cfg.max_data_rate_bps, 0.0, 0.0
            link = links[0]
            link_rate = link["conditions"]["bandwidth_bps"]
            airtime_rate = link_rate or self.cfg.max_data_rate_bps
            return airtime_rate, link["distance_m"], link_rate

        reachable = [link for link in links if link["conditions"]["reachable"]]
        energy_distance = (
            max(link["distance_m"] for link in reachable) if reachable else 0.0
        )
        return self.cfg.max_data_rate_bps, energy_distance, None

    def _transmit(self, req):
        tick = self._current_mobility_tick()
        links = self._links_for_request(req, tick)
        airtime_rate, energy_distance, unicast_link_rate = self._radio_rate_and_distance(
            req, links
        )

        now = time.monotonic()
        if self.elapsed() >= self.plan.config.duration_s:
            self._cancel_pending("simulation_end")
            return

        # An out-of-range destination is not a kernel UDP transmission. Reject it
        # before consuming rate tokens or incrementing logical radio emissions.
        if not any(link["conditions"]["reachable"] for link in links):
            if not links:
                self._event(req, "local_drop", {"packets_dropped": 1}, reason="no_receivers")
            for link in links:
                conditions = link["conditions"]
                self.metrics.record(
                    "delivery_attempt", header=req["header"],
                    counters={"delivery_attempts": 1}, packet_id=req["id"],
                    time_s=self.elapsed(), source=self.spec.name,
                    destination=list(self._address_of[link["name"]]),
                    distance_m=link["distance_m"],
                    rssi_dbm=conditions["rssi_dbm"],
                    bandwidth_bps=conditions["bandwidth_bps"],
                    plr=conditions["plr"], ipdv_ms=conditions["ipdv_ms"], mobility_mobility_tick=tick,
                )
                self._drop_delivery(req, link["name"], "unreachable",
                                    conditions=conditions, distance_m=link["distance_m"])
            self._finish(req)
            return

        bucket_key = (
            ("broadcast", req["destination"][1])
            if req["broadcast"]
            else req["targets"][0]
        )
        bucket = self._buckets.setdefault(
            bucket_key,
            TokenBucket(self.cfg.bucket_capacity_bytes * 8, now=now),
        )

        # Token-bucket waiting is the bandwidth-induced queuing/transmission delay.
        link_wait = bucket.wait_time(req["bits"], airtime_rate, now)
        radio_wait = self._shared_bucket.wait_time(
            req["bits"], self.cfg.max_data_rate_bps, now
        )
        wait = max(link_wait, radio_wait)

        if wait > 1e-9:
            # If a long queue ever spans a mobility quantum, wake at the next tick so the
            # distance/RSSI/rate can be recomputed rather than using stale topology.
            next_tick_wait = self._time_to_next_tick()
            if next_tick_wait > 1e-9:
                wait = min(wait, next_tick_wait)
            self._push(now + wait, "tx", req, None)
            return

        if not bucket.consume(req["bits"], airtime_rate, now):
            raise RuntimeError("link token accounting invariant failed")
        if not self._shared_bucket.consume(
            req["bits"], self.cfg.max_data_rate_bps, now
        ):
            raise RuntimeError("shared token accounting invariant failed")

        req["emitted"] = True
        queue_and_tx_delay = now - req["queued_at_mono"]
        tx_energy = energy_tx(req["bits"], energy_distance, self.cfg)
        self._event(
            req,
            "transmit",
            {
                "packets_sent": 1,
                "bits_sent": req["bits"],
                "energy_sent_j": tx_energy,
            },
            distance_m=(energy_distance if req["broadcast"] else links[0]["distance_m"]),
            energy_distance_m=energy_distance,
            energy_distance_policy=(
                "farthest_reachable_receiver" if req["broadcast"] else "unicast_peer"
            ),
            mobility_tick=tick,
            # A broadcast has no single receiver-derived Table 3-3 bandwidth.  Its
            # per-receiver bandwidths are recorded on delivery_attempt/scheduled_delivery.
            bandwidth_bps=(None if req["broadcast"] else unicast_link_rate),
            airtime_rate_bps=airtime_rate,
            queue_and_transmission_delay_s=queue_and_tx_delay,
        )

        # Evaluate each receiver independently from its pairwise Table 3-3 link.
        for link in links:
            name = link["name"]
            distance = link["distance_m"]
            conditions = link["conditions"]

            self.metrics.record(
                "delivery_attempt",
                header=req["header"],
                counters={"delivery_attempts": 1},
                packet_id=req["id"],
                time_s=self.elapsed(),
                source=self.spec.name,
                destination=list(self._address_of[name]),
                distance_m=distance,
                rssi_dbm=conditions["rssi_dbm"],
                bandwidth_bps=conditions["bandwidth_bps"],
                plr=conditions["plr"],
                ipdv_ms=conditions["ipdv_ms"],
                mobility_tick=tick,
            )

            lost, rolled, threshold = self._packet_is_lost(conditions["plr"])
            if lost:
                reason = "unreachable" if not conditions["reachable"] else "plr"
                self._drop_delivery(
                    req,
                    name,
                    reason,
                    rolled=rolled,
                    threshold=threshold,
                    conditions=conditions,
                    distance_m=distance,
                )
                continue

            # Unicast has already waited its full L/R in the pairwise token bucket.
            # Broadcast has only been serialized once at the sender's configured maximum;
            # add just enough receiver-specific delay for the total serialization component
            # to equal L/R for this receiver's own Table 3-3 bandwidth.  This avoids both
            # the old "weakest receiver controls everyone" rule and counting N broadcasts.
            if req["broadcast"]:
                # Previous code delayed each broadcast packet by L/R once, which
                # did not cap *sustained* throughput at weaker recipients. A
                # per-recipient serialization reservation enforces each Table 3-3
                # pairwise rate without charging N logical radio transmissions.
                pair_rate = conditions["bandwidth_bps"]
                sender_serialization = req["bits"] / airtime_rate
                prior_finish = self._broadcast_pair_finish.get(name, now - sender_serialization)
                pair_finish = max(now - sender_serialization, prior_finish) + req["bits"] / pair_rate
                self._broadcast_pair_finish[name] = pair_finish
                pairwise_bandwidth_delay = max(0.0, pair_finish - now)
            else:
                pairwise_bandwidth_delay = 0.0

            # Chapter 3's link table then adds IPDV.  A negative jitter sample may not make
            # delivery occur earlier than the link's bandwidth-imposed transmission time.
            variation = self._sample_ipdv_s(conditions["ipdv_ms"])
            extra_delay = (
                pairwise_bandwidth_delay + self.cfg.base_delay_s +
                distance / self.cfg.propagation_speed_mps + variation
            )
            extra_delay = max(pairwise_bandwidth_delay, extra_delay)

            req["outstanding"] += 1
            self._push(now + extra_delay, "deliver", req, name)
            self._event(
                req,
                "scheduled_delivery",
                target=list(self._address_of[name]),
                distance_m=distance,
                rssi_dbm=conditions["rssi_dbm"],
                bandwidth_bps=conditions["bandwidth_bps"],
                plr=conditions["plr"],
                ipdv_s=variation,
                propagation_delay_s=distance / self.cfg.propagation_speed_mps,
                pairwise_bandwidth_delay_s=pairwise_bandwidth_delay,
                post_transmission_delay_s=extra_delay,
            )

        if not req["outstanding"]:
            self._finish(req)

    def _drop_delivery(
        self,
        req,
        name,
        reason,
        *,
        rolled=None,
        threshold=None,
        conditions=None,
        distance_m=None,
    ):
        fields = {
            "target": list(self._address_of[name]),
            "reason": reason,
        }
        if rolled is not None:
            fields["plr_roll"] = rolled
        if threshold is not None:
            fields["plr_threshold"] = threshold
        if conditions is not None:
            fields.update(
                rssi_dbm=conditions["rssi_dbm"],
                bandwidth_bps=conditions["bandwidth_bps"],
                plr=conditions["plr"],
                ipdv_ms=conditions["ipdv_ms"],
            )
        if distance_m is not None:
            fields["distance_m"] = distance_m
        self._event(
            req,
            "delivery_drop",
            {"delivery_drops": 1},
            **fields,
        )

    def _deliver(self, req, name):
        try:
            self._sock.sendto(req["data"], self._address_of[name])
        except OSError as exc:
            self._drop_delivery(req, name, f"socket_error:{exc.errno}")
            self._event(
                req,
                "socket_error",
                {"socket_errors": 1},
                error=str(exc),
            )
            # An actual OS/socket transport failure is not simulated MANET loss; invalidate
            # the run instead of silently treating it as radio PLR.
            raise
        else:
            self._event(
                req,
                "kernel_send",
                {"kernel_datagrams_sent": 1},
                target=list(self._address_of[name]),
            )

        req["outstanding"] -= 1
        if req["outstanding"] == 0:
            self._finish(req)

    def _cancel_pending(self, reason):
        # Deliveries already emitted onto the modeled radio but not yet forwarded to the
        # the local UDP socket become per-recipient losses.  Requests not yet emitted are local drops.
        seen_deliveries = set()
        for _, _, action, req, name in self._heap:
            if action == "deliver":
                key = (req["id"], name)
                if key not in seen_deliveries:
                    seen_deliveries.add(key)
                    self._drop_delivery(req, name, reason)

        for req in list(self._pending.values()):
            if not req["emitted"]:
                self._event(
                    req,
                    "local_drop",
                    {"packets_dropped": 1},
                    reason=reason,
                )

        self._heap.clear()
        self._pending.clear()
        self._queued_bytes = 0

    def _sender_loop(self):
        try:
            with self._cv:
                while not self._closed:
                    if not self._heap:
                        self._cv.wait(0.05)
                        continue

                    if self.elapsed() >= self.plan.config.duration_s:
                        self._cancel_pending("simulation_end")
                        continue

                    when, _, action, req, target = self._heap[0]
                    wait = when - time.monotonic()
                    if wait > 0:
                        remaining = self.plan.config.duration_s - self.elapsed()
                        self._cv.wait(min(wait, max(0.0, remaining)))
                        continue

                    heapq.heappop(self._heap)
                    if action == "tx":
                        self._transmit(req)
                    elif action == "deliver":
                        self._deliver(req, target)
                    else:
                        raise RuntimeError(f"unknown NodeSocket action {action!r}")
        except BaseException as exc:
            self._fatal = exc

    # ------------------------------------------------------------------
    # Receiving / direct localhost UDP
    # ------------------------------------------------------------------

    def recvfrom(self, bufsize=65535):
        if type(bufsize) is not int or bufsize <= 0:
            raise ValueError("bufsize must be a positive integer")
        self.check_health()

        for _ in range(self.plan.config.receive_batch_size):
            try:
                # Read the complete legal IPv4 UDP payload for correct packet/bit metrics,
                # then expose socket-compatible truncation to the caller via bufsize.
                data, address = self._sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                return None

            name = self._name_of_address.get(address)
            if name is None:
                self.metrics.reject_unknown()
                continue
            if not 0 <= self.elapsed() < self.plan.config.duration_s:
                # Late host datagrams must not contaminate experiment metrics.
                continue

            self.metrics.record(
                "receive",
                header=parse_common_header(data),
                counters={
                    "packets_received": 1,
                    "bits_received": len(data) * 8,
                    "energy_received_j": energy_rx(len(data) * 8, self.cfg),
                },
                time_s=self.elapsed(),
                source=list(self._address_of[name]),
                mobility_tick=self._current_mobility_tick(),
            )
            return data[:bufsize], self._address_of[name]

        return None

    def close(self):
        try:
            with self._cv:
                if self._closed:
                    return
                self._closed = True
                self._cv.notify_all()
                reason = (
                    "socket_closed"
                    if self.elapsed() < self.plan.config.duration_s
                    else "simulation_end"
                )
                self._cancel_pending(reason)
        finally:
            self._sender.join(timeout=1.0)
            self._sock.close()
            self.metrics.close()

        self.check_health()
