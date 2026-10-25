from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class SocketInterface:
    host: str = "127.0.0.1"
    port: int = 0
    timeout: float = 0.1
    broadcast: bool = False
    reuse_address: bool = False
    reuse_port: bool = False

    def __post_init__(self) -> None:
        self.socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

        self.socket.settimeout(self.timeout)

        if self.broadcast:
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_BROADCAST,
                1,
            )

        if self.reuse_address:
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )

        if self.reuse_port:
            reuse_port_option = getattr(socket, "SO_REUSEPORT", None)

            if reuse_port_option is not None:
                self.socket.setsockopt(
                    socket.SOL_SOCKET,
                    reuse_port_option,
                    1,
                )
            else:
                # Windows fallback. This does not fully replicate
                # SO_REUSEPORT behavior on other platforms.
                self.socket.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_REUSEADDR,
                    1,
                )

        self.socket.bind((self.host, self.port))

    @property
    def address(self) -> Tuple[str, int]:
        return self.socket.getsockname()

    def sendto(
        self,
        payload: bytes,
        address: Tuple[str, int],
    ) -> int:
        return self.socket.sendto(payload, address)

    def recvfrom(
        self,
        max_bytes: int = 4096,
    ) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        try:
            return self.socket.recvfrom(max_bytes)
        except socket.timeout:
            return None

    def close(self) -> None:
        self.socket.close()
