"""CLIVE Orchestrator - localhost-only implementation.

The Orchestrator prepares a reproducible emulation plan containing:
- localhost node addresses (one loopback IPv4 address per virtual node),
- a precomputed Gauss-Markov mobility trace,
- link-emulation parameters, and
- a shared future experiment epoch.

It does not execute FLAME or any other protocol. Protocol behavior belongs to the
node process above NodeSocket.
"""
from __future__ import annotations

import enum
import hashlib
import ipaddress
import json
import math
import os
import random
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class NodeRole(enum.IntEnum):
    SENSOR_NODE = 0
    CLUSTER_HEAD = 1


DEFAULT_PORT = 27062
RESERVED_NODE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


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
    """One virtual node in localhost mode.

    The thesis' localhost record contains only node_name, node_role, ip and port.
    There is deliberately no overlay/underlay translation in this implementation.
    """

    name: str
    role: NodeRole
    ip: str
    port: int = DEFAULT_PORT

    @property
    def address(self):
        return (self.ip, self.port)

    def validate(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.name):
            raise ValueError("node name must be 1-64 ASCII letters, digits, underscores or hyphens")
        if self.name.upper() in RESERVED_NODE_NAMES:
            raise ValueError("reserved node filename")
        if type(self.role) not in (int, NodeRole) or self.role not in (0, 1):
            raise ValueError("invalid node role")
        address = ipaddress.IPv4Address(self.ip)
        if str(address) != self.ip or not address.is_loopback:
            raise ValueError("localhost node IP must be a canonical 127/8 loopback address")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer between 1 and 65535")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "role": int(self.role), "ip": self.ip, "port": self.port}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodeSpec":
        obj = cls(d["name"], NodeRole(d["role"]), d["ip"], d.get("port", DEFAULT_PORT))
        obj.validate()
        return obj


@dataclass
class LinkConfig:
    pathloss_exponent: float = 3.0
    rssi0_dbm: float = -30.0
    d0_m: float = 1.0
    max_data_rate_bps: float = 1e9
    propagation_speed_mps: float = 299792458.0
    base_delay_s: float = 0.0
    e_elec_j_per_bit: float = 50e-9
    e_amp_j_per_bit_m2: float = 100e-9
    bucket_capacity_bytes: int = 65507
    ipdv_enabled: bool = True

    # ``rssi`` preserves Thesis Table 3-3 exactly. ``explicit`` makes the sender's
    # configured maximum range authoritative and scales the same strong/good/poor
    # link bands across that range. This allows heterogeneous radios without changing
    # NodeSocket or the protocol interface.
    range_mode: str = "rssi"
    max_range_m: Optional[float] = None
    role_max_range_m: Dict[str, float] = field(default_factory=dict)
    node_max_range_m: Dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        for name in (
            "pathloss_exponent", "rssi0_dbm", "d0_m", "max_data_rate_bps",
            "propagation_speed_mps", "base_delay_s", "e_elec_j_per_bit",
            "e_amp_j_per_bit_m2",
        ):
            finite(getattr(self, name), name)
        positive_int(self.bucket_capacity_bytes, "bucket_capacity_bytes")
        if self.bucket_capacity_bytes < 65507:
            raise ValueError("bucket capacity must accommodate the maximum IPv4 UDP payload (65507 bytes)")
        if type(self.ipdv_enabled) is not bool:
            raise ValueError("ipdv_enabled must be boolean")
        if self.pathloss_exponent <= 0 or self.d0_m <= 0 or self.max_data_rate_bps <= 0 or self.propagation_speed_mps <= 0:
            raise ValueError("pathloss, d0, maximum rate and propagation speed must be > 0")
        if self.base_delay_s < 0 or self.e_elec_j_per_bit < 0 or self.e_amp_j_per_bit_m2 < 0:
            raise ValueError("delay and energy coefficients must be >= 0")
        self.range_mode = str(self.range_mode).lower().replace("-", "_")
        if self.range_mode not in {"rssi", "explicit"}:
            raise ValueError("range_mode must be 'rssi' or 'explicit'")
        if self.max_range_m is not None:
            finite(self.max_range_m, "max_range_m", 0, strictly=True)
        if not isinstance(self.role_max_range_m, dict) or not isinstance(self.node_max_range_m, dict):
            raise ValueError("role_max_range_m and node_max_range_m must be objects")
        normalized_roles = {}
        aliases = {
            "clusterhead": "cluster_head", "cluster_head": "cluster_head", "ch": "cluster_head",
            "sensornode": "sensor_node", "sensor_node": "sensor_node", "sn": "sensor_node",
        }
        for key, value in self.role_max_range_m.items():
            normalized = aliases.get(str(key).lower().replace(" ", "_"))
            if normalized is None:
                raise ValueError(f"unknown role range key: {key}")
            finite(value, f"role_max_range_m[{key}]", 0, strictly=True)
            normalized_roles[normalized] = float(value)
        self.role_max_range_m = normalized_roles
        for key, value in list(self.node_max_range_m.items()):
            finite(value, f"node_max_range_m[{key}]", 0, strictly=True)
            self.node_max_range_m[str(key)] = float(value)

    def rssi_reachability_range_m(self) -> float:
        # Solve RSSI(d) = -85 dBm (the Table 3-3 unreachable boundary).
        return self.d0_m * 10.0 ** ((self.rssi0_dbm - (-85.0)) / (10.0 * self.pathloss_exponent))

    def tx_range_m(self, node_spec=None) -> float:
        """Return this sender's configured/effective maximum transmission range."""
        if self.range_mode == "rssi":
            return self.rssi_reachability_range_m()
        if node_spec is not None:
            if node_spec.name in self.node_max_range_m:
                return self.node_max_range_m[node_spec.name]
            role_key = "cluster_head" if int(node_spec.role) == int(NodeRole.CLUSTER_HEAD) else "sensor_node"
            if role_key in self.role_max_range_m:
                return self.role_max_range_m[role_key]
        if self.max_range_m is not None:
            return self.max_range_m
        return self.rssi_reachability_range_m()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        obj = cls(**dict(d))
        obj.validate()
        return obj


