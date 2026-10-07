from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeConfig:
    """Configuration values required by a FLAME node before startup."""

    # Node
    role: int
    version: int = 1

    # Communication
    host: str = "127.0.0.1"
    port: int = 0
    poll_interval: float = 0.01

    # Buffer
    buffer_size: int = 128

    # Reliability / timers
    initial_window_size: int = 2
    max_window_size: int = 8
    ack_timeout: float = 0.25
    ack_delay: float = 0.04
    reassembly_timeout: float = 5.0
    completed_retention: float = 2.0
    max_retries: int = 3
    # Federated Learning
    training_threshold: int = 10
    aggregation_threshold: int = 1