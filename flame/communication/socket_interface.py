from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class SocketInterface:
    host: str = "127.0.0.1"
    port: int = 0
    timeout: float = 0.1

    def __post_init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(self.timeout)
        self.socket.bind((self.host, self.port))

    @property
    def address(self) -> Tuple[str, int]:
        return self.socket.getsockname() # Return the socket host and port.

    def sendto(self, payload: bytes, address: Tuple[str, int]) -> int:
        return self.socket.sendto(payload, address) # Send bytes to a UDP address.

    def recvfrom(self, max_bytes: int = 4096) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        # Receive one datagram or return None on timeout.
        try:
            return self.socket.recvfrom(max_bytes)
        except socket.timeout:
            return None

    def close(self) -> None:
        self.socket.close()
