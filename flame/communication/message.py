from dataclasses import dataclass
from enum import IntEnum


class PacketType(IntEnum):
    HELLO = 0x00
    LEAVE = 0x01
    SENSOR_DATA = 0x02
    SENSOR_DATA_ACK = 0x03
    MODEL_UPDATE = 0x04
    MODEL_UPDATE_ACK = 0x05
    JOIN = 0x06
    JOIN_ACCEPT = 0x07

@dataclass
class Message:
    version: int
    packet_type: PacketType
    role: int
    sequence_number: int
    payload: bytes

class LeaveReason(IntEnum):
    NORMAL_DISCONNECTION = 0x00
    LOW_BATTERY = 0x01
    MOBILITY_OUT_OF_RANGE = 0x02
    TASK_COMPLETED = 0x03
    SWITCHING_CLUSTER_HEAD = 0x04
    SENSOR_FAULT = 0x05
    DEVICE_SHUTDOWN = 0x06
    COMMUNICATION_FAILURE = 0x07

def serialize_header(
    version: int,
    packet_type: PacketType,
    role: int,
    sequence_number: int
) -> bytes:

    if not 0 <= version <= 0x0F:
        raise ValueError("Version must fit within 4 bits.")

    if not 0 <= role <= 0x01:
        raise ValueError("Role must be 0 or 1.")

    if not 0 <= sequence_number <= 0xFFFF:
        raise ValueError("Sequence number must fit within 16 bits.")

    header = (
        ((version & 0x0F) << 28)
        | ((int(packet_type) & 0x0F) << 24)
        | ((role & 0x01) << 23)
        | (sequence_number & 0xFFFF)
    )

    return header.to_bytes(4, byteorder="big")


def deserialize_header(data: bytes):
    if len(data) < 4:
        raise ValueError("Datagram is too short for a common header.")

    header = int.from_bytes(data[:4], byteorder="big")

    version = (header >> 28) & 0x0F
    packet_type_value = (header >> 24) & 0x0F
    role = (header >> 23) & 0x01
    sequence_number = header & 0xFFFF

    try:
        packet_type = PacketType(packet_type_value)
    except ValueError:
        raise ValueError(
            f"Unsupported packet type: 0x{packet_type_value:02X}"
        )

    return {
        "version": version,
        "packet_type": packet_type,
        "role": role,
        "sequence_number": sequence_number,
    }

def serialize_hello(
    version: int,
    role: int,
    sequence_number: int,
    model_id: int
) -> bytes:

    if not 0 <= model_id <= 0xFF:
        raise ValueError("Model ID must fit within 8 bits.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.HELLO,
        role=role,
        sequence_number=sequence_number,
    )

    return header + model_id.to_bytes(1, byteorder="big")

def serialize_join(
    version: int,
    role: int,
    sequence_number: int,
    model_id: int,
    join_id: int
) -> bytes:

    if not 0 <= model_id <= 0xFF:
        raise ValueError("Model ID must fit within 8 bits.")

    if not 0 <= join_id <= 0xFFFF:
        raise ValueError("Join ID must fit within 16 bits.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.JOIN,
        role=role,
        sequence_number=sequence_number,
    )

    payload = (
        model_id.to_bytes(1, byteorder="big")
        + join_id.to_bytes(2, byteorder="big")
    )

    return header + payload

def serialize_join_accept(
    version: int,
    role: int,
    sequence_number: int,
    join_id: int,
    timeout: int,
    rejected: int
) -> bytes:

    if not 0 <= join_id <= 0xFFFF:
        raise ValueError("Join ID must fit within 16 bits.")

    if not 0 <= timeout <= 0x7F:
        raise ValueError("Timeout must fit within 7 bits.")

    if rejected not in (0, 1):
        raise ValueError("Rejected must be 0 or 1.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.JOIN_ACCEPT,
        role=role,
        sequence_number=sequence_number,
    )

    join_id_bytes = join_id.to_bytes(2, byteorder="big")

    # 7-bit timeout + 1-bit rejected flag
    status_byte = (timeout << 1) | rejected

    return header + join_id_bytes + status_byte.to_bytes(1, byteorder="big")

def serialize_leave(
    version: int,
    role: int,
    sequence_number: int,
    reason: LeaveReason
) -> bytes:

    header = serialize_header(
        version=version,
        packet_type=PacketType.LEAVE,
        role=role,
        sequence_number=sequence_number,
    )

    return header + int(reason).to_bytes(1, byteorder="big")

