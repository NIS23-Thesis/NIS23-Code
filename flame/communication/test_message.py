from message import (
    PacketType,
    LeaveReason,
    serialize_header,
    deserialize_header,
    serialize_hello,
    deserialize_hello,
    serialize_join,
    deserialize_join,
    serialize_join_accept,
    deserialize_join_accept,
    serialize_leave,
    deserialize_leave,
    serialize_sensor_data,
    deserialize_sensor_data,
    serialize_model_update,
    deserialize_model_update,
    serialize_sensor_data_ack,
    deserialize_sensor_data_ack,
    serialize_model_update_ack,
    deserialize_model_update_ack,
    fragment_payload,
    reassemble_fragments,
)


# ============================================================
# TEST 1: Common Header
# ============================================================

print("=== Test 1: Common Header ===")

header = serialize_header(
    version=1,
    packet_type=PacketType.SENSOR_DATA,
    role=0,
    sequence_number=42,
)

print("Serialized:", header)
print("Length:", len(header))
print("Decoded:", deserialize_header(header))


# ============================================================
# TEST 2: HELLO
# ============================================================

print("\n=== Test 2: HELLO ===")

hello = serialize_hello(
    version=1,
    role=1,
    sequence_number=10,
    model_id=5,
)

print("Serialized HELLO:", hello)
print("Length:", len(hello))
print("Decoded:", deserialize_hello(hello))


# ============================================================
# TEST 3: JOIN
# ============================================================

print("\n=== Test 3: JOIN ===")

join = serialize_join(
    version=1,
    role=0,
    sequence_number=20,
    model_id=5,
    join_id=1000,
)

print("Serialized JOIN:", join)
print("Length:", len(join))
print("Decoded:", deserialize_join(join))


# ============================================================
# TEST 4: JOIN_ACCEPT
# ============================================================

print("\n=== Test 4: JOIN_ACCEPT ===")

join_accept = serialize_join_accept(
    version=1,
    role=1,
    sequence_number=21,
    join_id=1000,
    timeout=30,
    rejected=0,
)

print("Serialized JOIN_ACCEPT:", join_accept)
print("Length:", len(join_accept))
print("Decoded:", deserialize_join_accept(join_accept))


# ============================================================
# TEST 5: LEAVE
# ============================================================

print("\n=== Test 5: LEAVE ===")

leave = serialize_leave(
    version=1,
    role=0,
    sequence_number=30,
    reason=LeaveReason.TASK_COMPLETED,
)

print("Serialized LEAVE:", leave)
print("Length:", len(leave))
print("Decoded:", deserialize_leave(leave))


# ============================================================
# TEST 6: SENSOR_DATA
# ============================================================

print("\n=== Test 6: SENSOR_DATA ===")

sensor_data = serialize_sensor_data(
    version=1,
    role=0,
    sequence_number=40,
    data_id=10,
    sensor_type=1,
    block_number=0,
    last_block_number=0,
    payload=b"MNIST_SAMPLE",
)

print("Serialized SENSOR_DATA:", sensor_data)
print("Length:", len(sensor_data))
print("Decoded:", deserialize_sensor_data(sensor_data))


# ============================================================
# TEST 7: MODEL_UPDATE
# ============================================================

print("\n=== Test 7: MODEL_UPDATE ===")

model_update = serialize_model_update(
    version=1,
    role=1,
    sequence_number=50,
    update_id=3,
    model_id=5,
    block_number=0,
    last_block_number=0,
    payload=b"MNIST_MODEL_UPDATE",
)

print("Serialized MODEL_UPDATE:", model_update)
print("Length:", len(model_update))
print("Decoded:", deserialize_model_update(model_update))


# ============================================================
# TEST 8: SENSOR_DATA_ACK
# ============================================================

print("\n=== Test 8: SENSOR_DATA_ACK ===")

sensor_data_ack = serialize_sensor_data_ack(
    version=1,
    sequence_number=60,
    data_id=10,
    payload=b"ACK_DATA",
)

print("Serialized SENSOR_DATA_ACK:", sensor_data_ack)
print("Length:", len(sensor_data_ack))
print("Decoded:", deserialize_sensor_data_ack(sensor_data_ack))


# ============================================================
# TEST 9: MODEL_UPDATE_ACK
# ============================================================

print("\n=== Test 9: MODEL_UPDATE_ACK ===")

model_update_ack = serialize_model_update_ack(
    version=1,
    sequence_number=70,
    update_id=3,
    payload=b"ACK_DATA",
)

print("Serialized MODEL_UPDATE_ACK:", model_update_ack)
print("Length:", len(model_update_ack))
print("Decoded:", deserialize_model_update_ack(model_update_ack))


# ============================================================
# TEST 10: Fragmentation + Packetization
# ============================================================

print("\n=== Test 10: Fragmentation and Packetization ===")

# Create a 2500-byte test payload
payload = b"A" * 2500

# Fragment the payload
fragments = fragment_payload(payload)

# Store decoded fragments for later tests
decoded_fragments = []

for block_number, last_block_number, fragment in fragments:

    packet = serialize_sensor_data(
        version=1,
        role=0,
        sequence_number=block_number,
        data_id=10,
        sensor_type=1,
        block_number=block_number,
        last_block_number=last_block_number,
        payload=fragment,
    )

    print(
        f"Packet {block_number}: "
        f"{len(packet)} bytes "
        f"(payload: {len(fragment)} bytes)"
    )

    decoded = deserialize_sensor_data(packet)

    decoded_fragments.append(decoded)

    print(
        f"Decoded: "
        f"Block {decoded['block_number']}/"
        f"{decoded['last_block_number']}, "
        f"Payload {len(decoded['payload'])} bytes"
    )


# ============================================================
# TEST 11: Complete Reassembly
# ============================================================

print("\n=== Test 11: Complete Reassembly ===")

reassembled = reassemble_fragments(decoded_fragments)

print("Reassembled payload:", len(reassembled), "bytes")
print("Matches original:", reassembled == payload)


# ============================================================
# TEST 12: Missing Fragment
# ============================================================

print("\n=== Test 12: Missing Fragment ===")

missing_fragments = [
    decoded_fragments[0],
    decoded_fragments[2],
]

try:
    reassembled = reassemble_fragments(missing_fragments)
    print("Reassembled payload:", len(reassembled), "bytes")
except ValueError as e:
    print("Reassembly failed:", e)


# ============================================================
# TEST 13: Out-of-Order Fragments
# ============================================================

print("\n=== Test 13: Out-of-Order Fragments ===")

out_of_order_fragments = [
    decoded_fragments[0],
    decoded_fragments[2],
    decoded_fragments[1],
]

reassembled = reassemble_fragments(out_of_order_fragments)

print("Reassembled payload:", len(reassembled), "bytes")
print("Matches original:", reassembled == payload)


# ============================================================
# TEST 14: Duplicate Fragment
# ============================================================

print("\n=== Test 14: Duplicate Fragment ===")

duplicate_fragments = [
    decoded_fragments[0],
    decoded_fragments[1],
    decoded_fragments[1],
    decoded_fragments[2],
]

try:
    reassembled = reassemble_fragments(duplicate_fragments)
    print("Reassembled payload:", len(reassembled), "bytes")
except ValueError as e:
    print("Reassembly failed:", e)