@dataclass
class MobilityConfig:
    # Mobility trace interval. The thesis evaluation specifies 100-500 ms, but CLIVE
    # accepts finer experimental traces down to 1 ms when explicitly requested.
    tick_interval: float = 0.1
    model: str = "gauss_markov"  # gauss_markov | random_waypoint | static
    initial_topology: str = "random"  # random | line | star | grid | manual
    topology_margin_m: float = 0.0
    star_radius_m: Optional[float] = None
    # Optional deterministic placement controls for static/controlled scenarios.
    # node_order controls line/grid ordering and leaf order for star layouts.
    # center_node overrides the star hub. manual_positions is authoritative when
    # initial_topology="manual".
    center_node: Optional[str] = None
    node_order: Optional[List[str]] = None
    manual_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    # Gauss-Markov parameters.
    alpha: float = 0.5
    area_width_m: float = 200.0
    area_height_m: float = 200.0
    ch_mean_speed_mps: float = 0.0
    sn_mean_speed_mps: float = 0.0
    speed_stddev: float = 1.0
    direction_stddev: float = 0.5

    # Pause control. Periodic pause schedules are local to each node. If a scenario
    # supplies pause_time_range without a trigger, Orchestrator selects periodic mode.
    pause_time_range: Optional[Tuple[float, float]] = None
    pause_trigger: str = "zero_speed"
    move_time_range: Optional[Tuple[float, float]] = None
    # For pause_trigger="random": probability that a moving node begins a pause
    # within one second. Internally converted to a continuous-time exponential
    # waiting process so behavior does not depend on mobility tick size.
    pause_probability_per_second: Optional[float] = None

    # Random-Waypoint speed ranges. When omitted they are derived around the role's
    # mean speed. RWP pauses occur independently on waypoint arrival.
    ch_speed_range_mps: Optional[Tuple[float, float]] = None
    sn_speed_range_mps: Optional[Tuple[float, float]] = None

    def validate(self) -> None:
        self.model = str(self.model).lower().replace("-", "_").replace(" ", "_")
        aliases = {"gm": "gauss_markov", "gaussmarkov": "gauss_markov", "rwp": "random_waypoint", "none": "static"}
        self.model = aliases.get(self.model, self.model)
        self.initial_topology = str(self.initial_topology).lower().replace("-", "_").replace(" ", "_")
        topology_aliases = {"random_scatter": "random", "radial": "star"}
        self.initial_topology = topology_aliases.get(self.initial_topology, self.initial_topology)
        if self.model not in {"gauss_markov", "random_waypoint", "static"}:
            raise ValueError("mobility model must be gauss_markov, random_waypoint, or static")
        if self.initial_topology not in {"random", "line", "star", "grid", "manual"}:
            raise ValueError("initial_topology must be random, line, star, grid, or manual")
        if self.center_node is not None:
            self.center_node = str(self.center_node)
        if self.node_order is not None:
            if not isinstance(self.node_order, (list, tuple)) or not self.node_order:
                raise ValueError("node_order must be a non-empty array when provided")
            self.node_order = [str(name) for name in self.node_order]
            if len(self.node_order) != len(set(self.node_order)):
                raise ValueError("node_order must not contain duplicate node names")
        if not isinstance(self.manual_positions, dict):
            raise ValueError("manual_positions must be an object")
        normalized_positions = {}
        for name, position in self.manual_positions.items():
            if not isinstance(position, (list, tuple)) or len(position) != 2:
                raise ValueError(f"manual_positions[{name}] must contain [x, y]")
            x, y = position
            finite(x, f"manual_positions[{name}].x")
            finite(y, f"manual_positions[{name}].y")
            if not (0 <= x <= self.area_width_m and 0 <= y <= self.area_height_m):
                raise ValueError(f"manual position for {name} is outside the simulation area")
            normalized_positions[str(name)] = (float(x), float(y))
        self.manual_positions = normalized_positions
        for name in (
            "tick_interval", "alpha", "area_width_m", "area_height_m", "topology_margin_m",
            "ch_mean_speed_mps", "sn_mean_speed_mps", "speed_stddev", "direction_stddev",
        ):
            finite(getattr(self, name), name)
        if not 0.001 <= self.tick_interval <= 1.0:
            raise ValueError("tick_interval must be between 0.001 and 1.0 seconds")
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be between 0 and 1")
        if self.area_width_m <= 0 or self.area_height_m <= 0:
            raise ValueError("simulation area dimensions must be > 0")
        if self.topology_margin_m < 0 or self.topology_margin_m * 2 >= min(self.area_width_m, self.area_height_m):
            raise ValueError("topology_margin_m must leave usable simulation area")
        if self.star_radius_m is not None:
            finite(self.star_radius_m, "star_radius_m", 0, strictly=True)
            if self.star_radius_m > min(self.area_width_m, self.area_height_m) / 2:
                raise ValueError("star_radius_m must fit inside the simulation area")
        if self.ch_mean_speed_mps < 0 or self.sn_mean_speed_mps < 0:
            raise ValueError("mean speeds must be >= 0")
        if self.speed_stddev < 0 or self.direction_stddev < 0:
            raise ValueError("mobility standard deviations must be >= 0")
        self.pause_trigger = str(self.pause_trigger).lower().replace("-", "_").replace(" ", "_")
        pause_aliases = {"probabilistic": "random", "random_pause": "random", "random_interval": "periodic"}
        self.pause_trigger = pause_aliases.get(self.pause_trigger, self.pause_trigger)
        if self.pause_trigger not in {"zero_speed", "periodic", "random"}:
            raise ValueError("pause_trigger must be zero_speed, periodic, or random")
        if self.pause_trigger == "periodic" and self.pause_time_range is not None and self.move_time_range is None:
            raise ValueError("periodic pauses require move_time_range")
        if self.pause_trigger == "random":
            if self.pause_time_range is None:
                raise ValueError("random pauses require pause_time_range")
            if self.pause_probability_per_second is None:
                raise ValueError("random pauses require pause_probability_per_second")
            finite(self.pause_probability_per_second, "pause_probability_per_second")
            if not 0.0 < self.pause_probability_per_second < 1.0:
                raise ValueError("pause_probability_per_second must be > 0 and < 1")
        elif self.pause_probability_per_second is not None:
            finite(self.pause_probability_per_second, "pause_probability_per_second")
            if not 0.0 <= self.pause_probability_per_second < 1.0:
                raise ValueError("pause_probability_per_second must be >= 0 and < 1")
        for field_name, values, strictly_positive in (
            ("pause_time_range", self.pause_time_range, False),
            ("move_time_range", self.move_time_range, True),
            ("ch_speed_range_mps", self.ch_speed_range_mps, False),
            ("sn_speed_range_mps", self.sn_speed_range_mps, False),
        ):
            if values is None:
                continue
            if len(values) != 2:
                raise ValueError(f"{field_name} requires two values")
            lo, hi = values
            finite(lo, f"{field_name} minimum")
            finite(hi, f"{field_name} maximum")
            if (lo <= 0 if strictly_positive else lo < 0) or hi < lo:
                raise ValueError(f"invalid {field_name}")

    def to_dict(self):
        value = asdict(self)
        for key in ("pause_time_range", "move_time_range", "ch_speed_range_mps", "sn_speed_range_mps"):
            if value[key] is not None:
                value[key] = list(value[key])
        value["manual_positions"] = {name: list(pos) for name, pos in self.manual_positions.items()}
        return value

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        for key in ("pause_time_range", "move_time_range", "ch_speed_range_mps", "sn_speed_range_mps"):
            value = d.get(key)
            if value is not None:
                d[key] = tuple(value)
        obj = cls(**d)
        obj.validate()
        return obj