def serialize_sensor_data(
    version: int,
    role: int,
    sequence_number: int,
    data_id: int,
    sensor_type: int,
    block_number: int,
    last_block_number: int,
    payload: bytes
) -> bytes:

    if not 0 <= data_id <= 0xFF:
        raise ValueError("Data ID must fit within 8 bits.")

    if not 0 <= sensor_type <= 0xFF:
        raise ValueError("Sensor Type must fit within 8 bits.")

    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block Number must fit within 16 bits.")

    if not 0 <= last_block_number <= 0xFFFF:
        raise ValueError("Last Block Number must fit within 16 bits.")

    if len(payload) > 1024:
        raise ValueError("Sensor payload cannot exceed 1024 bytes.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.SENSOR_DATA,
        role=role,
        sequence_number=sequence_number,
    )

    packet_header = (
        data_id.to_bytes(1, byteorder="big")
        + sensor_type.to_bytes(1, byteorder="big")
        + block_number.to_bytes(2, byteorder="big")
        + last_block_number.to_bytes(2, byteorder="big")
    )

    return header + packet_header + payload

def serialize_model_update(
    version: int,
    role: int,
    sequence_number: int,
    update_id: int,
    model_id: int,
    block_number: int,
    last_block_number: int,
    payload: bytes
) -> bytes:

    if not 0 <= update_id <= 0xFF:
        raise ValueError("Update ID must fit within 8 bits.")

    if not 0 <= model_id <= 0xFF:
        raise ValueError("Model ID must fit within 8 bits.")

    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block Number must fit within 16 bits.")

    if not 0 <= last_block_number <= 0xFFFF:
        raise ValueError("Last Block Number must fit within 16 bits.")

    if len(payload) > 1024:
        raise ValueError("Model update payload cannot exceed 1024 bytes.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.MODEL_UPDATE,
        role=role,
        sequence_number=sequence_number,
    )

    packet_header = (
        update_id.to_bytes(1, byteorder="big")
        + model_id.to_bytes(1, byteorder="big")
        + block_number.to_bytes(2, byteorder="big")
        + last_block_number.to_bytes(2, byteorder="big")
    )

    return header + packet_header + payload

def serialize_sensor_data_ack(
    version: int,
    sequence_number: int,
    data_id: int,
    payload: bytes = b""
) -> bytes:

    if not 0 <= data_id <= 0xFF:
        raise ValueError("Data ID must fit within 8 bits.")

    if len(payload) > 1024:
        raise ValueError("ACK payload cannot exceed 1024 bytes.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.SENSOR_DATA_ACK,
        role=1,
        sequence_number=sequence_number,
    )

    # Second 32-bit row:
    # Data ID (1 byte) + 3 reserved bytes
    packet_header = (
        data_id.to_bytes(1, byteorder="big")
        + b"\x00\x00\x00"
    )

    return header + packet_header + payload

def serialize_model_update_ack(
    version: int,
    sequence_number: int,
    update_id: int,
    payload: bytes = b""
) -> bytes:

    if not 0 <= update_id <= 0xFF:
        raise ValueError("Update ID must fit within 8 bits.")

    if len(payload) > 1024:
        raise ValueError("ACK payload cannot exceed 1024 bytes.")

    header = serialize_header(
        version=version,
        packet_type=PacketType.MODEL_UPDATE_ACK,
        role=1,
        sequence_number=sequence_number,
    )

    packet_header = (
        update_id.to_bytes(1, byteorder="big")
        + b"\x00\x00\x00"
    )

    return header + packet_header + payload

def fragment_payload(payload: bytes, max_payload_size: int = 1024):
    if max_payload_size <= 0:
        raise ValueError("Maximum payload size must be greater than 0.")

    fragments = [
        payload[i:i + max_payload_size]
        for i in range(0, len(payload), max_payload_size)
    ]

    last_block_number = len(fragments) - 1

    return [
        (block_number, last_block_number, fragment)
        for block_number, fragment in enumerate(fragments)
    ]











































































