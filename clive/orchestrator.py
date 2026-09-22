"""
CLIVE Orchestrator.

Prepares an emulation plan containing:
- node configuration and addresses
- precomputed mobility
- link configuration
- experiment configuration
- emulation start time

The Orchestrator does not run nodes or implement protocol behavior.
"""

from __future__ import annotations

import enum
import json
import math
import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


class NodeRole(enum.IntEnum):
    SENSOR_NODE = 0
    CLUSTER_HEAD = 1


DEFAULT_PORT = 27062


@dataclass
class NodeSpec:
    name: str
    role: NodeRole
    overlay_ip: str
    overlay_port: int
    underlay_ip: str
    underlay_port: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": int(self.role),
            "overlay_ip": self.overlay_ip,
            "overlay_port": self.overlay_port,
            "underlay_ip": self.underlay_ip,
            "underlay_port": self.underlay_port,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodeSpec":
        return cls(
            d["name"],
            NodeRole(int(d["role"])),
            d["overlay_ip"],
            int(d["overlay_port"]),
            d["underlay_ip"],
            int(d["underlay_port"]),
        )


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

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class MobilityConfig:
    tick_interval: float = 0.1
    alpha: float = 0.5
    area_width_m: float = 200.0
    area_height_m: float = 200.0
    ch_mean_speed_mps: float = 0.0
    sn_mean_speed_mps: float = 0.0
    speed_stddev: float = 1.0
    direction_stddev: float = 0.5
    pause_time_range: Optional[Tuple[float, float]] = None

    def to_dict(self):
        d = asdict(self)
        d["pause_time_range"] = (
            list(self.pause_time_range)
            if self.pause_time_range is not None
            else None
        )
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        pause = d.pop("pause_time_range", None)
        return cls(
            pause_time_range=tuple(pause) if pause else None,
            **d,
        )


@dataclass
class EmulationConfig:
    duration_s: float = 300.0
    mode: str = "localhost"
    seed: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


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
        return cls(
            [NodeSpec.from_dict(n) for n in d["nodes"]],
            {int(k): v for k, v in d["trace"].items()},
            LinkConfig.from_dict(d["link_config"]),
            MobilityConfig.from_dict(d["mobility_config"]),
            EmulationConfig.from_dict(d["config"]),
            float(d["epoch_start"]),
        )

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str):
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def node_by_name(self, name: str) -> NodeSpec:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)


class GaussMarkovNode:

    def __init__(self, mean_speed, x, y, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.mean_speed = mean_speed

        self.x = x
        self.y = y

        self.speed = mean_speed
        self.mean_direction = rng.uniform(0, 2 * math.pi)
        self.direction = self.mean_direction

    def step(self, dt):
        a = self.cfg.alpha

        speed_noise = self.rng.gauss(
            0.0,
            self.cfg.speed_stddev * self.mean_speed,
        )

        direction_noise = self.rng.gauss(
            0.0,
            self.cfg.direction_stddev,
        )

        self.speed = (
            a * self.speed
            + (1 - a) * self.mean_speed
            + math.sqrt(1 - a * a) * speed_noise
        )

        self.direction = (
            a * self.direction
            + (1 - a) * self.mean_direction
            + math.sqrt(1 - a * a) * direction_noise
        )

        self.speed = max(0.0, self.speed)

        self.x += self.speed * math.cos(self.direction) * dt
        self.y += self.speed * math.sin(self.direction) * dt

        self._bounce()

    def _bounce(self):
        w = self.cfg.area_width_m
        h = self.cfg.area_height_m

        if self.x < 0:
            self.x = -self.x
            self.direction = math.pi - self.direction
        elif self.x > w:
            self.x = 2 * w - self.x
            self.direction = math.pi - self.direction

        if self.y < 0:
            self.y = -self.y
            self.direction = -self.direction
        elif self.y > h:
            self.y = 2 * h - self.y
            self.direction = -self.direction

        self.x = min(max(self.x, 0.0), w)
        self.y = min(max(self.y, 0.0), h)


def precompute_trace(node_names, roles, cfg, duration_s, seed):
    rng = random.Random(seed)

    states = {}

    for name in node_names:
        mean_speed = (
            cfg.ch_mean_speed_mps
            if roles[name] == NodeRole.CLUSTER_HEAD
            else cfg.sn_mean_speed_mps
        )

        states[name] = GaussMarkovNode(
            mean_speed,
            rng.uniform(0, cfg.area_width_m),
            rng.uniform(0, cfg.area_height_m),
            cfg,
            rng,
        )

    ticks = int(duration_s / cfg.tick_interval) + 1
    trace = {}

    for tick in range(ticks):
        trace[tick] = {
            name: [
                round(state.x, 4),
                round(state.y, 4),
            ]
            for name, state in states.items()
        }

        for state in states.values():
            state.step(cfg.tick_interval)

    return trace


def build_nodes(num_ch, num_sn, mode="localhost"):
    names = (
        [(f"CH-{i + 1}", NodeRole.CLUSTER_HEAD) for i in range(num_ch)]
        + [(f"SN-{i + 1}", NodeRole.SENSOR_NODE) for i in range(num_sn)]
    )

    nodes = []

    for index, (name, role) in enumerate(names, start=2):

        if mode == "localhost":
            overlay_ip = f"127.0.0.{index}"
            underlay_ip = overlay_ip
            overlay_port = DEFAULT_PORT
            underlay_port = DEFAULT_PORT

        else:
            overlay_ip = f"10.0.0.{index - 1}"
            overlay_port = DEFAULT_PORT
            underlay_ip = "192.168.69.1"
            underlay_port = 16000 + index - 2

        nodes.append(
            NodeSpec(
                name,
                role,
                overlay_ip,
                overlay_port,
                underlay_ip,
                underlay_port,
            )
        )

    return nodes


class Orchestrator:

    def prepare(self, scenario=None) -> EmulationPlan:
        scenario = scenario or {}

        config = EmulationConfig(
            duration_s=scenario.get("duration", 300.0),
            mode=scenario.get("mode", "localhost"),
            seed=scenario.get("seed", 0),
        )

        link = LinkConfig(**scenario.get("link", {}))
        mobility = MobilityConfig(**scenario.get("mobility", {}))

        nodes = build_nodes(
            scenario.get("num_ch", 2),
            scenario.get("num_sn", 6),
            config.mode,
        )

        roles = {
            node.name: node.role
            for node in nodes
        }

        trace = precompute_trace(
            [node.name for node in nodes],
            roles,
            mobility,
            config.duration_s,
            config.seed,
        )

        epoch_start = time.time() + 2.0

        return EmulationPlan(
            nodes=nodes,
            trace=trace,
            link_config=link,
            mobility_config=mobility,
            config=config,
            epoch_start=epoch_start,
        )