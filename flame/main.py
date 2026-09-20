from __future__ import annotations

import argparse
import sys
import time
from typing import Tuple

try:
    from communication.communication_module import CommunicationModule
    from communication.message import PacketType
    from communication.socket_interface import SocketInterface
except ImportError:
    from flame.communication.communication_module import CommunicationModule
    from flame.communication.message import PacketType
    from flame.communication.socket_interface import SocketInterface


def parse_address(value: str) -> Tuple[str, int]:
    host, port_text = value.rsplit(":", 1)
    return host, int(port_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="FLAME communication demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--peer", required=True, help="peer address as host:port")
    parser.add_argument("--role", type=int, choices=(0, 1), required=True)
    parser.add_argument("--kind", choices=("hello", "sensor"), default="hello")
    parser.add_argument("--message", default="hello from FLAME")
    args = parser.parse_args()

    peer_address = parse_address(args.peer)

    socket_interface = SocketInterface(host=args.host, port=args.port)
    comms = CommunicationModule(socket_interface=socket_interface, auto_start=True)

    print("Local address:", socket_interface.address)
    print("Peer address:", peer_address)

    if args.kind == "hello":
        payload = {
            "packet_type": PacketType.HELLO,
            "role": args.role,
            "sequence_number": 1,
            "model_id": 1,
        }
    else:
        payload = {
            "packet_type": PacketType.SENSOR_DATA,
            "role": args.role,
            "sequence_number": 1,
            "data_id": 1,
            "sensor_type": 1,
            "block_number": 0,
            "last_block_number": 0,
            "payload": args.message.encode("utf-8"),
        }

    comms.send_message(payload, destination=peer_address)

    print("Sent message. Waiting for a reply...")
    deadline = time.time() + 10
    try:
        while time.time() < deadline:
            comms.ingest_socket_once()

            neighbor = comms.pop_neighbor_messages()
            if neighbor:
                print("Received neighbor packet:", neighbor[0])
                break

            sensor = comms.pop_sensor_messages()
            if sensor:
                print("Received sensor packet:", sensor[0])
                break

            ack = comms.pop_ack_messages()
            if ack:
                print("Received ack packet:", ack[0])
                break

            time.sleep(0.05)
        else:
            print("No reply received before timeout.")
    finally:
        comms.stop()
        socket_interface.close()


if __name__ == "__main__":
    main()