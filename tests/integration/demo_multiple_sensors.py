"""FLAME multi-Sensor to one Cluster Head integration demonstration."""

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
from flame.neighbor import (
    AssociationState,
    NeighborModule,
)


# ---------------------------------------------------------------------------
# Project and MNIST paths
# ---------------------------------------------------------------------------

CURRENT_PATH = Path(__file__).resolve()

PROJECT_ROOT = next(
    parent
    for parent in CURRENT_PATH.parents
    if (parent / "nmist").is_dir()
)

MNIST_FILE = PROJECT_ROOT / "nmist" / "mnist.npz"

if not MNIST_FILE.exists():
    raise FileNotFoundError(
        f"MNIST dataset file not found: {MNIST_FILE}"
    )


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

OUTPUT_DIR = CURRENT_PATH.parent / "demo_multiple_sensors_output"

MNIST_OUTPUT_DIR = OUTPUT_DIR / "mnist"

MNIST_OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SENSOR_COUNT = 3
SAMPLES_PER_SENSOR = 4

# Each Sensor receives a different group of MNIST samples.
SENSOR_SAMPLE_STARTS = {
    1: 0,
    2: 4,
    3: 8,
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def wait_until(
    predicate,
    timeout: float = 5.0,
) -> bool:
    """Wait until a condition becomes true or timeout expires."""

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True

        time.sleep(0.02)

    return predicate()


def print_event(
    node: str,
    event: dict,
) -> None:
    """Print important Communication Module events."""

    event_type = event["type"]

    if event_type == "fragment_sent":
        print(
            f"[{node}] Sending "
            f"{event['packet_type'].name} "
            f"id={event['message_id']} "
            f"block={event['block_number']} "
            f"window={event['window_start']}-"
            f"{event['window_end']} "
            f"reason={event['reason']}"
        )

    elif event_type == "fragment_received":
        print(
            f"[{node}] Received "
            f"{event['packet_type'].name} "
            f"id={event['message_id']} "
            f"block={event['block_number']}/"
            f"{event['last_block_number']}"
        )

    elif event_type == "ack_sent":
        print(
            f"[{node}] Sending "
            f"{event['packet_type'].name} "
            f"id={event['message_id']} "
            f"window={event['window_start']}-"
            f"{event['window_end']} "
            f"received={event['received_blocks']}"
        )

    elif event_type == "ack_received":
        print(
            f"[{node}] Received "
            f"{event['packet_type'].name} "
            f"id={event['message_id']} "
            f"window={event['window_start']}-"
            f"{event['window_end']} "
            f"received={event['received_blocks']}"
        )

    elif event_type == "window_advanced":
        print(
            f"[{node}] Window advanced to "
            f"{event['window_start']}-"
            f"{event['window_end']}"
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
    """Save MNIST images as PNG files."""

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    for index, (image, label) in enumerate(
        zip(images, labels)
    ):
        image_path = (
            output_directory
            / f"sample_{index}_label_{label}.png"
        )

        Image.fromarray(image).save(image_path)

def reconstruct_mnist_images(
    payload: bytes,
    image_count: int,
) -> np.ndarray:
    """Reconstruct MNIST images from a received payload."""

    expected_size = image_count * 28 * 28

    if len(payload) != expected_size:
        raise ValueError(
            f"Invalid MNIST payload size: "
            f"expected {expected_size} bytes, "
            f"received {len(payload)} bytes."
        )

    return np.frombuffer(
        payload,
        dtype=np.uint8,
    ).reshape(
        image_count,
        28,
        28,
    )

# ---------------------------------------------------------------------------
# Main demonstration
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 60)
    print("FLAME MULTIPLE SENSOR TO ONE CH DEMONSTRATION")
    print("=" * 60)

    # -----------------------------------------------------------------------
    # Load MNIST
    # -----------------------------------------------------------------------

    mnist = np.load(MNIST_FILE)

    train_images = mnist["x_train"]
    train_labels = mnist["y_train"]

    print(
        f"\n[MNIST] Training images: "
        f"{len(train_images)}"
    )

    print(
        f"[MNIST] Image shape: "
        f"{train_images.shape[1:]}"
    )

    # -----------------------------------------------------------------------
    # Step 1: Prepare Sensor data
    # -----------------------------------------------------------------------

    print("\n" + "-" * 60)
    print("STEP 1: PREPARE MNIST DATA FOR EACH SENSOR")
    print("-" * 60)

    sensor_data = {}

    for sensor_id in range(1, SENSOR_COUNT + 1):
        start = SENSOR_SAMPLE_STARTS[sensor_id]
        end = start + SAMPLES_PER_SENSOR

        images = train_images[start:end]
        labels = train_labels[start:end]

        payload = images.tobytes()

        sensor_data[sensor_id] = {
            "images": images,
            "labels": labels,
            "payload": payload,
        }

        sensor_output_dir = (
            MNIST_OUTPUT_DIR
            / f"sensor_{sensor_id}"
        )

        save_mnist_images(
            images,
            labels,
            sensor_output_dir / "sent",
        )

        print(
            f"[SENSOR {sensor_id}] "
            f"Selected samples {start}-{end - 1}"
        )

        print(
            f"[SENSOR {sensor_id}] "
            f"Digits: {list(labels)}"
        )

        print(
            f"[SENSOR {sensor_id}] "
            f"Payload: {len(payload)} bytes"
        )

    # -----------------------------------------------------------------------
    # Step 2: Create CH
    # -----------------------------------------------------------------------

    print("\n" + "-" * 60)
    print("STEP 2: INITIALIZE CLUSTER HEAD")
    print("-" * 60)

    cluster_socket = SocketInterface(
        host="127.0.0.1",
        port=0,
        timeout=0.02,
    )

    cluster_neighbor = NeighborModule()

    cluster = CommunicationModule(
        cluster_socket,
        role=1,
        neighbor_module=cluster_neighbor,
        event_handler=lambda event: print_event(
            "CH",
            event,
        ),
        ack_timeout=0.2,
        ack_delay=0.03,
    )

    print(
        f"[CH] Listening on "
        f"{cluster_socket.address}"
    )

    # -----------------------------------------------------------------------
    # Step 3: Create Sensors
    # -----------------------------------------------------------------------

    print("\n" + "-" * 60)
    print("STEP 3: INITIALIZE SENSOR NODES")
    print("-" * 60)

    sensors = {}

    try:
        for sensor_id in range(1, SENSOR_COUNT + 1):
            sensor_socket = SocketInterface(
                host="127.0.0.1",
                port=0,
                timeout=0.02,
            )

            sensor_neighbor = NeighborModule()

            sensor = CommunicationModule(
                sensor_socket,
                role=0,
                neighbor_module=sensor_neighbor,
                event_handler=lambda event,
                    sensor_id=sensor_id: print_event(
                        f"SENSOR {sensor_id}",
                        event,
                    ),
                ack_timeout=0.2,
                ack_delay=0.03,
            )

            sensors[sensor_id] = {
                "communication": sensor,
                "socket": sensor_socket,
                "neighbor": sensor_neighbor,
            }

            print(
                f"[SENSOR {sensor_id}] Listening on "
                f"{sensor_socket.address}"
            )

        # -------------------------------------------------------------------
        # Step 4: HELLO / Discovery
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 4: HELLO / DISCOVERY")
        print("-" * 60)

        for sensor_id in range(1, SENSOR_COUNT + 1):
            sensor = sensors[sensor_id]["communication"]
            sensor_socket = sensors[sensor_id]["socket"]
            sensor_neighbor = sensors[sensor_id]["neighbor"]

            print(
                f"[CH] Sending HELLO to SENSOR {sensor_id}"
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
                lambda sensor=sensor:
                    len(sensor.neighbor_buffer) >= 1
            ):
                raise RuntimeError(
                    f"SENSOR {sensor_id} did not receive HELLO."
                )

            hello = sensor.pop_neighbor_messages()[0]

            print(
                f"[SENSOR {sensor_id}] "
                f"Discovered CH "
                f"{hello['source_address']}"
            )

            # ---------------------------------------------------------------
            # Start JOIN state on Sensor side.
            # ---------------------------------------------------------------

            sensor_neighbor.start_join(
                ip_address=cluster_socket.address[0],
                port=cluster_socket.address[1],
                join_id=sensor_id,
                model_id=1,
            )

        # -------------------------------------------------------------------
        # Step 5: JOIN / Association
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 5: SENSOR JOIN / ASSOCIATION")
        print("-" * 60)

        for sensor_id in range(1, SENSOR_COUNT + 1):
            sensor = sensors[sensor_id]["communication"]
            sensor_neighbor = sensors[sensor_id]["neighbor"]

            print(
                f"[SENSOR {sensor_id}] "
                f"Sending JOIN request"
            )

            sensor.send_message(
                {
                    "packet_type": PacketType.JOIN,
                    "role": 0,
                    "join_id": sensor_id,
                    "model_id": 1,
                },
                destination=cluster_socket.address,
            )

            if not wait_until(
                lambda sensor=sensor:
                    len(sensor.neighbor_buffer) >= 1
            ):
                raise RuntimeError(
                    f"SENSOR {sensor_id} did not receive "
                    "JOIN_ACCEPT."
                )

            join_accept = sensor.pop_neighbor_messages()[0]

            print(
                f"[SENSOR {sensor_id}] "
                f"Received JOIN_ACCEPT "
                f"join_id={join_accept['join_id']}"
            )

            neighbor = sensor_neighbor.get_neighbor(
                cluster_socket.address[0],
                cluster_socket.address[1],
            )

            if (
                neighbor is None
                or neighbor.association_state
                != AssociationState.ASSOCIATED
            ):
                raise RuntimeError(
                    f"SENSOR {sensor_id} was not associated "
                    "with the CH."
                )

            print(
                f"[SENSOR {sensor_id}] "
                f"Association state: "
                f"{neighbor.association_state.value}"
            )

        # -------------------------------------------------------------------
        # Step 6: Show CH neighbor table
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 6: CLUSTER HEAD NEIGHBOR TABLE")
        print("-" * 60)

        neighbors = cluster_neighbor.get_neighbors()

        for neighbor in neighbors:
            print(
                f"[CH] "
                f"{neighbor.ip_address}:{neighbor.port} "
                f"role={neighbor.node_role.value} "
                f"state={neighbor.association_state.value} "
                f"join_id={neighbor.join_id}"
            )

        print(
            f"[CH] Total neighbors: "
            f"{len(neighbors)}"
        )

        # -----------------------------------------------------------------------
        # Step 7: Send MNIST from every Sensor
        # -----------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 7: MULTIPLE SENSORS SEND MNIST SENSOR_DATA")
        print("-" * 60)

        received_by_sensor = {}

        for sensor_id in range(1, SENSOR_COUNT + 1):
            sensor = sensors[sensor_id]["communication"]
            sensor_socket = sensors[sensor_id]["socket"]

            payload = sensor_data[sensor_id]["payload"]

            print(
                f"\n[SENSOR {sensor_id}] "
                f"Starting SENSOR_DATA id=1"
            )

            print(
                f"[SENSOR {sensor_id}] "
                f"Application payload: "
                f"{len(payload)} bytes"
            )

            sensor.send_message(
                {
                    "packet_type": PacketType.SENSOR_DATA,
                    "role": 0,
                    "data_id": 1,
                    "sensor_type": 1,
                    "payload": payload,
                },
                destination=cluster_socket.address,
            )

            # Wait until this Sensor's complete message
            # reaches the CH's Sensor buffer.
            if not wait_until(
                lambda: any(
                    message["source_address"]
                    == sensor_socket.address
                    for message in cluster.sensor_buffer.items
                )
            ):
                raise RuntimeError(
                    f"CH did not receive data from SENSOR {sensor_id}."
                )

            # Find and remove the message belonging to this Sensor.
            messages = cluster.sensor_buffer.items

            received_message = next(
                message
                for message in messages
                if message["source_address"] == sensor_socket.address
            )

            received_by_sensor[sensor_id] = received_message

            print(
                f"[CH] Received complete SENSOR_DATA "
                f"from SENSOR {sensor_id}"
            )

            print(
                f"[CH] Reassembled payload: "
                f"{len(received_message['payload'])} bytes"
            )

        # print(
        #     f"[DEBUG] CH sensor buffer length: "
        #     f"{len(cluster.sensor_buffer)}"
        # )

        # print(
        #     f"[DEBUG] CH sensor buffer items: "
        #     f"{cluster.sensor_buffer.items}"
        # )

        # -------------------------------------------------------------------
        # Step 8: Verify all Sensor data
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 8: VERIFY SENSOR DATA AT CH")
        print("-" * 60)

        if len(received_by_sensor) != SENSOR_COUNT:
            raise RuntimeError(
                "CH did not receive data from all Sensors."
            )

        for sensor_id in range(1, SENSOR_COUNT + 1):
            original_payload = sensor_data[sensor_id]["payload"]

            received_message = received_by_sensor[sensor_id]

            received_payload = received_message["payload"]

            received_images = reconstruct_mnist_images( 
                received_payload, 
                SAMPLES_PER_SENSOR, 
            ) 
            
            save_mnist_images( 
                received_images, 
                sensor_data[sensor_id]["labels"], 
                ( 
                    MNIST_OUTPUT_DIR 
                    / f"sensor_{sensor_id}" 
                    / "received" 
                ), 
            )  

            payload_identical = (
                original_payload
                == received_payload
            )

            print(
                f"[SENSOR {sensor_id}] "
                f"Payload received: "
                f"{len(received_payload)} bytes"
            )

            print(
                f"[SENSOR {sensor_id}] "
                f"Payload identical: "
                f"{payload_identical}"
            )

            if not payload_identical:
                raise RuntimeError(
                    f"Payload verification failed "
                    f"for SENSOR {sensor_id}."
                )

        print(
            "\n[VERIFY] SUCCESS: "
            "All Sensor Nodes transmitted their "
            "MNIST payloads successfully."
        )

        # -------------------------------------------------------------------
        # Step 9: Final neighbor table
        # -------------------------------------------------------------------

        print("\n" + "-" * 60)
        print("STEP 9: FINAL CLUSTER MEMBERSHIP")
        print("-" * 60)

        for neighbor in cluster_neighbor.get_neighbors():
            print(
                f"[CH] "
                f"{neighbor.ip_address}:{neighbor.port} "
                f"state={neighbor.association_state.value} "
                f"join_id={neighbor.join_id}"
            )

        print(
            f"\n[VERIFY] CH has "
            f"{len(cluster_neighbor.get_neighbors())} "
            f"registered Sensor Nodes."
        )

        if len(cluster_neighbor.get_neighbors()) != SENSOR_COUNT:
            raise RuntimeError(
                "CH neighbor count does not match "
                "the number of Sensors."
            )

        print(
            "[VERIFY] SUCCESS: "
            "Multiple Sensors are associated "
            "with one Cluster Head."
        )

    finally:
        for sensor_info in sensors.values():
            sensor_info["communication"].stop()
            sensor_info["socket"].close()

        cluster.stop()
        cluster_socket.close()


if __name__ == "__main__":
    main()