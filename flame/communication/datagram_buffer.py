'''
The datagram buffer is a thread-safe queue that stores received datagrams.
 It allows for adding datagrams with optional sender addresses, 
 retrieving the oldest datagram, accessing the oldest datagram, 
 purging the queue, and checking the number of queued datagrams or if the queue is empty. 

 The buffer has a maximum size, and if this size is exceeded, the oldest datagram is 
 discarded to make room for new ones, while also keeping track of how many datagrams 
 have been dropped due to overflow.
'''

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Deque, Optional, Tuple


@dataclass
class DatagramBuffer:
    # Buffer for storing received datagrams with optional sender addresses.
    max_size: int = 128 # Current Maximum size of the queue.
    _queue: Deque[Any] = field(default_factory=deque, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    receive_overflow_counter: int = 0

    def put(self, datagram: bytes, address: Optional[Tuple[str, int]] = None) -> None:
        # Add a datagram and optional sender address.
        if not isinstance(datagram, (bytes, bytearray)):
            raise TypeError("DatagramBuffer only accepts bytes-like datagrams.")

        with self._lock:
            if len(self._queue) >= self.max_size:
                self._queue.popleft()
                self.receive_overflow_counter += 1

            if address is None:
                self._queue.append(bytes(datagram))
            else:
                self._queue.append((bytes(datagram), address))

    def get(self) -> Optional[Any]:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.popleft() # Remove and return the oldest datagram from the queue.

    def peek(self) -> Optional[Any]:
        with self._lock:
            if not self._queue:
                return None
            return self._queue[0] # Return the oldest datagram without removing it.

    def clear(self) -> None:
        with self._lock:
            self._queue.clear() # Purge the queue of all datagrams. gg

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue) # Number of items in the queue

    def empty(self) -> bool:
        with self._lock:
            return len(self._queue) == 0 # Confirm whether the queue is empty.