@dataclass
class EmulationConfig:
    duration_s: float = 300.0
    seed: int = 0
    startup_delay_s: float = 2.0
    io_poll_interval_s: float = 0.002
    receive_batch_size: int = 64
    receive_budget_s: float = 0.002
    max_queue_packets: int = 4096
    max_queue_bytes: int = 8 * 1024 * 1024
    worker_grace_s: float = 5.0
    readiness_timeout_s: float = 30.0
    start_tolerance_s: float = 0.10

    def validate(self) -> None:
        for name in (
            "duration_s", "startup_delay_s", "io_poll_interval_s", "receive_budget_s",
            "worker_grace_s", "readiness_timeout_s", "start_tolerance_s",
        ):
            finite(getattr(self, name), name, 0, strictly=True)
        for name in ("receive_batch_size", "max_queue_packets", "max_queue_bytes"):
            positive_int(getattr(self, name), name)
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        # Backward compatibility with plans/scenarios created before packet-log tails
        # were removed from normal metrics output.
        d.pop("log_tail_records", None)
        obj = cls(**d)
        obj.validate()
        return obj


@dataclass
class LoggingConfig:
    """Optional diagnostic output. Normal evaluation writes no JSONL logs."""

    protocol_events: bool = False
    packet_events: bool = False
    mobility_tsv: bool = False

    def validate(self) -> None:
        for name in ("protocol_events", "packet_events", "mobility_tsv"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"logging.{name} must be true or false")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        obj = cls(**dict(d or {}))
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
    logging_config: LoggingConfig = field(default_factory=LoggingConfig)

    def trace_digest(self):
        cached = getattr(self, "_trace_digest_cache", None)
        if cached is not None:
            return cached
        raw = json.dumps(self.trace, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self._trace_digest_cache = digest
        return digest

    def to_dict(self):
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "trace": {str(k): v for k, v in self.trace.items()},
            "link_config": self.link_config.to_dict(),
            "mobility_config": self.mobility_config.to_dict(),
            "config": self.config.to_dict(),
            "logging_config": self.logging_config.to_dict(),
            "epoch_start": self.epoch_start,
            "trace_sha256": self.trace_digest(),
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
            LoggingConfig.from_dict(d.get("logging_config", {})),
        )
        if "trace_sha256" in d and d["trace_sha256"] != plan.trace_digest():
            raise ValueError("mobility trace digest mismatch")
        plan.validate()
        return plan

    def validate(self) -> None:
        self.link_config.validate()
        self.mobility_config.validate()
        self.config.validate()
        self.logging_config.validate()
        finite(self.epoch_start, "epoch_start", 0, strictly=True)
        if not self.nodes:
            raise ValueError("an emulation plan must contain at least one node")
        if not self.trace:
            raise ValueError("an emulation plan must contain a mobility trace")

        for node in self.nodes:
            node.validate()

        names = [n.name for n in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("node names must be unique")
        unknown_range_nodes = set(self.link_config.node_max_range_m) - set(names)
        if unknown_range_nodes:
            raise ValueError(f"node_max_range_m contains unknown nodes: {sorted(unknown_range_nodes)}")
        addresses = [n.address for n in self.nodes]
        if len(addresses) != len(set(addresses)):
            raise ValueError("node addresses must be unique")

        expected_ticks = int(self.config.duration_s / self.mobility_config.tick_interval) + 1
        if len(self.trace) < expected_ticks:
            raise ValueError(
                f"mobility trace is incomplete: expected at least {expected_ticks} ticks, got {len(self.trace)}"
            )
        expected_names = set(names)
        for tick in range(expected_ticks):
            positions = self.trace.get(tick)
            if positions is None:
                raise ValueError(f"mobility trace is missing tick {tick}")
            if set(positions) != expected_names:
                raise ValueError(f"mobility trace tick {tick} has inconsistent nodes")
            for name, position in positions.items():
                if not isinstance(position, (list, tuple)) or len(position) != 2:
                    raise ValueError(f"invalid coordinates for {name} at tick {tick}")
                for value in position:
                    finite(value, f"coordinates for {name}")
                if not (
                    0 <= position[0] <= self.mobility_config.area_width_m
                    and 0 <= position[1] <= self.mobility_config.area_height_m
                ):
                    raise ValueError(f"coordinates outside simulation area for {name}")

    def save(self, path: str):
        self.validate()
        atomic_json(path, self.to_dict())

    def save_trace(self, path):
        """Row-per-mobility-quantum TSV: elapsed time followed by each node x/y pair."""
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
        with open(path, encoding="utf-8") as stream:
            return cls.from_dict(
                json.load(
                    stream,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON value {value}")
                    ),
                )
            )

    def node_by_name(self, name: str) -> NodeSpec:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)

    def time_for_tick(self, tick: int) -> float:
        return tick * self.mobility_config.tick_interval


