'''
message.py handles all the packet types and their 
serialization/deserialization for the FLAME protocol.
It defines the common packet header, specific packet structures, and provides
functions to encode and decode each packet type. The module also includes utilities 
for fragmenting payloads and reassembling them from received fragments.
'''

from dataclasses import dataclass
from enum import IntEnum


MAX_FRAGMENT_PAYLOAD = 1024
MAX_BLOCK_NUMBER = 0xFFFF


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
    '''List the reasons a device may leave.'''
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
    '''Encode the common packet header.'''

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
    '''Decode and validate the common packet header.'''
    if len(data) < 4:
        raise ValueError("Datagram is too short for a common header.")

    header = int.from_bytes(data[:4], byteorder="big")

    version = (header >> 28) & 0x0F
    packet_type_value = (header >> 24) & 0x0F
    role = (header >> 23) & 0x01
    reserved = (header >> 16) & 0x7F
    sequence_number = header & 0xFFFF

    if reserved != 0:
        raise ValueError("Reserved common-header bits must be zero.")

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
    '''Encode a HELLO packet.'''

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
    '''Encode a JOIN packet.'''

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
    '''Encode a JOIN_ACCEPT packet.'''

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
    '''Encode a LEAVE packet.'''

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
    '''Encode one sensor data fragment.'''

    if not 0 <= data_id <= 0xFF:
        raise ValueError("Data ID must fit within 8 bits.")

    if not 0 <= sensor_type <= 0xFF:
        raise ValueError("Sensor Type must fit within 8 bits.")

    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block Number must fit within 16 bits.")

    if not 0 <= last_block_number <= 0xFFFF:
        raise ValueError("Last Block Number must fit within 16 bits.")

    if block_number > last_block_number:
        raise ValueError("Block Number cannot exceed Last Block Number.")

    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Sensor payload must be bytes-like.")

    if len(payload) > MAX_FRAGMENT_PAYLOAD:
        raise ValueError(f"Sensor payload cannot exceed {MAX_FRAGMENT_PAYLOAD} bytes.")

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
    '''Encode one model update fragment.'''

    if not 0 <= update_id <= 0xFF:
        raise ValueError("Update ID must fit within 8 bits.")

    if not 0 <= model_id <= 0xFF:
        raise ValueError("Model ID must fit within 8 bits.")

    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block Number must fit within 16 bits.")

    if not 0 <= last_block_number <= 0xFFFF:
        raise ValueError("Last Block Number must fit within 16 bits.")

    if block_number > last_block_number:
        raise ValueError("Block Number cannot exceed Last Block Number.")

    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Model update payload must be bytes-like.")

    if len(payload) > MAX_FRAGMENT_PAYLOAD:
        raise ValueError(f"Model update payload cannot exceed {MAX_FRAGMENT_PAYLOAD} bytes.")

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
    window_start: int,
    window_end: int,
    received_blocks,
) -> bytes:
    '''Encode a sensor data acknowledgement.'''

    if not 0 <= data_id <= 0xFF:
        raise ValueError("Data ID must fit within 8 bits.")

    ack_body = _serialize_ack_body(
        message_id=data_id,
        window_start=window_start,
        window_end=window_end,
        received_blocks=received_blocks,
    )

    header = serialize_header(
        version=version,
        packet_type=PacketType.SENSOR_DATA_ACK,
        role=1,
        sequence_number=sequence_number,
    )

    return header + ack_body

def serialize_model_update_ack(
    version: int,
    sequence_number: int,
    update_id: int,
    window_start: int,
    window_end: int,
    received_blocks,
) -> bytes:
    '''Encode a model update acknowledgement.'''

    if not 0 <= update_id <= 0xFF:
        raise ValueError("Update ID must fit within 8 bits.")

    ack_body = _serialize_ack_body(
        message_id=update_id,
        window_start=window_start,
        window_end=window_end,
        received_blocks=received_blocks,
    )

    header = serialize_header(
        version=version,
        packet_type=PacketType.MODEL_UPDATE_ACK,
        role=1,
        sequence_number=sequence_number,
    )

    return header + ack_body


def _serialize_ack_body(message_id, window_start, window_end, received_blocks):
    '''Encode the shared acknowledgement fields.'''
    if not 0 <= message_id <= 0xFF:
        raise ValueError("Message ID must fit within 8 bits.")
    if not 0 <= window_start <= MAX_BLOCK_NUMBER:
        raise ValueError("ACK window start must fit within 16 bits.")
    if not 0 <= window_end <= MAX_BLOCK_NUMBER:
        raise ValueError("ACK window end must fit within 16 bits.")
    if window_start > window_end:
        raise ValueError("ACK window start cannot exceed window end.")

    blocks = list(received_blocks)
    if blocks != sorted(set(blocks)):
        raise ValueError("ACK received blocks must be unique and ascending.")
    if len(blocks) > 0xFF:
        raise ValueError("ACK cannot contain more than 255 block numbers.")
    if any(not isinstance(block, int) or block < window_start or block > window_end for block in blocks):
        raise ValueError("ACK block numbers must be inside the acknowledged window.")

    return (
        message_id.to_bytes(1, byteorder="big")
        + window_start.to_bytes(2, byteorder="big")
        + window_end.to_bytes(2, byteorder="big")
        + len(blocks).to_bytes(1, byteorder="big")
        + b"".join(block.to_bytes(2, byteorder="big") for block in blocks)
    )

