from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional, Tuple


@dataclass
class DatagramBuffer:
    max_size: int = 128
    _queue: Deque[Any] = field(default_factory=deque, init=False)
    receive_overflow_counter: int = 0

    def put(self, datagram: bytes, address: Optional[Tuple[str, int]] = None) -> None:
        if not isinstance(datagram, (bytes, bytearray)):
            raise TypeError("DatagramBuffer only accepts bytes-like datagrams.")

        if len(self._queue) >= self.max_size:
            self._queue.popleft()
            self.receive_overflow_counter += 1

        if address is None:
            self._queue.append(bytes(datagram))
        else:
            self._queue.append((bytes(datagram), address))

    def get(self) -> Optional[Any]:
        if not self._queue:
            return None
        return self._queue.popleft()

    def peek(self) -> Optional[Any]:
        if not self._queue:
            return None
        return self._queue[0]

    def clear(self) -> None:
        self._queue.clear()

    def __len__(self) -> int:
        return len(self._queue)

    def empty(self) -> bool:
        return len(self._queue) == 0