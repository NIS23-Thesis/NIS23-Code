"""Periodic HELLO exchange over localhost, independent of Neighbor Module."""

from __future__ import annotations

import time

from flame.communication import CommunicationModule, PacketType, SocketInterface


def wait_for_hello(sensor: CommunicationModule, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = sensor.pop_neighbor_messages()
        if messages:
            return messages[0]
        time.sleep(0.02)
    raise RuntimeError("Timed out waiting for HELLO")


def main() -> None:
    cluster_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
    sensor_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
    cluster = CommunicationModule(cluster_socket, role=1)
    sensor = CommunicationModule(sensor_socket, role=0)

    try:
        print(f"[CH] {cluster_socket.address} -> [SENSOR] {sensor_socket.address}")
        for number in range(1, 6):
            cluster.send_message(
                {
                    "packet_type": PacketType.HELLO,
                    "role": 1,
                    "model_id": 1,
                },
                destination=sensor_socket.address,
            )
            print(f"[CH] HELLO #{number}")
            hello = wait_for_hello(sensor)
            print(
                f"[SENSOR] Received HELLO #{number} "
                f"seq={hello['sequence_number']} from {hello['source_address']}"
            )
            time.sleep(0.25)
    finally:
        sensor.stop()
        cluster.stop()
        sensor_socket.close()
        cluster_socket.close()


if __name__ == "__main__":
    main()