# FUNCTION: Raw 5-byte stream → deserialize_hello() → Usable HELLO data structure
def deserialize_hello(data: bytes):
    if len(data) != 5:
        raise ValueError("HELLO packet must be exactly 5 bytes.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.HELLO:
        raise ValueError("Packet is not a HELLO packet.")

    model_id = data[4]

    return {
        **header,
        "model_id": model_id,
    }

def deserialize_join(data: bytes):
    if len(data) != 7:
        raise ValueError("JOIN packet must be exactly 7 bytes.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.JOIN:
        raise ValueError("Packet is not a JOIN packet.")

    model_id = data[4]
    join_id = int.from_bytes(data[5:7], byteorder="big")

    return {
        **header,
        "model_id": model_id,
        "join_id": join_id,
    }

def deserialize_join_accept(data: bytes):
    if len(data) != 7:
        raise ValueError("JOIN_ACCEPT packet must be exactly 7 bytes.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.JOIN_ACCEPT:
        raise ValueError("Packet is not a JOIN_ACCEPT packet.")

    join_id = int.from_bytes(data[4:6], byteorder="big")

    status_byte = data[6]

    timeout = (status_byte >> 1) & 0x7F
    rejected = status_byte & 0x01

    return {
        **header,
        "join_id": join_id,
        "timeout": timeout,
        "rejected": rejected,
    }

def deserialize_leave(data: bytes):
    if len(data) != 5:
        raise ValueError("LEAVE packet must be exactly 5 bytes.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.LEAVE:
        raise ValueError("Packet is not a LEAVE packet.")

    reason_value = data[4]

    try:
        reason = LeaveReason(reason_value)
    except ValueError:
        raise ValueError(
            f"Unsupported leave reason: 0x{reason_value:02X}"
        )

    return {
        **header,
        "reason": reason,
    }

def deserialize_sensor_data(data: bytes):
    if len(data) < 10:
        raise ValueError(
            "SENSOR_DATA packet must be at least 10 bytes."
        )

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.SENSOR_DATA:
        raise ValueError("Packet is not a SENSOR_DATA packet.")

    data_id = data[4]
    sensor_type = data[5]
    block_number = int.from_bytes(data[6:8], byteorder="big")
    last_block_number = int.from_bytes(data[8:10], byteorder="big")
    payload = data[10:]

    return {
        **header,
        "data_id": data_id,
        "sensor_type": sensor_type,
        "block_number": block_number,
        "last_block_number": last_block_number,
        "payload": payload,
    }

def deserialize_model_update(data: bytes):
    if len(data) < 10:
        raise ValueError(
            "MODEL_UPDATE packet must be at least 10 bytes."
        )

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.MODEL_UPDATE:
        raise ValueError("Packet is not a MODEL_UPDATE packet.")

    update_id = data[4]
    model_id = data[5]
    block_number = int.from_bytes(data[6:8], byteorder="big")
    last_block_number = int.from_bytes(data[8:10], byteorder="big")
    payload = data[10:]

    return {
        **header,
        "update_id": update_id,
        "model_id": model_id,
        "block_number": block_number,
        "last_block_number": last_block_number,
        "payload": payload,
    }

def deserialize_sensor_data_ack(data: bytes):
    if len(data) < 8:
        raise ValueError(
            "SENSOR_DATA_ACK packet must be at least 8 bytes."
        )

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.SENSOR_DATA_ACK:
        raise ValueError(
            "Packet is not a SENSOR_DATA_ACK packet."
        )

    data_id = data[4]
    payload = data[8:]

    return {
        **header,
        "data_id": data_id,
        "payload": payload,
    }

def deserialize_model_update_ack(data: bytes):
    if len(data) < 8:
        raise ValueError(
            "MODEL_UPDATE_ACK packet must be at least 8 bytes."
        )

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.MODEL_UPDATE_ACK:
        raise ValueError(
            "Packet is not a MODEL_UPDATE_ACK packet."
        )

    update_id = data[4]
    payload = data[8:]

    return {
        **header,
        "update_id": update_id,
        "payload": payload,
    }

def reassemble_fragments(fragments):
    if not fragments:
        raise ValueError("No fragments provided.")

    fragments = sorted(fragments, key=lambda x: x["block_number"])

    last_block_number = fragments[0]["last_block_number"]

    for i, fragment in enumerate(fragments):
        if fragment["block_number"] != i:
            raise ValueError("Missing or out-of-order fragment.")

        if fragment["last_block_number"] != last_block_number:
            raise ValueError("Fragments have inconsistent last block numbers.")

    if len(fragments) != last_block_number + 1:
        raise ValueError("Not all fragments have been received.")

    return b"".join(
        fragment["payload"]
        for fragment in fragments
    )