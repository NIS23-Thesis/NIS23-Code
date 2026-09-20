from reliability import ReliabilityManager


reliability = ReliabilityManager()


fragment = {
    "block_number": 0,
    "payload": b"BLOCK_0",
}


# First arrival
result = reliability.process_received_fragment(
    source_address="192.168.1.10",
    packet_type="SENSOR_DATA",
    message_id=10,
    block_number=0,
    fragment=fragment,
)

print("First arrival:", result)


# Same fragment arrives again
result = reliability.process_received_fragment(
    source_address="192.168.1.10",
    packet_type="SENSOR_DATA",
    message_id=10,
    block_number=0,
    fragment=fragment,
)

print("Duplicate arrival:", result)


# Different block
fragment_1 = {
    "block_number": 1,
    "payload": b"BLOCK_1",
}

result = reliability.process_received_fragment(
    source_address="192.168.1.10",
    packet_type="SENSOR_DATA",
    message_id=10,
    block_number=1,
    fragment=fragment_1,
)

print("New Block 1:", result)

ack = reliability.create_ack(
    packet_type="SENSOR_DATA",
    message_id=10,
    received_blocks={0, 1, 2},
    last_block_number=2,
)

print("Complete ACK:", ack)

ack = reliability.create_ack(
    packet_type="SENSOR_DATA",
    message_id=10,
    received_blocks={0, 2},
    last_block_number=2,
)

print("ACK with missing block:", ack)

fragments = {
    0: b"PACKET_BLOCK_0",
    1: b"PACKET_BLOCK_1",
    2: b"PACKET_BLOCK_2",
    3: b"PACKET_BLOCK_3",
    4: b"PACKET_BLOCK_4",
}

reliability.register_transfer(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    fragments=fragments,
)

print("Outbound transfers:", reliability.outbound_transfers)

fragment = reliability.get_fragment(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    block_number=1,
)

print("Fragment for retransmission:", fragment)

fragment = reliability.get_fragment(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    block_number=5,
)

print("Missing stored fragment:", fragment)

missing_fragments = reliability.get_missing_fragments(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    missing_blocks=[1, 2],
)

print("Fragments for retransmission:", missing_fragments)

window = reliability.get_current_window(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
)

print("Current window:", window)


# ACK with missing block
result = reliability.process_ack(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    received_blocks={0, 2},
    missing_blocks=[1],
)

print("ACK with missing block:", result)

window = reliability.get_current_window(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
)

print("Window after missing ACK:", window)


# Complete ACK
result = reliability.process_ack(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
    received_blocks={0, 1, 2},
    missing_blocks=[],
)

print("Complete ACK:", result)

window = reliability.get_current_window(
    destination_address="192.168.1.20",
    packet_type="SENSOR_DATA",
    message_id=10,
)

print("Window after complete ACK:", window)