"""Real-network FLAME Communication Module test suite."""

from __future__ import annotations

import sys
import time

from flame.communication import CommunicationModule, PacketType, SocketInterface


# ============================================================
# NETWORK CONFIGURATION
# ============================================================

CH_IP = "192.168.254.120"
CH_PORT = 5000

SENSOR_PORT = 5001


# ============================================================
# HELPER FUNCTIONS
# ============================================================

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


def create_cluster_head() -> CommunicationModule:
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

    cluster._test_socket = cluster_socket

    return cluster


def create_sensor() -> CommunicationModule:
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

    sensor._test_socket = sensor_socket

    return sensor


def wait_for_sensor_data(
    cluster: CommunicationModule,
    timeout: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if len(cluster.sensor_buffer) > 0:
            return cluster.pop_sensor_messages()[0]

        time.sleep(0.02)

    raise RuntimeError("Timed out waiting for SENSOR_DATA.")


def send_sensor_data(
    sensor: CommunicationModule,
    data_id: int,
    payload_size: int,
) -> None:
    payload = (
        f"network-test-{data_id}:".encode()
        + (b"x" * payload_size)
    )

    print()
    print(
        f"[SENSOR] Sending SENSOR_DATA "
        f"id={data_id} bytes={len(payload)}"
    )

    sensor.send_message(
        {
            "packet_type": PacketType.SENSOR_DATA,
            "role": 0,
            "data_id": data_id,
            "sensor_type": 1,
            "payload": payload,
        },
        destination=(CH_IP, CH_PORT),
    )


# ============================================================
# TEST 1 — BASIC UDP COMMUNICATION
# ============================================================

def test_basic(sensor: CommunicationModule, cluster: CommunicationModule) -> None:
    print()
    print("=" * 60)
    print("TEST 1 — BASIC UDP COMMUNICATION")
    print("=" * 60)

    send_sensor_data(
        sensor,
        data_id=1,
        payload_size=100,
    )

    message = wait_for_sensor_data(cluster)

    print(
        f"[CH] TEST 1 PASSED — "
        f"Received {len(message['payload'])} bytes"
    )


# ============================================================
# TEST 2 — FRAGMENTATION & REASSEMBLY
# ============================================================

def test_fragmentation(
    sensor: CommunicationModule,
    cluster: CommunicationModule,
) -> None:
    print()
    print("=" * 60)
    print("TEST 2 — FRAGMENTATION & REASSEMBLY")
    print("=" * 60)

    send_sensor_data(
        sensor,
        data_id=2,
        payload_size=3500,
    )

    message = wait_for_sensor_data(cluster)

    print(
        f"[CH] TEST 2 PASSED — "
        f"Reassembled {len(message['payload'])} bytes"
    )


# ============================================================
# TEST 3 — PACKET LOSS & SELECTIVE RETRANSMISSION
# ============================================================

def test_packet_loss(
    sensor: CommunicationModule,
    cluster: CommunicationModule,
) -> None:
    print()
    print("=" * 60)
    print("TEST 3 — PACKET LOSS & SELECTIVE RETRANSMISSION")
    print("=" * 60)

    # Intentionally drop block 1 once.
    sensor.drop_block_number = 1
    sensor.drop_message_id = 3
    sensor.drop_once = True

    send_sensor_data(
        sensor,
        data_id=3,
        payload_size=3500,
    )

    message = wait_for_sensor_data(cluster)

    print(
        f"[CH] TEST 3 PASSED — "
        f"Message recovered after simulated packet loss "
        f"({len(message['payload'])} bytes)"
    )


# ============================================================
# TEST 4 — WINDOW ADVANCEMENT
# ============================================================

def test_window(
    sensor: CommunicationModule,
    cluster: CommunicationModule,
) -> None:
    print()
    print("=" * 60)
    print("TEST 4 — WINDOW ADVANCEMENT")
    print("=" * 60)

    send_sensor_data(
        sensor,
        data_id=4,
        payload_size=7000,
    )

    message = wait_for_sensor_data(cluster)

    print(
        f"[CH] TEST 4 PASSED — "
        f"Received {len(message['payload'])} bytes"
    )


# ============================================================
# TEST 5 — MULTIPLE TRANSFERS
# ============================================================

def test_multiple_transfers(
    sensor: CommunicationModule,
    cluster: CommunicationModule,
) -> None:
    print()
    print("=" * 60)
    print("TEST 5 — MULTIPLE TRANSFERS")
    print("=" * 60)

    send_sensor_data(sensor, data_id=5, payload_size=2000)
    send_sensor_data(sensor, data_id=6, payload_size=2500)
    send_sensor_data(sensor, data_id=7, payload_size=3000)

    received_ids = set()
    deadline = time.monotonic() + 15

    while time.monotonic() < deadline and len(received_ids) < 3:
        if len(cluster.sensor_buffer) > 0:
            messages = cluster.pop_sensor_messages()

            for message in messages:
                received_ids.add(message["data_id"])

        time.sleep(0.02)

    if received_ids != {5, 6, 7}:
        raise RuntimeError(
            f"TEST 5 FAILED — Received IDs: {sorted(received_ids)}"
        )

    print(
        "[CH] TEST 5 PASSED — "
        "All three transfers received successfully"
    )


# ============================================================
# CLUSTER HEAD MODE
# ============================================================

def run_cluster_head() -> None:
    print("=" * 60)
    print("FLAME REAL-NETWORK COMMUNICATION TEST")
    print("=" * 60)
    print()
    print(f"[CH] Listening on 0.0.0.0:{CH_PORT}")
    print("[CH] Waiting for SENSOR tests...")
    print()

    cluster = create_cluster_head()

    try:
        while True:
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[CH] Stopping...")

    finally:
        cluster.stop()
        cluster._test_socket.close()


# ============================================================
# SENSOR MODE
# ============================================================

def run_sensor() -> None:
    print("=" * 60)
    print("FLAME REAL-NETWORK COMMUNICATION TEST")
    print("=" * 60)
    print()
    print(f"[SENSOR] Sending to {CH_IP}:{CH_PORT}")
    print()

    sensor = create_sensor()

    try:
        time.sleep(1)

        print("Select a test:")
        print()
        print("[1] Basic UDP Communication")
        print("[2] Fragmentation & Reassembly")
        print("[3] Packet Loss & Selective Retransmission")
        print("[4] Window Advancement")
        print("[5] Multiple Transfers")
        print("[6] Run ALL tests")
        print()

        choice = input("Enter choice: ").strip()

        # Cluster-side buffers are only used for synchronization/output.
        # The actual tests run from the sensor side.

        # --------------------------------------------------------
        # TEST 1
        # --------------------------------------------------------
        if choice == "1":
            print("\nRun TEST 1 on the CH computer first.")
            time.sleep(1)
            print("Starting TEST 1...")
            send_sensor_data(sensor, 1, 100)

        # --------------------------------------------------------
        # TEST 2
        # --------------------------------------------------------
        elif choice == "2":
            print("\nStarting TEST 2...")
            send_sensor_data(sensor, 2, 3500)

        # --------------------------------------------------------
        # TEST 3
        # --------------------------------------------------------
        elif choice == "3":
            print("\nStarting TEST 3...")
            sensor.drop_block_number = 1
            sensor.drop_message_id = 3
            sensor.drop_once = True
            send_sensor_data(sensor, 3, 3500)

        # --------------------------------------------------------
        # TEST 4
        # --------------------------------------------------------
        elif choice == "4":
            print("\nStarting TEST 4...")
            send_sensor_data(sensor, 4, 7000)

        # --------------------------------------------------------
        # TEST 5
        # --------------------------------------------------------
        elif choice == "5":
            print("\nStarting TEST 5...")

            send_sensor_data(sensor, 5, 2000)
            time.sleep(0.5)

            send_sensor_data(sensor, 6, 2500)
            time.sleep(0.5)

            send_sensor_data(sensor, 7, 3000)

        # --------------------------------------------------------
        # ALL TESTS
        # --------------------------------------------------------
        elif choice == "6":
            print()
            print("Running ALL network tests...")
            print()

            send_sensor_data(sensor, 1, 100)
            time.sleep(1)

            send_sensor_data(sensor, 2, 3500)
            time.sleep(1)

            sensor.drop_block_number = 1
            sensor.drop_message_id = 3
            sensor.drop_once = True

            send_sensor_data(sensor, 3, 3500)
            time.sleep(1)

            send_sensor_data(sensor, 4, 7000)
            time.sleep(1)

            send_sensor_data(sensor, 5, 2000)
            time.sleep(0.5)

            send_sensor_data(sensor, 6, 2500)
            time.sleep(0.5)

            send_sensor_data(sensor, 7, 3000)

        else:
            print("Invalid choice.")

        print()
        print("[SENSOR] Tests sent. Waiting for communication...")
        time.sleep(10)

    except KeyboardInterrupt:
        print("\n[SENSOR] Stopping...")

    finally:
        sensor.stop()
        sensor._test_socket.close()


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage:")
        print()
        print("  Cluster Head:")
        print(
            "  python -m tests.communication.network_sensor_cluster ch"
        )
        print()
        print("  Sensor:")
        print(
            "  python -m tests.communication.network_sensor_cluster sensor"
        )
        raise SystemExit(1)

    mode = sys.argv[1].lower()

    if mode == "ch":
        run_cluster_head()

    elif mode == "sensor":
        run_sensor()

    else:
        print("Invalid mode. Use 'sensor' or 'ch'.")
        raise SystemExit(1)