def _stable_node_seed(seed: int, name: str, namespace: str = "mobility") -> int:
    raw = f"{namespace}:{seed}:{name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def generate_initial_positions(node_names, cfg, seed):
    """Generate deterministic initial positions independent of the mobility model.

    Controlled layouts can optionally specify ``node_order``. Star layouts can select
    ``center_node`` explicitly. ``manual`` layouts require an exact position for every
    generated node.
    """
    cfg.validate()
    names = list(node_names)
    if not names:
        return {}
    w, h = cfg.area_width_m, cfg.area_height_m
    m = cfg.topology_margin_m
    usable_w, usable_h = w - 2 * m, h - 2 * m

    ordered = list(names)
    if cfg.node_order is not None:
        if len(cfg.node_order) != len(names) or set(cfg.node_order) != set(names):
            raise ValueError("node_order must contain every generated node exactly once")
        ordered = list(cfg.node_order)

    if cfg.initial_topology == "manual":
        if set(cfg.manual_positions) != set(names):
            missing = sorted(set(names) - set(cfg.manual_positions))
            extra = sorted(set(cfg.manual_positions) - set(names))
            raise ValueError(f"manual_positions must match generated nodes exactly; missing={missing}, extra={extra}")
        return {name: list(cfg.manual_positions[name]) for name in names}

    result = {}
    if cfg.initial_topology == "random":
        rng = random.Random(_stable_node_seed(seed, "initial-topology", "topology"))
        for name in ordered:
            result[name] = [rng.uniform(m, w - m), rng.uniform(m, h - m)]
        return result

    if cfg.initial_topology == "line":
        if len(ordered) == 1:
            return {ordered[0]: [w / 2.0, h / 2.0]}
        for i, name in enumerate(ordered):
            x = m + usable_w * i / (len(ordered) - 1)
            result[name] = [x, h / 2.0]
        return result

    if cfg.initial_topology == "grid":
        cols = max(1, math.ceil(math.sqrt(len(ordered) * (usable_w / max(usable_h, 1e-9)))))
        rows = math.ceil(len(ordered) / cols)
        for i, name in enumerate(ordered):
            col, row = i % cols, i // cols
            x = w / 2.0 if cols == 1 else m + usable_w * col / (cols - 1)
            y = h / 2.0 if rows == 1 else m + usable_h * row / (rows - 1)
            result[name] = [x, y]
        return result

    # Star/radial placement. center_node is the hub when supplied, otherwise
    # the first ordered node is the hub. Large scenarios use concentric rings.
    if cfg.center_node is not None:
        if cfg.center_node not in names:
            raise ValueError(f"center_node does not exist: {cfg.center_node}")
        ordered = [cfg.center_node] + [name for name in ordered if name != cfg.center_node]
    cx, cy = w / 2.0, h / 2.0
    result[ordered[0]] = [cx, cy]
    leaves = ordered[1:]
    if not leaves:
        return result
    max_radius = min(w, h) / 2.0 - m
    outer = min(cfg.star_radius_m or max_radius * 0.72, max_radius)
    ring_count = max(1, math.ceil(len(leaves) / 24))
    ring_sizes = []
    remaining = len(leaves)
    for ring in range(1, ring_count + 1):
        capacity = min(remaining, max(6, 12 * ring))
        ring_sizes.append(capacity)
        remaining -= capacity
        if remaining <= 0:
            break
    cursor = 0
    for ring_index, count in enumerate(ring_sizes, start=1):
        radius = outer * ring_index / len(ring_sizes)
        phase = (ring_index % 2) * math.pi / max(count, 1)
        for j in range(count):
            if cursor >= len(leaves):
                break
            angle = phase + 2.0 * math.pi * j / count
            result[leaves[cursor]] = [cx + radius * math.cos(angle), cy + radius * math.sin(angle)]
            cursor += 1
    return result


