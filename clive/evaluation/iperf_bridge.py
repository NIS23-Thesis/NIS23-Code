"""Run real iperf3 UDP traffic through two NodeSockets.

Only iperf3's TCP control connection bypasses link emulation. UDP data and UDP
handshakes traverse CLIVE in both directions. One iperf stream per measurement.
"""
from __future__ import annotations
import json
import select
import shutil
import socket
import subprocess
import threading
import time


def run_iperf(a, b, *, seconds=3, bitrate_bps=1_000_000, payload_bytes=1200):
    executable = shutil.which("iperf3")
    if not executable:
        raise RuntimeError("iperf3 is required for the documented bandwidth baseline; install it first")
    if seconds < 1 or bitrate_bps <= 0 or not 16 <= payload_bytes <= 65507:
        raise ValueError("invalid iperf parameters")
    front = socket.socket()
    udp_front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_backend = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server = client = None
    stop = threading.Event()
    errors = []
    threads = []
    start_rx = b.metrics.snapshot()["bits_received"]
    start_tx = a.metrics.snapshot()["bits_sent"]

    def bridge_state():
        return dict(errors=errors,
                    sender_bits=a.metrics.snapshot()["bits_sent"] - start_tx,
                    receiver_bits=b.metrics.snapshot()["bits_received"] - start_rx,
                    tcp_control_emulated=False, udp_data_emulated=True)

    try:
        front.bind(("127.0.0.1", 0))
        front.listen(1)
        front.settimeout(.1)
        front_port = front.getsockname()[1]
        udp_front.bind(("127.0.0.1", front_port))
        udp_backend.bind(("127.0.0.1", 0))
        udp_front.setblocking(False)
        udp_backend.setblocking(False)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            server_port = reservation.getsockname()[1]
        server_address = ("127.0.0.1", server_port)
        server = subprocess.Popen([executable, "-s", "-1", "-B", "127.0.0.1", "-p", str(server_port), "--json"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def tcp_control():
            peers = []
            try:
                while not stop.is_set():
                    try:
                        incoming, _ = front.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                peers.append(incoming)
                upstream = socket.create_connection(server_address, timeout=2)
                peers.append(upstream)
                incoming.settimeout(2)
                while not stop.is_set():
                    readable, _, _ = select.select(peers, [], [], .05)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        target = upstream if source is incoming else incoming
                        target.sendall(data)
            except BaseException as exc:
                if not stop.is_set():
                    errors.append(f"TCP control bridge: {exc}")
            finally:
                for peer in peers:
                    peer.close()

        def udp_data():
            client_address = None
            try:
                while not stop.is_set():
                    ready, _, _ = select.select([udp_front, udp_backend, a, b], [], [], .002)
                    for endpoint in ready:
                        # Bounded draining; neither direction can monopolize the bridge.
                        for _ in range(64):
                            if endpoint is a or endpoint is b:
                                result = endpoint.recvfrom()
                                if result is None:
                                    break
                                data, _ = result
                                if endpoint is b:
                                    udp_backend.sendto(data, server_address)
                                elif client_address is not None:
                                    udp_front.sendto(data, client_address)
                            else:
                                try:
                                    data, address = endpoint.recvfrom(65535)
                                except BlockingIOError:
                                    break
                                if endpoint is udp_front:
                                    if client_address is None:
                                        client_address = address
                                    if address != client_address:
                                        continue
                                    sender, destination = a, (b.spec.overlay_ip, b.spec.overlay_port)
                                else:
                                    if address != server_address:
                                        continue
                                    sender, destination = b, (a.spec.overlay_ip, a.spec.overlay_port)
                                try:
                                    sender.sendto(data, destination)
                                except BlockingIOError:
                                    # Reported explicitly by CLIVE's local_drop metric.
                                    pass
            except BaseException as exc:
                if not stop.is_set():
                    errors.append(f"UDP data bridge: {exc}")

        for target in (tcp_control, udp_data):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            threads.append(thread)
        time.sleep(.1)
        client = subprocess.Popen([executable, "-c", "127.0.0.1", "-p", str(front_port),
                                   "-u", "-b", str(int(bitrate_bps)), "-l", str(payload_bytes),
                                   "-t", str(int(seconds)), "--json", "--connect-timeout", "2000"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = client.communicate(timeout=seconds+8)
        except subprocess.TimeoutExpired:
            client.kill()
            stdout, stderr = client.communicate()
            return dict(status="inconclusive", reason="iperf3 timed out (possibly unreachable UDP handshake)",
                        bridge_errors=errors, clive_bridge=bridge_state())
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return dict(status="failed", reason="iperf3 did not produce JSON", stderr=stderr, bridge_errors=errors)
        result["clive_bridge"] = bridge_state()
        result["status"] = "measured" if client.returncode == 0 and not errors else "failed"
        return result
    finally:
        stop.set()
        for process in (client, server):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=1)
        for thread in threads:
            thread.join(timeout=1)
        front.close()
        udp_front.close()
        udp_backend.close()