def fragment_payload(payload: bytes, max_payload_size: int = MAX_FRAGMENT_PAYLOAD):
    '''Split a payload into numbered fragments.'''
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Payload must be bytes-like.")
    if max_payload_size <= 0:
        raise ValueError("Maximum payload size must be greater than 0.")
    if max_payload_size > MAX_FRAGMENT_PAYLOAD:
        raise ValueError(f"Maximum payload size cannot exceed {MAX_FRAGMENT_PAYLOAD} bytes.")

    payload = bytes(payload)
    if not payload:
        return [(0, 0, b"")]

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
    '''Decode a HELLO packet.'''
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
    '''Decode a JOIN packet.'''
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
    '''Decode a JOIN_ACCEPT packet.'''
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
    '''Decode a LEAVE packet.'''
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
    '''Decode one sensor data fragment.'''
    if len(data) < 10:
        raise ValueError(
            "SENSOR_DATA packet must be at least 10 bytes."
        )
    if len(data) > 10 + MAX_FRAGMENT_PAYLOAD:
        raise ValueError("SENSOR_DATA payload exceeds the maximum fragment size.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.SENSOR_DATA:
        raise ValueError("Packet is not a SENSOR_DATA packet.")

    data_id = data[4]
    sensor_type = data[5]
    block_number = int.from_bytes(data[6:8], byteorder="big")
    last_block_number = int.from_bytes(data[8:10], byteorder="big")
    payload = data[10:]

    if block_number > last_block_number:
        raise ValueError("Block Number cannot exceed Last Block Number.")

    return {
        **header,
        "data_id": data_id,
        "sensor_type": sensor_type,
        "block_number": block_number,
        "last_block_number": last_block_number,
        "payload": payload,
    }

def deserialize_model_update(data: bytes):
    '''Decode one model update fragment.'''
    if len(data) < 10:
        raise ValueError(
            "MODEL_UPDATE packet must be at least 10 bytes."
        )
    if len(data) > 10 + MAX_FRAGMENT_PAYLOAD:
        raise ValueError("MODEL_UPDATE payload exceeds the maximum fragment size.")

    header = deserialize_header(data)

    if header["packet_type"] != PacketType.MODEL_UPDATE:
        raise ValueError("Packet is not a MODEL_UPDATE packet.")

    update_id = data[4]
    model_id = data[5]
    block_number = int.from_bytes(data[6:8], byteorder="big")
    last_block_number = int.from_bytes(data[8:10], byteorder="big")
    payload = data[10:]

    if block_number > last_block_number:
        raise ValueError("Block Number cannot exceed Last Block Number.")

    return {
        **header,
        "update_id": update_id,
        "model_id": model_id,
        "block_number": block_number,
        "last_block_number": last_block_number,
        "payload": payload,
    }

def deserialize_sensor_data_ack(data: bytes):
    '''Decode a sensor data acknowledgement.'''
    header = deserialize_header(data)

    if header["packet_type"] != PacketType.SENSOR_DATA_ACK:
        raise ValueError(
            "Packet is not a SENSOR_DATA_ACK packet."
        )

    ack = _deserialize_ack_body(data, "SENSOR_DATA_ACK")

    return {
        **header,
        "data_id": ack["message_id"],
        "window_start": ack["window_start"],
        "window_end": ack["window_end"],
        "received_blocks": ack["received_blocks"],
    }

def deserialize_model_update_ack(data: bytes):
    '''Decode a model update acknowledgement.'''
    header = deserialize_header(data)

    if header["packet_type"] != PacketType.MODEL_UPDATE_ACK:
        raise ValueError(
            "Packet is not a MODEL_UPDATE_ACK packet."
        )

    ack = _deserialize_ack_body(data, "MODEL_UPDATE_ACK")

    return {
        **header,
        "update_id": ack["message_id"],
        "window_start": ack["window_start"],
        "window_end": ack["window_end"],
        "received_blocks": ack["received_blocks"],
    }


def _deserialize_ack_body(data: bytes, packet_name: str):
    '''Decode the shared acknowledgement fields.'''
    if len(data) < 10:
        raise ValueError(f"{packet_name} packet must be at least 10 bytes.")

    message_id = data[4]
    window_start = int.from_bytes(data[5:7], byteorder="big")
    window_end = int.from_bytes(data[7:9], byteorder="big")
    received_count = data[9]
    expected_length = 10 + (received_count * 2)

    if len(data) != expected_length:
        raise ValueError(
            f"{packet_name} length does not match its received-block count."
        )
    if window_start > window_end:
        raise ValueError(f"{packet_name} window start cannot exceed window end.")

    received_blocks = [
        int.from_bytes(data[offset:offset + 2], byteorder="big")
        for offset in range(10, expected_length, 2)
    ]
    if received_blocks != sorted(set(received_blocks)):
        raise ValueError(f"{packet_name} block numbers must be unique and ascending.")
    if any(block < window_start or block > window_end for block in received_blocks):
        raise ValueError(f"{packet_name} contains a block outside its window.")

    return {
        "message_id": message_id,
        "window_start": window_start,
        "window_end": window_end,
        "received_blocks": received_blocks,
    }

def reassemble_fragments(fragments):
    '''Check and join all fragments in order.'''
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
