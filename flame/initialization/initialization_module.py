from __future__ import annotations

from ..communication.reliability import ReliabilityManager

from .config import NodeConfig


class InitializationModule:
    """
    Initializes and validates the configuration required by a FLAME node
    before protocol operation begins.
    """

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self._initialized = False

    def initialize(self) -> NodeConfig:
        """Validate the configuration and mark the node as initialized."""
        self._validate_config()
        self._initialized = True
        return self.config

    @property
    def initialized(self) -> bool:
        """Return whether initialization has completed."""
        return self._initialized

    def _validate_config(self) -> None:
        """Validate configuration values before protocol startup."""

        if self.config.role not in (0, 1):
            raise ValueError("Role must be 0 (Sensor Node) or 1 (Cluster Head).")

        if not 0 <= self.config.version <= 0x0F:
            raise ValueError("Version must fit within 4 bits.")

        if not isinstance(self.config.host, str) or not self.config.host:
            raise ValueError("Host must be a non-empty string.")

        if not 0 <= self.config.port <= 0xFFFF:
            raise ValueError("Port must fit within 16 bits.")

        if self.config.poll_interval <= 0:
            raise ValueError("Poll interval must be greater than zero.")

        if self.config.buffer_size <= 0:
            raise ValueError("Buffer size must be greater than zero.")

        # Use the runtime's validation so initialization cannot accept a
        # window/timer configuration that CommunicationModule later rejects.
        ReliabilityManager(
            initial_window_size=self.config.initial_window_size,
            max_window_size=self.config.max_window_size,
            ack_timeout=self.config.ack_timeout,
            ack_delay=self.config.ack_delay,
            reassembly_timeout=self.config.reassembly_timeout,
            completed_retention=self.config.completed_retention,
            max_retries=self.config.max_retries,
        )

        if type(self.config.expected_observations) is not int or (
            self.config.expected_observations <= 0
        ):
            raise ValueError("Expected observations must be greater than zero.")

        if not isinstance(self.config.training_threshold, (int, float)) or not (
            0 < self.config.training_threshold <= 1
        ):
            raise ValueError("Training threshold must be between zero and one.")

        if type(self.config.aggregation_threshold) is not int or (
            self.config.aggregation_threshold <= 0
        ):
            raise ValueError("Aggregation threshold must be greater than zero.")
