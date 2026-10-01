"""End-to-end localhost FLAME communication demonstration."""

from __future__ import annotations

import time

from flame.communication import CommunicationModule, PacketType, SocketInterface


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
            f"id={event['message_id']} block={event['block_number']} "
            f"window={event['window_start']}-{event['window_end']} "
            f"reason={event['reason']}"
        )
    elif event_type == "fragment_received":
        print(
            f"[{node}] Received {event['packet_type'].name} "
            f"id={event['message_id']} block={event['block_number']}/"
            f"{event['last_block_number']}"
        )
    elif event_type == "ack_sent":
        print(
            f"[{node}] Sending {event['packet_type'].name} id={event['message_id']} "
            f"window={event['window_start']}-{event['window_end']} "
            f"received={event['received_blocks']}"
        )
    elif event_type == "ack_received":
        print(
            f"[{node}] Received {event['packet_type'].name} id={event['message_id']} "
            f"window={event['window_start']}-{event['window_end']} "
            f"received={event['received_blocks']}"
        )
    elif event_type == "window_advanced":
        print(
            f"[{node}] Window advanced to "
            f"{event['window_start']}-{event['window_end']}"
        )
    elif event_type == "transfer_complete":
        print(f"[{node}] Transfer complete id={event['message_id']}")
    elif event_type == "message_reassembled":
        print(
            f"[{node}] Reassembled {event['packet_type'].name} "
            f"id={event['message_id']} from {event['fragment_count']} fragments"
        )


def main() -> None:
    cluster_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
    sensor_socket = SocketInterface(host="127.0.0.1", port=0, timeout=0.02)
    cluster = CommunicationModule(
        cluster_socket,
        role=1,
        event_handler=lambda event: print_event("CH", event),
        ack_timeout=0.2,
        ack_delay=0.03,
    )
    sensor = CommunicationModule(
        sensor_socket,
        role=0,
        event_handler=lambda event: print_event("SENSOR", event),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    print(f"[CH] Listening on {cluster_socket.address}")
    print(f"[SENSOR] Listening on {sensor_socket.address}")

    try:
        for hello_number in range(1, 4):
            print(f"[CH] Sending HELLO #{hello_number}")
            cluster.send_message(
                {
                    "packet_type": PacketType.HELLO,
                    "role": 1,
                    "model_id": 1,
                },
                destination=sensor_socket.address,
            )
            wait_until(lambda: len(sensor.neighbor_buffer) >= 1)
            hello = sensor.pop_neighbor_messages()[0]
            print(
                f"[SENSOR] Received HELLO #{hello_number} "
                f"seq={hello['sequence_number']} from {hello['source_address']}"
            )
            time.sleep(0.1)

        # add condition < 1500 
        for data_id, payload_size in ((42, 500), (43, 200)):
            payload = f"reading-{data_id}:".encode() + (b"x" * payload_size)
            print(f"\n[SENSOR] Starting SENSOR_DATA id={data_id}, bytes={len(payload)}")
            sensor.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": data_id,
                    "sensor_type": 1,
                    "payload": payload,
                },
                destination=cluster_socket.address,
            )

            if not wait_until(lambda: len(cluster.sensor_buffer) >= 1):
                raise RuntimeError(f"SENSOR_DATA id={data_id} was not delivered")
            complete = cluster.pop_sensor_messages()[0]
            print(
                f"[CH] Delivering complete SENSOR_DATA id={complete['data_id']} "
                f"bytes={len(complete['payload'])} from {complete['source_address']}"
            )
            time.sleep(0.1)
    finally:
        sensor.stop()
        cluster.stop()
        sensor_socket.close()
        cluster_socket.close()


if __name__ == "__main__":
    main()
