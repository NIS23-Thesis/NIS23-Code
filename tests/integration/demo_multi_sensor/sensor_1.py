"""FLAME Sensor 1 HELLO and JOIN test."""

from __future__ import annotations

import time

from flame.communication import (
    CommunicationModule,
    PacketType,
    SocketInterface,
)
from flame.neighbor import (
    AssociationState,
    NeighborModule,
    NodeRole,
)


SENSOR_ID = 1
SENSOR_HOST = "0.0.0.0"
SENSOR_PORT = 50010


def main() -> None:
    print("\n" + "=" * 70)
    print("FLAME SENSOR 1 - HELLO + JOIN TEST")
    print("=" * 70)

    socket = SocketInterface(
        host=SENSOR_HOST,
        port=SENSOR_PORT,
        timeout=0.02,
    )

    neighbor = NeighborModule()

    sensor = CommunicationModule(
        socket,
        role=0,
        neighbor_module=neighbor,
    )

    print(f"\n[SENSOR {SENSOR_ID}] Listening on {socket.address}")
    print("[SENSOR 1] Waiting for periodic HELLO...")
    print()

    last_status = time.monotonic()
    join_sent = False
    next_join_id = 0

    try:
        while True:
            messages = sensor.pop_neighbor_messages()

            for message in messages:
                packet_type = message.get("packet_type")

                if packet_type == PacketType.HELLO:
                    source = message.get("source_address")

                    print(
                        f"\n[SENSOR {SENSOR_ID}] "
                        f"HELLO received from {source}"
                    )

                    discovered = neighbor.get_neighbor(
                        source[0],
                        source[1],
                    )

                    print(
                        f"[SENSOR {SENSOR_ID}] "
                        f"CH discovered: "
                        f"{discovered.ip_address}:{discovered.port} "
                        f"state={discovered.association_state.value}"
                    )

                    if (
                        discovered.node_role == NodeRole.CLUSTER_HEAD
                        and discovered.association_state
                        == AssociationState.DISCOVERED
                        and not join_sent
                    ):
                        join_id = next_join_id
                        next_join_id = (next_join_id + 1) & 0xFFFF

                        print(
                            f"[SENSOR {SENSOR_ID}] "
                            f"Sending JOIN to "
                            f"{discovered.ip_address}:{discovered.port} "
                            f"join_id={join_id}"
                        )

                        neighbor.start_join(
                            ip_address=discovered.ip_address,
                            port=discovered.port,
                            join_id=join_id,
                            model_id=discovered.model_id,
                        )

                        sensor.send_message(
                            {
                                "packet_type": PacketType.JOIN,
                                "role": 0,
                                "model_id": discovered.model_id,
                                "join_id": join_id,
                            },
                            destination=(
                                discovered.ip_address,
                                discovered.port,
                            ),
                        )

                        join_sent = True

                elif packet_type == PacketType.JOIN_ACCEPT:
                    source = message.get("source_address")

                    print(
                        f"\n[SENSOR {SENSOR_ID}] "
                        f"JOIN_ACCEPT received from {source}"
                    )

                    print(
                        f"[SENSOR {SENSOR_ID}] "
                        f"join_id={message.get('join_id')} "
                        f"rejected={message.get('rejected')} "
                        f"timeout={message.get('timeout')}"
                    )

                    if message.get("rejected") == 0:
                        print(
                            f"[SENSOR {SENSOR_ID}] "
                            f"JOIN accepted. Sending SENSOR_DATA..."
                        )

                        sensor.send_message(
                            {
                                "packet_type": PacketType.SENSOR_DATA,
                                "role": 0,
                                "data_id": 1,
                                "sensor_type": 1,
                                "block_number": 0,
                                "last_block_number": 0,
                                "payload": b"HELLO_FROM_SENSOR_1",
                            },
                            destination=source,
                        )


            neighbor.check_expired_neighbors()

            if time.monotonic() - last_status >= 1:
                neighbors = neighbor.get_neighbors()

                if neighbors:
                    for entry in neighbors:
                        print(
                            f"[SENSOR {SENSOR_ID}] "
                            f"CH status: "
                            f"{entry.ip_address}:{entry.port} "
                            f"state={entry.association_state.value}"
                        )
                else:
                    print(
                        f"[SENSOR {SENSOR_ID}] "
                        f"No neighbors."
                    )

                last_status = time.monotonic()

            time.sleep(0.02)

    except KeyboardInterrupt:
        print(f"\n[SENSOR {SENSOR_ID}] Stopping...")

    finally:
        sensor.stop()
        socket.close()


if __name__ == "__main__":
    main()