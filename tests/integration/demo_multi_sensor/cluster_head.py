"""FLAME Cluster Head HELLO and SENSOR_DATA test."""

from __future__ import annotations

import time

from flame.communication import (
    CommunicationModule,
    PacketType,
    SocketInterface,
)
from flame.neighbor import NeighborModule

CH_HOST = "127.0.0.1"
CH_PORT = 50000
HELLO_PORT = 50010
HELLO_INTERVAL = 5.0


def main() -> None:
    print("\n" + "=" * 70)
    print("FLAME CLUSTER HEAD - HELLO + SENSOR DATA TEST")
    print("=" * 70)

    socket = SocketInterface(
        host=CH_HOST,
        port=CH_PORT,
        timeout=0.02,
        broadcast=True,
    )

    neighbor = NeighborModule()

    cluster = CommunicationModule(
        socket,
        role=1,
        neighbor_module=neighbor,
    )

    print(f"\n[CH] Listening on {socket.address}")
    print(
        f"[CH] Broadcasting HELLO to "
        f"255.255.255.255:{HELLO_PORT}"
    )
    print(f"[CH] HELLO interval: {HELLO_INTERVAL} seconds")
    print()

    last_hello = time.monotonic()

    try:
        time.sleep(2)

        while True:
            # --------------------------------------------------
            # Periodic HELLO broadcast
            # --------------------------------------------------
            now = time.monotonic()

            if now - last_hello >= HELLO_INTERVAL:
                print("[CH] Broadcasting HELLO...")

                cluster.send_message(
                    {
                        "packet_type": PacketType.HELLO,
                        "role": 1,
                        "model_id": 1,
                    },
                    destination=(
                        "255.255.255.255",
                        HELLO_PORT,
                    ),
                )

                last_hello = now

            # --------------------------------------------------
            # Process received SENSOR_DATA
            # --------------------------------------------------
            messages = cluster.pop_sensor_messages()

            for message in messages:
                source = message.get("source_address")

                print(
                    f"\n[CH] SENSOR_DATA received from {source}"
                )

                print(
                    f"[CH] data_id={message.get('data_id')} "
                    f"sensor_type={message.get('sensor_type')}"
                )

                print(
                    f"[CH] block="
                    f"{message.get('block_number')}/"
                    f"{message.get('last_block_number')}"
                )

                print(
                    f"[CH] payload="
                    f"{message.get('payload')}"
                )

                print()

            # --------------------------------------------------
            # Check neighbor expiration
            # --------------------------------------------------
            neighbor.check_expired_neighbors()

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\n[CH] Stopping...")

    finally:
        cluster.stop()
        socket.close()


if __name__ == "__main__":
    main()
