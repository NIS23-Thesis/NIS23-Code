"""Real-network FLAME communication demonstration."""

from __future__ import annotations

import time

from flame.communication import CommunicationModule, PacketType, SocketInterface


CH_IP = "192.168.254.120" # hard coded ip addr of PC-A
CH_PORT = 5000

SENSOR_IP = "192.168.240.1"
SENSOR_PORT = 5001


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)

    return predicate()


def print_event(node: str, event: dict) -> None:
    event_type = event["type"]

    if event_type == "fragment_sent":
        print(
            f"[{node}] Sending {event['packet_type'].name} "
            f"id={event['message_id']} "
            f"block={event['block_number']} "
            f"window={event['window_start']}-{event['window_end']} "
            f"reason={event['reason']}"
        )

    elif event_type == "fragment_received":
        print(
            f"[{node}] Received {event['packet_type'].name} "
            f"id={event['message_id']} "
            f"block={event['block_number']}/"
            f"{event['last_block_number']}"
        )

    elif event_type == "ack_sent":
        print(
            f"[{node}] Sending {event['packet_type'].name} "
            f"id={event['message_id']} "
            f"received={event['received_blocks']}"
        )

    elif event_type == "ack_received":
        print(
            f"[{node}] Received {event['packet_type'].name} "
            f"id={event['message_id']} "
            f"received={event['received_blocks']}"
        )

    elif event_type == "window_advanced":
        print(
            f"[{node}] Window advanced to "
            f"{event['window_start']}-{event['window_end']}"
        )

    elif event_type == "transfer_complete":
        print(
            f"[{node}] Transfer complete "
            f"id={event['message_id']}"
        )

    elif event_type == "message_reassembled":
        print(
            f"[{node}] Reassembled "
            f"{event['packet_type'].name} "
            f"id={event['message_id']} "
            f"from {event['fragment_count']} fragments"
        )


def run_cluster_head() -> None:
    print(f"[CH] Starting on {CH_IP}:{CH_PORT}")

    cluster_socket = SocketInterface(
        host="0.0.0.0",
        port=CH_PORT,
        timeout=0.02,
    )

    cluster = CommunicationModule(
        cluster_socket,
        role=1,
        event_handler=lambda event: print_event("CH", event),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    try:
        print("[CH] Waiting for SENSOR_DATA...")

        while True:
            time.sleep(0.1)

            if len(cluster.sensor_buffer) > 0:
                message = cluster.pop_sensor_messages()[0]

                print(
                    f"[CH] Complete SENSOR_DATA received "
                    f"id={message['data_id']} "
                    f"bytes={len(message['payload'])} "
                    f"from={message['source_address']}"
                )
                break

    finally:
        cluster.stop()
        cluster_socket.close()


def run_sensor() -> None:
    print(f"[SENSOR] Starting on {SENSOR_IP}:{SENSOR_PORT}")

    sensor_socket = SocketInterface(
        host="0.0.0.0",
        port=SENSOR_PORT,
        timeout=0.02,
    )

    sensor = CommunicationModule(
        sensor_socket,
        role=0,
        event_handler=lambda event: print_event("SENSOR", event),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    try:
        time.sleep(1)

        payload = (
            b"network-test:"
            + (b"x" * 2500)
        )

        print(
            f"[SENSOR] Sending SENSOR_DATA "
            f"id=42 bytes={len(payload)}"
        )

        sensor.send_message(
            {
                "packet_type": PacketType.SENSOR_DATA,
                "role": 0,
                "data_id": 42,
                "sensor_type": 1,
                "payload": payload,
            },
            destination=(CH_IP, CH_PORT),
        )

        print("[SENSOR] Waiting for transfer to complete...")

        time.sleep(3)

    finally:
        sensor.stop()
        sensor_socket.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2 or sys.argv[1] not in {"sensor", "ch"}:
        print("Usage:")
        print("  python -m tests.communication.network_sensor_cluster sensor")
        print("  python -m tests.communication.network_sensor_cluster ch")
        raise SystemExit(1)

    if sys.argv[1] == "sensor":
        run_sensor()
    else:
        run_cluster_head()