def _sample_random_pause_wait_s(cfg, rng):
    """Sample active-movement time until the next probabilistic pause.

    ``pause_probability_per_second`` is interpreted as the probability of at least
    one pause trigger during one second of movement. Converting it to an
    exponential hazard keeps the process independent of ``tick_interval``.
    """
    if cfg.pause_trigger != "random":
        return None
    p = cfg.pause_probability_per_second
    if p is None:
        return None
    rate = -math.log1p(-p)
    return rng.expovariate(rate)


class GaussMarkovNode:
    """Precomputed Gauss-Markov mobility state for one independently scheduled node."""

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
        self.move_remaining_s = (
            rng.uniform(*cfg.move_time_range)
            if cfg.pause_trigger == "periodic" and cfg.pause_time_range is not None and cfg.move_time_range is not None
            else None
        )
        self.random_pause_wait_s = _sample_random_pause_wait_s(cfg, rng)

    def step(self, dt):
        if self.mean_speed <= 0:
            self.speed = 0.0
            return

        remaining = float(dt)
        loops = 0
        while remaining > 1e-12 and loops < 8:
            loops += 1
            if self.pause_remaining_s > 0:
                used = min(remaining, self.pause_remaining_s)
                self.pause_remaining_s -= used
                remaining -= used
                if remaining <= 1e-12:
                    return
                continue

            move_dt = remaining
            if self.move_remaining_s is not None:
                move_dt = min(move_dt, self.move_remaining_s)
            if self.random_pause_wait_s is not None:
                move_dt = min(move_dt, self.random_pause_wait_s)

            # A random pause may be scheduled exactly at this boundary. Start it
            # before attempting a zero-duration mobility update.
            if move_dt <= 1e-12 and self.random_pause_wait_s is not None:
                self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                self.random_pause_wait_s = _sample_random_pause_wait_s(self.cfg, self.rng)
                continue

            # Scale temporal correlation when only part of a configured mobility quantum
            # is spent moving because a pause begins/ends inside the quantum.
            fraction = max(1e-12, move_dt / self.cfg.tick_interval)
            a = self.cfg.alpha ** fraction
            noise_scale = math.sqrt(max(0.0, 1 - a * a))
            speed_noise = self.rng.gauss(0.0, self.cfg.speed_stddev * self.mean_speed)
            direction_noise = self.rng.gauss(0.0, self.cfg.direction_stddev)
            self.speed = a * self.speed + (1 - a) * self.mean_speed + noise_scale * speed_noise
            self.direction = a * self.direction + (1 - a) * self.mean_direction + noise_scale * direction_noise
            self.speed = max(0.0, self.speed)

            if self.speed == 0.0 and self.cfg.pause_time_range is not None and self.cfg.pause_trigger == "zero_speed":
                self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                continue

            self.x += self.speed * math.cos(self.direction) * move_dt
            self.y += self.speed * math.sin(self.direction) * move_dt
            self._bounce()
            remaining -= move_dt

            if self.move_remaining_s is not None:
                self.move_remaining_s -= move_dt
                if self.move_remaining_s <= 1e-12:
                    self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                    self.move_remaining_s = self.rng.uniform(*self.cfg.move_time_range)

            if self.random_pause_wait_s is not None:
                self.random_pause_wait_s -= move_dt
                if self.random_pause_wait_s <= 1e-12:
                    self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                    self.random_pause_wait_s = _sample_random_pause_wait_s(self.cfg, self.rng)

    def _bounce(self):
        w, h = self.cfg.area_width_m, self.cfg.area_height_m

        def reflect(position, bound):
            finite(position, "computed mobility position")
            if 0 <= position <= bound:
                return position, False
            period = 2.0 * bound
            wrapped = position % period
            if wrapped <= bound:
                return wrapped, False
            return period - wrapped, True

        self.x, flip_x = reflect(self.x, w)
        self.y, flip_y = reflect(self.y, h)
        if flip_x:
            self.direction = math.pi - self.direction
            self.mean_direction = math.pi - self.mean_direction
        if flip_y:
            self.direction = -self.direction
            self.mean_direction = -self.mean_direction
        self.direction %= 2.0 * math.pi
        self.mean_direction %= 2.0 * math.pi


