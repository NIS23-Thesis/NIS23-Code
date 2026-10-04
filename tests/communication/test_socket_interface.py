import sys
import socket
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.socket_interface import SocketInterface


class SocketInterfaceTests(unittest.TestCase):
    def test_underlying_socket_is_udp_datagram_socket(self):
        udp = SocketInterface(host="127.0.0.1", port=0, timeout=0.1)
        try:
            self.assertEqual(
                udp.socket.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE),
                socket.SOCK_DGRAM,
            )
            self.assertEqual(udp.socket.family, socket.AF_INET)
        finally:
            udp.close()

    def test_send_and_receive_over_localhost(self):
        a = SocketInterface(host="127.0.0.1", port=0, timeout=1.0)
        b = SocketInterface(host="127.0.0.1", port=0, timeout=1.0)
        try:
            a.sendto(b"hello", b.address)
            received = b.recvfrom()
            self.assertIsNotNone(received)
            raw, addr = received
            self.assertEqual(raw, b"hello")
            self.assertEqual(addr[0], "127.0.0.1")
        finally:
            a.close()
            b.close()

    def test_recvfrom_returns_none_on_timeout(self):
        s = SocketInterface(host="127.0.0.1", port=0, timeout=0.05)
        try:
            self.assertIsNone(s.recvfrom())
        finally:
            s.close()

    def test_ephemeral_port_is_nonzero_after_bind(self):
        s = SocketInterface(host="127.0.0.1", port=0, timeout=0.1)
        try:
            self.assertNotEqual(s.address[1], 0)
        finally:
            s.close()

    def test_reuse_port_allows_multiple_discovery_listeners(self):
        if not hasattr(socket, "SO_REUSEPORT"):
            self.skipTest("SO_REUSEPORT is unavailable on this platform.")

        first = SocketInterface(
            host="127.0.0.1",
            port=0,
            timeout=0.1,
            reuse_address=True,
            reuse_port=True,
        )
        second = None
        try:
            second = SocketInterface(
                host="127.0.0.1",
                port=first.address[1],
                timeout=0.1,
                reuse_address=True,
                reuse_port=True,
            )
            self.assertEqual(second.address[1], first.address[1])
        finally:
            if second is not None:
                second.close()
            first.close()


if __name__ == "__main__":
    unittest.main()
