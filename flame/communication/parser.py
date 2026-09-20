# flame/communication/parser.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from message import (
        PacketType,
        LeaveReason,
        deserialize_header,
        deserialize_hello,
        deserialize_join,
        deserialize_join_accept,
        deserialize_leave,
        deserialize_sensor_data,
        deserialize_model_update,
        deserialize_sensor_data_ack,
        deserialize_model_update_ack,
        serialize_hello,
        serialize_join,
        serialize_join_accept,
        serialize_leave,
        serialize_sensor_data,
        serialize_model_update,
        serialize_sensor_data_ack,
        serialize_model_update_ack,
    )
except ImportError:
    from .message import (
        PacketType,
        LeaveReason,
        deserialize_header,
        deserialize_hello,
        deserialize_join,
        deserialize_join_accept,
        deserialize_leave,
        deserialize_sensor_data,
        deserialize_model_update,
        deserialize_sensor_data_ack,
        deserialize_model_update_ack,
        serialize_hello,
        serialize_join,
        serialize_join_accept,
        serialize_leave,
        serialize_sensor_data,
        serialize_model_update,
        serialize_sensor_data_ack,
        serialize_model_update_ack,
    )


EXPECTED_VERSION = 1

ROLE_RESTRICTIONS = {
    PacketType.HELLO: {1},
    PacketType.JOIN: {0},
    PacketType.LEAVE: {0},
    PacketType.SENSOR_DATA: {0},
    PacketType.MODEL_UPDATE: {1},
    PacketType.JOIN_ACCEPT: {1},
    PacketType.SENSOR_DATA_ACK: {1},
    PacketType.MODEL_UPDATE_ACK: {1},
}


@dataclass
class ParsedPacket:
    packet_type: PacketType
    version: int
    role: int
    sequence_number: int
    payload: Dict[str, Any]
    raw: bytes


def parse_datagram(raw_bytes: bytes, expected_version: int = EXPECTED_VERSION) -> ParsedPacket:
    header = deserialize_header(raw_bytes)

    if header["version"] != expected_version:
        raise ValueError(f"Unsupported protocol version: {header['version']}")

    if header["role"] not in (0, 1):
        raise ValueError(f"Invalid role value: {header['role']}")

    packet_type = header["packet_type"]

    allowed_roles = ROLE_RESTRICTIONS.get(packet_type, {0, 1})
    if header["role"] not in allowed_roles:
        raise ValueError(
            f"role {header['role']} is not permitted to send {packet_type.name}"
        )

    if packet_type == PacketType.HELLO:
        payload = deserialize_hello(raw_bytes)
    elif packet_type == PacketType.JOIN:
        payload = deserialize_join(raw_bytes)
    elif packet_type == PacketType.JOIN_ACCEPT:
        payload = deserialize_join_accept(raw_bytes)
    elif packet_type == PacketType.LEAVE:
        payload = deserialize_leave(raw_bytes)
    elif packet_type == PacketType.SENSOR_DATA:
        payload = deserialize_sensor_data(raw_bytes)
    elif packet_type == PacketType.MODEL_UPDATE:
        payload = deserialize_model_update(raw_bytes)
    elif packet_type == PacketType.SENSOR_DATA_ACK:
        payload = deserialize_sensor_data_ack(raw_bytes)
    elif packet_type == PacketType.MODEL_UPDATE_ACK:
        payload = deserialize_model_update_ack(raw_bytes)
    else:
        raise ValueError(f"Unsupported packet type: {packet_type}")

    return ParsedPacket(
        packet_type=packet_type,
        version=header["version"],
        role=header["role"],
        sequence_number=header["sequence_number"],
        payload=payload,
        raw=raw_bytes,
    )


def _get_field(message_obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(message_obj, dict):
        return message_obj.get(key, default)
    return getattr(message_obj, key, default)


def serialize_message(message_obj: Any) -> bytes:
    packet_type = _get_field(message_obj, "packet_type")
    version = _get_field(message_obj, "version", EXPECTED_VERSION)
    role = _get_field(message_obj, "role", 0)
    sequence_number = _get_field(message_obj, "sequence_number", 0)

    if isinstance(packet_type, str):
        packet_type = PacketType[packet_type]

    if packet_type == PacketType.HELLO:
        return serialize_hello(
            version=version,
            role=role,
            sequence_number=sequence_number,
            model_id=_get_field(message_obj, "model_id", 0),
        )

    if packet_type == PacketType.JOIN:
        return serialize_join(
            version=version,
            role=role,
            sequence_number=sequence_number,
            model_id=_get_field(message_obj, "model_id", 0),
            join_id=_get_field(message_obj, "join_id", 0),
        )

    if packet_type == PacketType.JOIN_ACCEPT:
        return serialize_join_accept(
            version=version,
            role=role,
            sequence_number=sequence_number,
            join_id=_get_field(message_obj, "join_id", 0),
            timeout=_get_field(message_obj, "timeout", 0),
            rejected=_get_field(message_obj, "rejected", 0),
        )

    if packet_type == PacketType.LEAVE:
        reason = _get_field(message_obj, "reason", LeaveReason.NORMAL_DISCONNECTION)
        return serialize_leave(
            version=version,
            role=role,
            sequence_number=sequence_number,
            reason=reason,
        )

    if packet_type == PacketType.SENSOR_DATA:
        return serialize_sensor_data(
            version=version,
            role=role,
            sequence_number=sequence_number,
            data_id=_get_field(message_obj, "data_id", 0),
            sensor_type=_get_field(message_obj, "sensor_type", 0),
            block_number=_get_field(message_obj, "block_number", 0),
            last_block_number=_get_field(message_obj, "last_block_number", 0),
            payload=_get_field(message_obj, "payload", b""),
        )

    if packet_type == PacketType.MODEL_UPDATE:
        return serialize_model_update(
            version=version,
            role=role,
            sequence_number=sequence_number,
            update_id=_get_field(message_obj, "update_id", 0),
            model_id=_get_field(message_obj, "model_id", 0),
            block_number=_get_field(message_obj, "block_number", 0),
            last_block_number=_get_field(message_obj, "last_block_number", 0),
            payload=_get_field(message_obj, "payload", b""),
        )

    if packet_type == PacketType.SENSOR_DATA_ACK:
        return serialize_sensor_data_ack(
            version=version,
            sequence_number=sequence_number,
            data_id=_get_field(message_obj, "data_id", 0),
            payload=_get_field(message_obj, "payload", b""),
        )

    if packet_type == PacketType.MODEL_UPDATE_ACK:
        return serialize_model_update_ack(
            version=version,
            sequence_number=sequence_number,
            update_id=_get_field(message_obj, "update_id", 0),
            payload=_get_field(message_obj, "payload", b""),
        )

    raise ValueError(f"Unsupported packet type for serialization: {packet_type}")