class RandomWaypointNode:
    """Independent Random Waypoint mover with per-node destinations, speeds and pauses."""

    def __init__(self, speed_range, x, y, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.speed_range = speed_range
        self.x, self.y = x, y
        self.pause_remaining_s = 0.0
        self.random_pause_wait_s = _sample_random_pause_wait_s(cfg, rng)
        self.target_x = x
        self.target_y = y
        self.speed = 0.0
        self._choose_target()

    def _choose_target(self):
        m = self.cfg.topology_margin_m
        self.target_x = self.rng.uniform(m, self.cfg.area_width_m - m)
        self.target_y = self.rng.uniform(m, self.cfg.area_height_m - m)
        self.speed = self.rng.uniform(*self.speed_range)

    def step(self, dt):
        remaining = float(dt)
        loops = 0
        while remaining > 1e-12 and loops < 32:
            loops += 1
            if self.pause_remaining_s > 0:
                used = min(remaining, self.pause_remaining_s)
                self.pause_remaining_s -= used
                remaining -= used
                if self.pause_remaining_s <= 1e-12 and self.cfg.pause_trigger != "random":
                    self._choose_target()
                continue

            dx, dy = self.target_x - self.x, self.target_y - self.y
            distance = math.hypot(dx, dy)
            if distance <= 1e-9 or self.speed <= 0:
                # In probabilistic-pause mode, waypoint arrival is not itself a
                # pause trigger; choose the next waypoint and continue.
                if self.cfg.pause_trigger == "random":
                    self._choose_target()
                    continue
                if self.cfg.pause_time_range is not None:
                    self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                    if self.pause_remaining_s > 0:
                        continue
                self._choose_target()
                continue

            time_to_target = distance / self.speed
            move_dt = min(remaining, time_to_target)
            if self.random_pause_wait_s is not None:
                move_dt = min(move_dt, self.random_pause_wait_s)

            if move_dt <= 1e-12 and self.random_pause_wait_s is not None:
                self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                self.random_pause_wait_s = _sample_random_pause_wait_s(self.cfg, self.rng)
                continue

            ratio = min(1.0, (self.speed * move_dt) / distance)
            self.x += dx * ratio
            self.y += dy * ratio
            remaining -= move_dt

            if self.random_pause_wait_s is not None:
                self.random_pause_wait_s -= move_dt
                if self.random_pause_wait_s <= 1e-12:
                    self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                    self.random_pause_wait_s = _sample_random_pause_wait_s(self.cfg, self.rng)
                    continue

            if move_dt >= time_to_target - 1e-12:
                self.x, self.y = self.target_x, self.target_y
                if self.cfg.pause_trigger == "random":
                    self._choose_target()
                elif self.cfg.pause_time_range is not None:
                    self.pause_remaining_s = self.rng.uniform(*self.cfg.pause_time_range)
                else:
                    self._choose_target()



def _derived_speed_range(mean_speed):
    if mean_speed <= 0:
        return (0.0, 0.0)
    return (max(0.05, mean_speed * 0.5), max(0.05, mean_speed * 1.5))


def precompute_trace(node_names, roles, cfg, duration_s, seed):
    cfg.validate()
    finite(duration_s, "duration_s", 0, strictly=True)
    names = list(node_names)
    initial = generate_initial_positions(names, cfg, seed)
    dt = cfg.tick_interval
    ticks = int(duration_s / dt) + 1

    if cfg.model == "static":
        return {
            tick: {name: [initial[name][0], initial[name][1]] for name in names}
            for tick in range(ticks)
        }

    states = {}
    for name in names:
        rng = random.Random(_stable_node_seed(seed, name))
        role = roles[name]
        x, y = initial[name]
        if cfg.model == "gauss_markov":
            mean_speed = cfg.ch_mean_speed_mps if role == NodeRole.CLUSTER_HEAD else cfg.sn_mean_speed_mps
            states[name] = GaussMarkovNode(mean_speed, x, y, cfg, rng)
        elif cfg.model == "random_waypoint":
            speed_range = (
                cfg.ch_speed_range_mps if role == NodeRole.CLUSTER_HEAD else cfg.sn_speed_range_mps
            )
            if speed_range is None:
                mean_speed = cfg.ch_mean_speed_mps if role == NodeRole.CLUSTER_HEAD else cfg.sn_mean_speed_mps
                speed_range = _derived_speed_range(mean_speed)
            states[name] = RandomWaypointNode(speed_range, x, y, cfg, rng)
        else:
            raise ValueError(f"unsupported mobility model: {cfg.model}")

    trace: Dict[int, Dict[str, List[float]]] = {}
    for tick in range(ticks):
        trace[tick] = {name: [state.x, state.y] for name, state in states.items()}
        for state in states.values():
            state.step(dt)
    return trace


def build_nodes(num_ch: int, num_sn: int):
    if type(num_ch) is not int or type(num_sn) is not int or num_ch < 0 or num_sn < 0 or num_ch + num_sn <= 0:
        raise ValueError("num_ch and num_sn must be non-negative and total at least 1")
    # 127.0.0.1 is reserved for the physical localhost/controller identity. Nodes start at .2.
    # The 127/8 loopback block provides ample addresses for stress tests such as 200 nodes.
    if num_ch + num_sn > (2 ** 24 - 2):
        raise ValueError("too many localhost nodes for 127/8 loopback addressing")

    names = (
        [(f"CH-{i + 1}", NodeRole.CLUSTER_HEAD) for i in range(num_ch)]
        + [(f"SN-{i + 1}", NodeRole.SENSOR_NODE) for i in range(num_sn)]
    )
    base = int(ipaddress.IPv4Address("127.0.0.0"))
    nodes = []
    for index, (name, role) in enumerate(names, start=2):
        nodes.append(NodeSpec(name, role, str(ipaddress.IPv4Address(base + index)), DEFAULT_PORT))
    for node in nodes:
        node.validate()
    return nodes


class Orchestrator:
    def prepare(self, scenario=None) -> EmulationPlan:
        scenario = dict(scenario or {})
        if "mode" in scenario and str(scenario["mode"]).lower() != "localhost":
            raise ValueError("CLIVE is localhost-only; multi-host scenarios are not supported")
        scenario.pop("mode", None)  # legacy localhost configs may still contain this value
        removed_keys = {"hosts", "num_hosts", "underlay_ip_by_node", "node_to_host"}.intersection(scenario)
        if removed_keys:
            raise ValueError(f"localhost-only CLIVE does not accept deployment fields: {sorted(removed_keys)}")

        runtime = dict(scenario.get("runtime", {}))
        runtime.setdefault("duration_s", scenario.get("duration", 300.0))
        runtime.setdefault("seed", scenario.get("seed", 0))
        runtime.setdefault("startup_delay_s", scenario.get("startup_delay_s", 2.0))
        runtime.setdefault("io_poll_interval_s", scenario.get("io_poll_interval_s", 0.002))
        # Old configs may still contain this field. Packet-log tails no longer exist.
        runtime.pop("log_tail_records", None)
        config = EmulationConfig(**runtime)
        logging_config = LoggingConfig.from_dict(scenario.get("logging", {}))

        link_data = dict(scenario.get("link", {}))
        if "range_mode" not in link_data and any(
            key in link_data for key in ("max_range_m", "role_max_range_m", "node_max_range_m")
        ):
            link_data["range_mode"] = "explicit"
        link = LinkConfig(**link_data)

        mobility_data = dict(scenario.get("mobility", {}))
        if mobility_data.get("pause_time_range") is not None and "pause_trigger" not in mobility_data:
            # A configured pause range means the caller expects pauses. Give each node an
            # independent periodic move/pause schedule unless the scenario explicitly chooses
            # zero_speed behavior. The movement interval remains configurable.
            mobility_data["pause_trigger"] = "periodic"
            mobility_data.setdefault("move_time_range", [10.0, 30.0])
        mobility = MobilityConfig.from_dict(mobility_data)
        for item in (config, link):
            item.validate()

        num_ch, num_sn = scenario.get("num_ch", 2), scenario.get("num_sn", 6)
        nodes = build_nodes(num_ch, num_sn)
        roles = {node.name: node.role for node in nodes}
        trace = precompute_trace(list(roles), roles, mobility, config.duration_s, config.seed)
        plan = EmulationPlan(
            nodes=nodes,
            trace=trace,
            link_config=link,
            mobility_config=mobility,
            config=config,
            epoch_start=time.time() + config.startup_delay_s,
            logging_config=logging_config,
        )
        plan.validate()
        return plan
