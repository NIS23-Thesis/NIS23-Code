"""End-to-end FLAME communication demonstration using MNIST data."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image

from flame.communication import (
    CommunicationModule,
    PacketType,
    SocketInterface,
)


# ---------------------------------------------------------------------------
# Project and MNIST paths
# ---------------------------------------------------------------------------

CURRENT_PATH = Path(__file__).resolve()

PROJECT_ROOT = next(
    parent
    for parent in CURRENT_PATH.parents
    if (parent / "mnist").is_dir()
)

MNIST_DIR = PROJECT_ROOT / "mnist"
MNIST_FILE = MNIST_DIR / "mnist.npz"

if not MNIST_DIR.exists():
    raise FileNotFoundError(
        f"MNIST dataset directory not found: {MNIST_DIR}"
    )

if not MNIST_FILE.exists():
    raise FileNotFoundError(
        f"MNIST dataset file not found: {MNIST_FILE}"
    )


# ---------------------------------------------------------------------------
# Demo output directory
# ---------------------------------------------------------------------------

OUTPUT_DIR = CURRENT_PATH.parent / "demo_output"
SENT_DIR = OUTPUT_DIR / "sent"
RECEIVED_DIR = OUTPUT_DIR / "received"

SENT_DIR.mkdir(parents=True, exist_ok=True)
RECEIVED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# MNIST dataset
# ---------------------------------------------------------------------------

mnist = np.load(MNIST_FILE)

train_images = mnist["x_train"]
train_labels = mnist["y_train"]

print(f"[MNIST] Dataset directory: {MNIST_DIR}")
print(f"[MNIST] Dataset file: {MNIST_FILE}")
print(f"[MNIST] Training images: {len(train_images)}")
print(f"[MNIST] Training labels: {len(train_labels)}")
print(f"[MNIST] Image shape: {train_images.shape[1:]}")
print(f"[MNIST] Image data type: {train_images.dtype}")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def wait_until(predicate, timeout: float = 5.0) -> bool:
    """Wait until a condition becomes true or the timeout expires."""

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True

        time.sleep(0.02)

    return predicate()


def print_event(node: str, event: dict) -> None:
    """Print important Communication Module events."""

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
            f"window={event['window_start']}-"
            f"{event['window_end']} "
            f"received={event['received_blocks']}"
        )

    elif event_type == "ack_received":
        print(
            f"[{node}] Received {event['packet_type'].name} "
            f"id={event['message_id']} "
            f"window={event['window_start']}-"
            f"{event['window_end']} "
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


def save_mnist_images(
    images: np.ndarray,
    labels: np.ndarray,
    output_directory: Path,
) -> None:
    """Save MNIST image arrays as PNG files."""

    for index, (image, label) in enumerate(
        zip(images, labels)
    ):
        image_path = (
            output_directory
            / f"sample_{index}_label_{label}.png"
        )

        Image.fromarray(image).save(image_path)


# ---------------------------------------------------------------------------
# Main demonstration
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 60)
    print("FLAME END-TO-END MNIST COMMUNICATION DEMONSTRATION")
    print("=" * 60)

    # -----------------------------------------------------------------------
    # Step 1: Select MNIST samples
    # -----------------------------------------------------------------------

    print("\n" + "-" * 60)
    print("STEP 1: MNIST DATA PREPARATION")
    print("-" * 60)

    sample_count = 4

    selected_images = train_images[:sample_count]
    selected_labels = train_labels[:sample_count]

    print(
        f"[SENSING MODULE] Selected {sample_count} MNIST samples"
    )

    for index, label in enumerate(selected_labels):
        print(
            f"[SENSING MODULE] "
            f"Sample {index}: digit {label}"
        )

    print(
        f"[SENSING MODULE] Image dimensions: "
        f"{selected_images.shape[1]} x "
        f"{selected_images.shape[2]}"
    )

    # Each image is 28 x 28 uint8 pixels.
    # 4 images = 4 x 28 x 28 = 3136 bytes.
    payload = selected_images.tobytes()

    print(
        f"[SENSING MODULE] Payload size: "
        f"{len(payload)} bytes"
    )

    save_mnist_images(
        selected_images,
        selected_labels,
        SENT_DIR,
    )

    print(
        f"[SENSING MODULE] Original images saved to: "
        f"{SENT_DIR}"
    )

    # -----------------------------------------------------------------------
    # Step 2: Create local UDP sockets
    # -----------------------------------------------------------------------

    print("\n" + "-" * 60)
    print("STEP 2: NODE INITIALIZATION")
    print("-" * 60)

    cluster_socket = SocketInterface(
        host="127.0.0.1",
        port=0,
        timeout=0.02,
    )

    sensor_socket = SocketInterface(
        host="127.0.0.1",
        port=0,
        timeout=0.02,
    )

    cluster = CommunicationModule(
        cluster_socket,
        role=1,
        event_handler=lambda event: print_event(
            "CH",
            event,
        ),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    sensor = CommunicationModule(
        sensor_socket,
        role=0,
        event_handler=lambda event: print_event(
            "SENSOR",
            event,
        ),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    print(
        f"[CH] Listening on {cluster_socket.address}"
    )

    print(
        f"[SENSOR] Listening on {sensor_socket.address}"
    )

    try:
        # -------------------------------------------------------------------
        # Step 3: HELLO / Discovery
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 3: HELLO / DISCOVERY")
        print("-" * 60)

        for hello_number in range(1, 4):
            print(
                f"[CH] Sending HELLO #{hello_number}"
            )

            cluster.send_message(
                {
                    "packet_type": PacketType.HELLO,
                    "role": 1,
                    "model_id": 1,
                },
                destination=sensor_socket.address,
            )

            if not wait_until(
                lambda: len(sensor.neighbor_buffer) >= 1
            ):
                raise RuntimeError(
                    "HELLO was not received by Sensor."
                )

            hello = sensor.pop_neighbor_messages()[0]

            print(
                f"[SENSOR] Received HELLO #{hello_number} "
                f"seq={hello['sequence_number']} "
                f"from {hello['source_address']}"
            )

            time.sleep(0.1)

        # -------------------------------------------------------------------
        # Step 4: Send MNIST through FLAME
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 4: SEND AND REASSEMBLE MNIST SENSOR_DATA")
        print("-" * 60)

        data_id = 1

        print(
            f"[SENSOR] Starting SENSOR_DATA "
            f"id={data_id}"
        )

        print(
            f"[SENSOR] Application payload: "
            f"{len(payload)} bytes"
        )

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

        if not wait_until(
            lambda: len(cluster.sensor_buffer) >= 1
        ):
            raise RuntimeError(
                "MNIST SENSOR_DATA was not delivered."
            )

        received_message = cluster.pop_sensor_messages()[0]

        received_payload = received_message["payload"]

        print(
            f"[CH] Complete SENSOR_DATA received"
        )

        print(
            f"[CH] Data ID: "
            f"{received_message['data_id']}"
        )

        print(
            f"[CH] Reassembled payload: "
            f"{len(received_payload)} bytes"
        )

        print(
            f"[CH] Source: "
            f"{received_message['source_address']}"
        )

        # -------------------------------------------------------------------
        # Step 5: Reconstruct MNIST images
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 6: RECONSTRUCT MNIST IMAGES")
        print("-" * 60)

        received_images = np.frombuffer(
            received_payload,
            dtype=np.uint8,
        ).reshape(
            sample_count,
            28,
            28,
        )

        print(
            f"[CH] Reconstructed "
            f"{len(received_images)} images"
        )

        for index, label in enumerate(selected_labels):
            print(
                f"[CH] Sample {index}: "
                f"digit {label}"
            )

        save_mnist_images(
            received_images,
            selected_labels,
            RECEIVED_DIR,
        )

        print(
            f"[CH] Reconstructed images saved to: "
            f"{RECEIVED_DIR}"
        )

        # -------------------------------------------------------------------
        # Step 6: Verify data integrity
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 7: DATA INTEGRITY VERIFICATION")
        print("-" * 60)

        payload_identical = (
            payload == received_payload
        )

        images_identical = np.array_equal(
            selected_images,
            received_images,
        )

        print(
            f"[VERIFY] Payload identical: "
            f"{payload_identical}"
        )

        print(
            f"[VERIFY] Images identical: "
            f"{images_identical}"
        )

        if payload_identical and images_identical:
            print(
                "[VERIFY] SUCCESS: "
                "MNIST data was transmitted and "
                "reassembled without modification."
            )
        else:
            raise RuntimeError(
                "MNIST data integrity verification failed."
            )

    finally:
        sensor.stop()
        cluster.stop()

        sensor_socket.close()
        cluster_socket.close()


if __name__ == "__main__":
    main()
