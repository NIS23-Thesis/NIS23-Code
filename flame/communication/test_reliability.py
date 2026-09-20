import time

from reliability import ReliabilityManager, prepare_fragments
from message import fragment_payload


def test_duplicate_detection():
    reliability = ReliabilityManager()

    fragment = {
        "block_number": 0,
        "payload": b"BLOCK_0",
    }

    first = reliability.process_received_fragment(
        source_address="192.168.1.10",
        packet_type="SENSOR_DATA",
        message_id=10,
        block_number=0,
        fragment=fragment,
    )

    duplicate = reliability.process_received_fragment(
        source_address="192.168.1.10",
        packet_type="SENSOR_DATA",
        message_id=10,
        block_number=0,
        fragment=fragment,
    )

    new_fragment = {
        "block_number": 1,
        "payload": b"BLOCK_1",
    }

    new_block = reliability.process_received_fragment(
        source_address="192.168.1.10",
        packet_type="SENSOR_DATA",
        message_id=10,
        block_number=1,
        fragment=new_fragment,
    )

    print("First arrival:", first)
    print("Duplicate arrival:", duplicate)
    print("New block:", new_block)


def test_ack_generation():
    reliability = ReliabilityManager()

    complete_ack = reliability.create_ack(
        packet_type="SENSOR_DATA",
        message_id=10,
        received_blocks={0, 1, 2},
        last_block_number=2,
    )

    missing_ack = reliability.create_ack(
        packet_type="SENSOR_DATA",
        message_id=10,
        received_blocks={0, 2},
        last_block_number=2,
    )

    print("Complete ACK:", complete_ack)
    print("ACK with missing block:", missing_ack)


def create_test_transfer(reliability):
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


def test_transfer_and_window():
    reliability = ReliabilityManager()
    create_test_transfer(reliability)

    window = reliability.get_current_window(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
    )

    fragment = reliability.get_fragment(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
        1,
    )

    missing_fragment = reliability.get_fragment(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
        5,
    )

    missing_fragments = reliability.get_missing_fragments(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
        [1, 2],
    )

    print("Current window:", window)
    print("Stored fragment:", fragment)
    print("Missing stored fragment:", missing_fragment)
    print("Missing fragments:", missing_fragments)


def test_ack_processing():
    reliability = ReliabilityManager()
    create_test_transfer(reliability)

    result = reliability.process_ack(
        destination_address="192.168.1.20",
        packet_type="SENSOR_DATA",
        message_id=10,
        received_blocks={0, 2},
        missing_blocks=[1],
    )

    print("ACK with missing block:", result)
    print(
        "Window after missing ACK:",
        reliability.get_current_window(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
        ),
    )

    result = reliability.process_ack(
        destination_address="192.168.1.20",
        packet_type="SENSOR_DATA",
        message_id=10,
        received_blocks={0, 1, 2},
        missing_blocks=[],
    )

    print("Complete ACK:", result)
    print(
        "Window after complete ACK:",
        reliability.get_current_window(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
        ),
    )
    print(
        "Window size:",
        reliability.get_window_size(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
        ),
    )


def test_timeout_and_retransmission():
    reliability = ReliabilityManager()
    create_test_transfer(reliability)

    reliability.start_timeout(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
    )

    print(
        "Timed out immediately:",
        reliability.is_timeout(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
            timeout_duration=0.1,
        ),
    )

    time.sleep(0.2)

    print(
        "Timed out after 0.2 seconds:",
        reliability.is_timeout(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
            timeout_duration=0.1,
        ),
    )

    timeout_fragments = reliability.process_timeout(
        "192.168.1.20",
        "SENSOR_DATA",
        10,
        timeout_duration=0.1,
    )

    print("Timeout retransmission:", timeout_fragments)
    print(
        "Window size after timeout:",
        reliability.get_window_size(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
        ),
    )
    print(
        "Retry count:",
        reliability.outbound_transfers[
            ("192.168.1.20", "SENSOR_DATA", 10)
        ]["retry_count"],
    )


def test_retry_limit():
    reliability = ReliabilityManager()
    create_test_transfer(reliability)

    transfer_key = (
        "192.168.1.20",
        "SENSOR_DATA",
        10,
    )

    for attempt in range(4):
        reliability.outbound_transfers[transfer_key]["timeout"] = (
            time.time() - 1
        )

        result = reliability.process_timeout(
            "192.168.1.20",
            "SENSOR_DATA",
            10,
            timeout_duration=0.1,
        )

        print(f"Retry attempt {attempt + 1}:", result)

    print(
        "Transfer still exists:",
        transfer_key in reliability.outbound_transfers,
    )


def test_fragment_integration():
    payload = b"A" * 2500

    fragments = fragment_payload(payload)
    prepared_fragments = prepare_fragments(fragments)

    print(
        "Actual fragment sizes:",
        [len(fragment) for fragment in prepared_fragments.values()],
    )


def test_transfer_cleanup():
    reliability = ReliabilityManager()

    fragments = {
        0: b"BLOCK_0",
        1: b"BLOCK_1",
        2: b"BLOCK_2",
    }

    reliability.register_transfer(
        destination_address="192.168.1.30",
        packet_type="SENSOR_DATA",
        message_id=20,
        fragments=fragments,
        window_size=3,
    )

    result = reliability.process_ack(
        destination_address="192.168.1.30",
        packet_type="SENSOR_DATA",
        message_id=20,
        received_blocks=[0, 1, 2],
        missing_blocks=[],
    )

    print("Cleanup ACK:", result)
    print(
        "Transfer after completion:",
        reliability.outbound_transfers,
    )


if __name__ == "__main__":
    print("\n--- Duplicate Detection ---")
    test_duplicate_detection()

    print("\n--- ACK Generation ---")
    test_ack_generation()

    print("\n--- Transfer and Window ---")
    test_transfer_and_window()

    print("\n--- ACK Processing ---")
    test_ack_processing()

    print("\n--- Timeout and Retransmission ---")
    test_timeout_and_retransmission()

    print("\n--- Retry Limit ---")
    test_retry_limit()

    print("\n--- Fragment Integration ---")
    test_fragment_integration()

    print("\n--- Transfer Cleanup ---")
    test_transfer_cleanup()
