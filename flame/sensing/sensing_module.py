
from collections import deque
from threading import Lock
from typing import Callable

try:
    from ..communication.message import PacketType
except ImportError:
    from communication.message import PacketType

from .data_acquisition import DataAcquisition


class SensingModule:
    """Collect application data and prepare SENSOR_DATA messages."""

    def __init__(
        self,
        data_acquisition: DataAcquisition,
        communication_module,
        destination: tuple[str, int],
        *,
        sensor_type: int = 1,
        sample_buffer_capacity: int = 100,
        send_buffer_available: Callable[[], bool] | None = None,
    ) -> None:
        if sample_buffer_capacity < 1:
            raise ValueError(
                "sample_buffer_capacity must be at least 1."
            )

        if not 0 <= sensor_type <= 0xFF:
            raise ValueError("sensor_type must fit within 8 bits.")

        self.data_acquisition = data_acquisition
        self.communication_module = communication_module
        self.destination = destination
        self.sensor_type = sensor_type
        self.sample_buffer_capacity = sample_buffer_capacity

        # Connect this callback to the Communication Module's
        # Send Buffer status.
        self.send_buffer_available = (
            send_buffer_available or (lambda: True)
        )

        self._sample_buffer = deque()
        self._lock = Lock()
        self._next_data_id = 1
        self.dropped_samples = 0

    def _create_message(self, payload: bytes) -> dict:
        """Create an application-level SENSOR_DATA message."""

        with self._lock:
            data_id = self._next_data_id
            self._next_data_id = (self._next_data_id + 1) & 0xFF

        return {
            "packet_type": PacketType.SENSOR_DATA,
            "role": 0,
            "data_id": data_id,
            "sensor_type": self.sensor_type,
            "payload": payload,
        }

    def collect_once(self) -> dict:
        """Collect one sample and submit or buffer its message."""

        payload = self.data_acquisition.collect()
        message = self._create_message(payload)

        try:
            accepted = self.communication_module.try_enqueue_message(
                message,
                destination=self.destination,
            )
        except Exception:
            # Preserve the sample if submission fails.
            self._buffer_sample(message)
            raise

        if not accepted:
            self._buffer_sample(message)

        return message

    def _buffer_sample(self, message: dict) -> None:
        """Queue a sample, dropping the oldest if the buffer is full."""

        with self._lock:
            if len(self._sample_buffer) >= self.sample_buffer_capacity:
                self._sample_buffer.popleft()
                self.dropped_samples += 1

            self._sample_buffer.append(message)

    def flush_sample_buffer(self) -> int:
        """Submit buffered samples while the Send Buffer has capacity."""

        submitted = 0

        while True:
            with self._lock:
                if not self._sample_buffer:
                    break

                message = self._sample_buffer[0]

            try:
                accepted = self.communication_module.try_enqueue_message(
                    message,
                    destination=self.destination,
                )
            except Exception:
                # Keep the message queued if submission fails.
                break

            if not accepted:
                # The Send Buffer is full. Keep the sample for later.
                break

            with self._lock:
                # Remove only the message that was just accepted.
                if (
                    self._sample_buffer
                    and self._sample_buffer[0] is message
                ):
                    self._sample_buffer.popleft()

            submitted += 1

        return submitted

    @property
    def sample_buffer_size(self) -> int:
        with self._lock:
            return len(self._sample_buffer)

    def get_sample_buffer(self) -> list[dict]:
        """Return a snapshot of the pending samples."""

        with self._lock:
            return list(self._sample_buffer)
