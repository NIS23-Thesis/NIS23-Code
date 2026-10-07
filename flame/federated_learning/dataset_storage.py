from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DatasetEntry:
    data_id: int
    sensor_type: int
    sequence_number: int
    payload: bytes


class DatasetStorage:
    """Stores locally received sensor observations."""

    def __init__(self, capacity: int = 100):
        if capacity <= 0:
            raise ValueError("Dataset storage capacity must be greater than 0.")

        self.capacity = capacity
        self._entries: List[DatasetEntry] = []

    def add(self, entry: DatasetEntry) -> None:
        """Store a dataset entry using FIFO capacity management."""

        if not isinstance(entry, DatasetEntry):
            raise TypeError("entry must be a DatasetEntry.")

        if not isinstance(entry.payload, bytes):
            raise TypeError("Dataset payload must be bytes.")

        self._entries.append(entry)

        if len(self._entries) > self.capacity:
            self._entries.pop(0)

    def get_all(self) -> List[DatasetEntry]:
        """Return all currently stored dataset entries."""

        return list(self._entries)

    def get_count(self) -> int:
        """Return the number of stored dataset entries."""

        return len(self._entries)

    def get_latest(self) -> Optional[DatasetEntry]:
        """Return the most recently stored entry."""

        if not self._entries:
            return None

        return self._entries[-1]

    def clear(self) -> None:
        """Remove all stored dataset entries."""

        self._entries.clear()