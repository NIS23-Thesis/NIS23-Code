"""
CLIVE Orchestrator.

Prepares an emulation plan containing:
- node configuration and overlay/underlay addresses
- precomputed Gauss-Markov mobility
- link configuration
- experiment configuration
- a shared emulation start epoch

The Orchestrator does not run nodes or implement protocol behavior.
"""

from __future__ import annotations

import enum
import ipaddress
import json
import math
import os
import random
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


class NodeRole(enum.IntEnum):
    SENSOR_NODE = 0
    CLUSTER_HEAD = 1


DEFAULT_PORT = 27062
LAN_BASE_PORT = 16000
RESERVED_NODE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def finite(value, name, minimum=None, strictly=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and (value <= minimum if strictly else value < minimum):
        raise ValueError(f"{name} is out of range")


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def atomic_json(path, value):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=parent, prefix=".clive-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class NodeSpec:
    name: str
    role: NodeRole
    overlay_ip: str
    overlay_port: int
    underlay_ip: str
    underlay_port: int

    def validate(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.name):
            raise ValueError("node name must be 1-64 ASCII letters, digits, underscores or hyphens")
        if self.name.upper() in RESERVED_NODE_NAMES:
            raise ValueError("reserved node filename")
        if type(self.role) not in (int, NodeRole) or self.role not in (0, 1):
            raise ValueError("invalid node role")
        for name in ("overlay_ip", "underlay_ip"):
            value = getattr(self, name)
            address = ipaddress.IPv4Address(value)
            if str(address) != value or address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
                raise ValueError(f"{name} must be a canonical unicast IPv4 address")
        for name in ("overlay_port", "underlay_port"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError(f"{name} must be an integer between 1 and 65535")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["role"] = int(self.role)
        return value

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodeSpec":
        return cls(**d)


@dataclass
class LinkConfig:
    pathloss_exponent: float = 3.0
    rssi0_dbm: float = -30.0
    d0_m: float = 1.0
    max_data_rate_bps: float = 1e9
    propagation_speed_mps: float = 299792458.0
    base_delay_s: float = 0.0
    e_elec_j_per_bit: float = 50e-9
    # Kept under the thesis' notation/name. With n != 2, the document's
    # dimensional units for this coefficient are formally ambiguous.
    e_amp_j_per_bit_m2: float = 100e-9
    bucket_capacity_bytes: int = 65507
    ipdv_enabled: bool = True

    def validate(self) -> None:
        for name in ("pathloss_exponent", "rssi0_dbm", "d0_m", "max_data_rate_bps", "propagation_speed_mps", "base_delay_s", "e_elec_j_per_bit", "e_amp_j_per_bit_m2"):
            finite(getattr(self, name), name)
        positive_int(self.bucket_capacity_bytes, "bucket_capacity_bytes")
        if self.bucket_capacity_bytes < 65507:
            raise ValueError("bucket capacity must accommodate the maximum IPv4 UDP payload (65507 bytes)")
        if type(self.ipdv_enabled) is not bool:
            raise ValueError("ipdv_enabled must be boolean")
        if self.pathloss_exponent <= 0:
            raise ValueError("pathloss_exponent must be > 0")
        if self.d0_m <= 0:
            raise ValueError("d0_m must be > 0")
        if self.max_data_rate_bps <= 0:
            raise ValueError("max_data_rate_bps must be > 0")
        if self.propagation_speed_mps <= 0:
            raise ValueError("propagation_speed_mps must be > 0")
        if self.base_delay_s < 0:
            raise ValueError("base_delay_s must be >= 0")
        if self.e_elec_j_per_bit < 0 or self.e_amp_j_per_bit_m2 < 0:
            raise ValueError("energy coefficients must be >= 0")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        obj = cls(**d)
        obj.validate()
        return obj


@dataclass
class MobilityConfig:
    # The methodology specifies an adjustable 100-500 ms quantum.
    tick_interval: float = 0.1
    alpha: float = 0.5
    area_width_m: float = 200.0
    area_height_m: float = 200.0
    ch_mean_speed_mps: float = 0.0
    sn_mean_speed_mps: float = 0.0
    speed_stddev: float = 1.0
    direction_stddev: float = 0.5
    pause_time_range: Optional[Tuple[float, float]] = None
    pause_trigger: str = "zero_speed"
    move_time_range: Optional[Tuple[float, float]] = None

    def validate(self) -> None:
        for name in ("tick_interval", "alpha", "area_width_m", "area_height_m", "ch_mean_speed_mps", "sn_mean_speed_mps", "speed_stddev", "direction_stddev"):
            finite(getattr(self, name), name)
        if self.pause_trigger not in {"zero_speed", "periodic"}:
            raise ValueError("pause_trigger must be zero_speed or periodic")
        if self.pause_trigger == "periodic" and (self.pause_time_range is None or self.move_time_range is None):
            raise ValueError("periodic pauses require pause_time_range and move_time_range")
        if self.move_time_range is not None:
            if len(self.move_time_range) != 2:
                raise ValueError("move_time_range requires two values")
            for value in self.move_time_range:
                finite(value, "move_time_range", 0, strictly=True)
            if self.move_time_range[1] < self.move_time_range[0]:
                raise ValueError("invalid move_time_range")
        if not 0.1 <= self.tick_interval <= 0.5:
            raise ValueError("tick_interval must be between 0.1 and 0.5 seconds")
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be between 0 and 1")
        if self.area_width_m <= 0 or self.area_height_m <= 0:
            raise ValueError("simulation area dimensions must be > 0")
        finite(2*self.area_width_m, "twice area width")
        finite(2*self.area_height_m, "twice area height")
        if self.ch_mean_speed_mps < 0 or self.sn_mean_speed_mps < 0:
            raise ValueError("mean speeds must be >= 0")
        if self.speed_stddev < 0 or self.direction_stddev < 0:
            raise ValueError("mobility standard deviations must be >= 0")
        if self.pause_time_range is not None:
            if len(self.pause_time_range) != 2:
                raise ValueError("pause_time_range must contain (min_seconds, max_seconds)")
            lo, hi = self.pause_time_range
            finite(lo, "pause minimum")
            finite(hi, "pause maximum")
            if lo < 0 or hi < lo:
                raise ValueError("pause_time_range must satisfy 0 <= min <= max")

    def to_dict(self):
        value = asdict(self)
        if self.pause_time_range is not None:
            value["pause_time_range"] = list(self.pause_time_range)
        return value

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        pause = d.pop("pause_time_range", None)
        obj = cls(
            pause_time_range=tuple(pause) if pause is not None else None,
            **d,
        )
        obj.validate()
        return obj


@dataclass
class EmulationConfig:
    duration_s: float = 300.0
    mode: str = "localhost"
    seed: int = 0
    startup_delay_s: float = 2.0
    io_poll_interval_s: float = 0.002
    receive_batch_size: int = 64
    receive_budget_s: float = 0.002
    max_queue_packets: int = 4096
    max_queue_bytes: int = 8 * 1024 * 1024
    log_tail_records: int = 256
    worker_grace_s: float = 5.0
    readiness_timeout_s: float = 30.0
    max_clock_offset_s: float = 0.01

    def validate(self) -> None:
        for name in ("duration_s", "startup_delay_s", "io_poll_interval_s", "receive_budget_s", "worker_grace_s", "readiness_timeout_s", "max_clock_offset_s"):
            finite(getattr(self, name), name, 0, strictly=True)
        for name in ("receive_batch_size", "max_queue_packets", "max_queue_bytes", "log_tail_records"):
            positive_int(getattr(self, name), name)
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if self.mode not in {"localhost", "lan"}:
            raise ValueError("mode must be either 'localhost' or 'lan'")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        obj = cls(**d)
        obj.validate()
        return obj


@dataclass
class EmulationPlan:
    nodes: List[NodeSpec]
    trace: Dict[int, Dict[str, List[float]]]
    link_config: LinkConfig
    mobility_config: MobilityConfig
    config: EmulationConfig
    epoch_start: float

    def to_dict(self):
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "trace": {str(k): v for k, v in self.trace.items()},
            "link_config": self.link_config.to_dict(),
            "mobility_config": self.mobility_config.to_dict(),
            "config": self.config.to_dict(),
            "epoch_start": self.epoch_start,
        }

    @classmethod
    def from_dict(cls, d):
        plan = cls(
            [NodeSpec.from_dict(n) for n in d["nodes"]],
            {int(k): v for k, v in d["trace"].items()},
            LinkConfig.from_dict(d["link_config"]),
            MobilityConfig.from_dict(d["mobility_config"]),
            EmulationConfig.from_dict(d["config"]),
            d["epoch_start"],
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        self.link_config.validate()
        self.mobility_config.validate()
        self.config.validate()
        if not self.nodes:
            raise ValueError("an emulation plan must contain at least one node")
        if not self.trace:
            raise ValueError("an emulation plan must contain a mobility trace")
        finite(self.epoch_start, "epoch_start", 0, strictly=True)
        for node in self.nodes:
            node.validate()
            if self.config.mode == "localhost" and not ipaddress.IPv4Address(node.underlay_ip).is_loopback:
                raise ValueError("localhost mode requires loopback underlay addresses")

        names = [n.name for n in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("node names must be unique")

        overlays = [(n.overlay_ip, n.overlay_port) for n in self.nodes]
        if len(overlays) != len(set(overlays)):
            raise ValueError("overlay addresses must be unique")

        underlays = [(n.underlay_ip, n.underlay_port) for n in self.nodes]
        if len(underlays) != len(set(underlays)):
            raise ValueError("underlay addresses must be unique")

        expected_ticks = int(self.config.duration_s / self.mobility_config.tick_interval) + 1
        if len(self.trace) < expected_ticks:
            raise ValueError(
                f"mobility trace is incomplete: expected at least {expected_ticks} ticks, "
                f"got {len(self.trace)}"
            )

        expected_names = set(names)
        for tick in range(expected_ticks):
            positions = self.trace.get(tick)
            if positions is None:
                raise ValueError(f"mobility trace is missing tick {tick}")
            actual_names = set(positions)
            if actual_names != expected_names:
                missing = sorted(expected_names - actual_names)
                extra = sorted(actual_names - expected_names)
                raise ValueError(
                    f"mobility trace tick {tick} has inconsistent nodes; "
                    f"missing={missing}, extra={extra}"
                )
            for name, position in positions.items():
                if not isinstance(position, (list, tuple)) or len(position) != 2:
                    raise ValueError(f"invalid coordinates for {name} at tick {tick}")
                for value in position:
                    finite(value, f"coordinates for {name}")
                if not (0 <= position[0] <= self.mobility_config.area_width_m and 0 <= position[1] <= self.mobility_config.area_height_m):
                    raise ValueError(f"coordinates outside simulation area for {name}")

    def save(self, path: str):
        self.validate()
        atomic_json(path, self.to_dict())

    def save_trace(self, path):
        """Documented row-per-tick interchange: elapsed seconds, then each x/y pair."""
        self.validate()
        with open(path, "x", encoding="utf-8") as stream:
            stream.write("time_s\t" + "\t".join(f"{n.name}_x\t{n.name}_y" for n in self.nodes) + "\n")
            for tick in sorted(self.trace):
                values = [self.time_for_tick(tick)]
                for node in self.nodes:
                    values.extend(self.trace[tick][node.name])
                stream.write("\t".join(format(value, ".12g") for value in values) + "\n")

    @classmethod
    def load(cls, path: str):
        with open(path) as f:
            return cls.from_dict(json.load(f, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON value {value}"))))

    def node_by_name(self, name: str) -> NodeSpec:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)

    def time_for_tick(self, tick: int) -> float:
        return tick * self.mobility_config.tick_interval


class GaussMarkovNode:
    """Precomputed Gauss-Markov mobility state for one node.

    The thesis adds a discrete pause duration to Gauss-Markov but does not define a
    separate pause-arrival process. To avoid inventing an additional experiment
    parameter, a configured pause is entered when the stochastic speed update reaches
    zero after clamping. The pause duration is sampled uniformly from
    ``pause_time_range`` and is fully deterministic for a fixed experiment seed.
    """

    def __init__(self, mean_speed, x, y, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.mean_speed = mean_speed
        self.noise_scale = math.sqrt(max(0.0, 1 - cfg.alpha * cfg.alpha))

        self.x = x
        self.y = y

        self.speed = mean_speed
        self.mean_direction = rng.uniform(0, 2 * math.pi)
        self.direction = self.mean_direction
        self.pause_remaining_s = 0.0
        self.move_remaining_s = rng.uniform(*cfg.move_time_range) if cfg.pause_trigger == "periodic" else None

    def step(self, dt):
        if self.mean_speed <= 0:
            self.speed = 0.0
            return

        if self.pause_remaining_s > 0:
            self.pause_remaining_s = max(0.0, self.pause_remaining_s - dt)
            return

        if self.move_remaining_s is not None:
            if self.move_remaining_s <= 0:
                self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                self.move_remaining_s = self.rng.uniform(*self.cfg.move_time_range)
                return
            self.move_remaining_s -= dt

        a = self.cfg.alpha

        speed_noise = self.rng.gauss(
            0.0,
            self.cfg.speed_stddev * self.mean_speed,
        )
        direction_noise = self.rng.gauss(
            0.0,
            self.cfg.direction_stddev,
        )

        self.speed = a * self.speed + (1 - a) * self.mean_speed + self.noise_scale * speed_noise
        self.direction = a * self.direction + (1 - a) * self.mean_direction + self.noise_scale * direction_noise

        self.speed = max(0.0, self.speed)

        if self.speed == 0.0 and self.cfg.pause_time_range is not None and self.cfg.pause_trigger == "zero_speed":
            self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
            return

        self.x += self.speed * math.cos(self.direction) * dt
        self.y += self.speed * math.sin(self.direction) * dt
        self._bounce()

    def _bounce(self):
        w = self.cfg.area_width_m
        h = self.cfg.area_height_m
        # Constant-time reflection avoids an unbounded loop for extreme inputs.
        def reflect(position, bound):
            finite(position, "computed mobility position")
            if 0 <= position <= bound:
                return position, False
            remainder = position % (2*bound)
            flipped = remainder > bound or (remainder == 0 and position > 0) or (remainder == bound and position < 0)
            return min(remainder, 2*bound-remainder), flipped
        self.x, flip_x = reflect(self.x, w)
        self.y, flip_y = reflect(self.y, h)
        if flip_x:
            self.direction = math.pi-self.direction
        if flip_y:
            self.direction = -self.direction


def precompute_trace(node_names, roles, cfg, duration_s, seed):
    cfg.validate()
    if duration_s <= 0:
        raise ValueError("duration_s must be > 0")

    rng = random.Random(seed)
    states = {}
    for name in node_names:
        mean_speed = cfg.ch_mean_speed_mps if roles[name] == NodeRole.CLUSTER_HEAD else cfg.sn_mean_speed_mps
        states[name] = GaussMarkovNode(mean_speed, rng.uniform(0, cfg.area_width_m),
                                       rng.uniform(0, cfg.area_height_m), cfg, rng)

    dt = cfg.tick_interval
    ticks = int(duration_s / dt) + 1
    trace: Dict[int, Dict[str, List[float]]] = {}

    for tick in range(ticks):
        trace[tick] = {
            name: [state.x, state.y]
            for name, state in states.items()
        }
        for state in states.values():
            state.step(dt)

    return trace


def build_nodes(
    num_ch: int,
    num_sn: int,
    mode: str = "localhost",
    underlay_ip_by_node: Optional[Mapping[str, str]] = None,
):
    if type(num_ch) is not int or type(num_sn) is not int or num_ch < 0 or num_sn < 0 or num_ch + num_sn <= 0:
        raise ValueError("num_ch and num_sn must be non-negative and total at least 1")
    if mode not in {"localhost", "lan"}:
        raise ValueError("mode must be either 'localhost' or 'lan'")
    if num_ch + num_sn > 65534:
        raise ValueError("maximum supported node count is 65534")

    names = (
        [(f"CH-{i + 1}", NodeRole.CLUSTER_HEAD) for i in range(num_ch)]
        + [(f"SN-{i + 1}", NodeRole.SENSOR_NODE) for i in range(num_sn)]
    )

    if mode == "lan":
        if not underlay_ip_by_node:
            raise ValueError(
                "LAN mode requires scenario['underlay_ip_by_node'], an explicit "
                "mapping from every node name to the physical host IPv4 address"
            )
        missing = [name for name, _ in names if name not in underlay_ip_by_node]
        if missing:
            raise ValueError(f"LAN underlay mapping is missing nodes: {missing}")

    nodes = []
    per_host_next_port: Dict[str, int] = {}
    loopback_base = int(ipaddress.IPv4Address("127.0.0.0"))
    overlay_base = int(ipaddress.IPv4Address("10.0.0.0"))

    for index, (name, role) in enumerate(names, start=2):
        if mode == "localhost":
            overlay_ip = str(ipaddress.IPv4Address(loopback_base + index))
            underlay_ip = overlay_ip
            overlay_port = DEFAULT_PORT
            underlay_port = DEFAULT_PORT
        else:
            overlay_ip = str(ipaddress.IPv4Address(overlay_base + index - 1))
            overlay_port = DEFAULT_PORT
            underlay_ip = str(underlay_ip_by_node[name])
            underlay_port = per_host_next_port.get(underlay_ip, LAN_BASE_PORT)
            per_host_next_port[underlay_ip] = underlay_port + 1

        nodes.append(NodeSpec(name, role, overlay_ip, overlay_port, underlay_ip, underlay_port))

    for node in nodes:
        node.validate()
    return nodes


class Orchestrator:
    def prepare(self, scenario=None) -> EmulationPlan:
        scenario = scenario or {}

        config = EmulationConfig(
            duration_s=scenario.get("duration", 300.0),
            mode=str(scenario.get("mode", "localhost")).lower(),
            seed=scenario.get("seed", 0),
            startup_delay_s=scenario.get("startup_delay_s", 2.0),
            io_poll_interval_s=scenario.get("io_poll_interval_s", 0.002),
            **scenario.get("runtime", {}),
        )
        link = LinkConfig(**scenario.get("link", {}))
        mobility = MobilityConfig(**scenario.get("mobility", {}))
        for item in (config, link, mobility):
            item.validate()

        nodes = build_nodes(
            scenario.get("num_ch", 2),
            scenario.get("num_sn", 6),
            config.mode,
            scenario.get("underlay_ip_by_node"),
        )

        roles = {node.name: node.role for node in nodes}
        trace = precompute_trace(list(roles), roles, mobility, config.duration_s, config.seed)
        plan = EmulationPlan(nodes, trace, link, mobility, config, time.time() + config.startup_delay_s)
        plan.validate()
        return plan
