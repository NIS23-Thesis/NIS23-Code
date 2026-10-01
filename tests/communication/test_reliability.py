import sys
import unittest
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.message import PacketType, fragment_payload
from flame.communication.reliability import (
    MIN_WINDOW_SIZE,
    ReliabilityManager as BaseReliabilityManager,
    prepare_fragments,
)


# ============================================================
# LOGGING CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)

logger = logging.getLogger("reliability")


# ============================================================
# RELIABILITY MANAGER WITH LOGGING
# ============================================================

class ReliabilityManager(BaseReliabilityManager):
    """
    Extends the original ReliabilityManager to display
    reliability events without modifying its implementation.
    """

    @staticmethod
    def log_action(event, action):
        if action is None:
            return

        blocks = [
            block
            for block, _ in getattr(action, "datagrams", [])
        ]

        logger.info(
            "[%s] window=%s-%s | sent=%s | "
            "advanced=%s | completed=%s | failed=%s",
            event,
            getattr(action, "window_start", None),
            getattr(action, "window_end", None),
            blocks,
            getattr(action, "window_advanced", False),
            getattr(action, "completed", False),
            getattr(action, "failed", False),
        )

    def register_transfer(self, *args, **kwargs):
        key = super().register_transfer(*args, **kwargs)

        logger.info(
            "[REGISTER] transfer=%s | fragments=%s",
            key,
            sorted(kwargs.get("fragments", {})),
        )

        return key

    def start_transfer(self, key, *args, **kwargs):
        logger.info(
            "[START TRANSFER] transfer=%s",
            key,
        )

        action = super().start_transfer(
            key, *args, **kwargs
        )

        self.log_action("INITIAL WINDOW", action)

        return action

    def process_ack(self, *args, **kwargs):
        received = (
            args[6]
            if len(args) > 6
            else kwargs.get("received_blocks", [])
        )

        logger.info(
            "[ACK RECEIVED] blocks=%s",
            received,
        )

        action = super().process_ack(
            *args, **kwargs
        )

        self.log_action("ACK RESULT", action)

        return action

    def service_outbound(self, *args, **kwargs):
        actions = super().service_outbound(
            *args, **kwargs
        )

        for action in actions:
            if action.failed:
                logger.warning(
                    "[RETRY EXHAUSTED] Transfer failed"
                )
                self.log_action(
                    "TRANSFER FAILED", action
                )
            else:
                logger.warning(
                    "[TIMEOUT] Retransmitting "
                    "unacknowledged fragments"
                )
                self.log_action(
                    "RETRANSMISSION", action
                )

        return actions

    def receive_fragment(
        self, peer, fragment, *args, **kwargs
    ):
        logger.info(
            "[RECEIVE] block=%s | sequence=%s",
            fragment["block_number"],
            fragment["sequence_number"],
        )

        result = super().receive_fragment(
            peer, fragment, *args, **kwargs
        )

        if result.duplicate:
            logger.warning(
                "[DUPLICATE] block=%s",
                fragment["block_number"],
            )

        if result.accepted:
            logger.info(
                "[ACCEPTED] block=%s",
                fragment["block_number"],
            )

        for ack in result.acknowledgements:
            logger.info(
                "[ACK SENT] window=%s-%s | "
                "received=%s",
                ack.window_start,
                ack.window_end,
                ack.received_blocks,
            )

        if result.complete_fragments is not None:
            logger.info(
                "[REASSEMBLY COMPLETE] "
                "fragments=%s",
                len(result.complete_fragments),
            )

        return result

    def service_inbound(self, *args, **kwargs):
        pending = super().service_inbound(
            *args, **kwargs
        )

        for key, ack in pending:
            logger.info(
                "[DELAYED ACK] transfer=%s | "
                "window=%s-%s | received=%s",
                key,
                ack.window_start,
                ack.window_end,
                ack.received_blocks,
            )

        return pending

    def cleanup(self, *args, **kwargs):
        expired = super().cleanup(
            *args, **kwargs
        )

        for key in expired:
            logger.warning(
                "[REASSEMBLY EXPIRED] transfer=%s",
                key,
            )

        return expired


# ============================================================
# TEST HELPERS
# ============================================================

DEST = ("127.0.0.1", 9000)


# ============================================================
# Helpers
# ============================================================

def fragments(count=5):
    return {
        block: f"BLOCK_{block}".encode()
        for block in range(count)
    }


def register(manager, *, count=5, sequence=10, retries=None):
    kwargs = {} if retries is None else {"max_retries": retries}

    key = manager.register_transfer(
        DEST,
        PacketType.SENSOR_DATA,
        message_id=7,
        sequence_number=sequence,
        fragments=fragments(count),
        **kwargs,
    )

    return key


def sensor_fragment(
    block,
    last,
    sequence=10,
    data_id=7,
    payload=None,
):
    return {
        "version": 1,
        "packet_type": PacketType.SENSOR_DATA,
        "role": 0,
        "sequence_number": sequence,
        "data_id": data_id,
        "sensor_type": 1,
        "block_number": block,
        "last_block_number": last,
        "payload": (
            payload
            if payload is not None
            else f"B{block}".encode()
        ),
    }


def print_header(number, title):
    print()
    print("=" * 65)
    print(f"[TEST {number}] {title}")
    print("=" * 65)


def print_pass():
    print()
    print("✓ TEST PASSED")


def print_fail(error):
    print()
    print(f"✗ TEST FAILED: {error}")


# ============================================================
# Interactive demonstrations
# ============================================================

def demo_1():
    print_header(
        1,
        "Initial window starts at two",
    )

    manager = ReliabilityManager()
    key = register(manager)

    print("Creating reliable transfer...")
    print("Configured initial window size:", manager.get_outbound_transfer(key).window_size)

    action = manager.start_transfer(key, now=0.0)

    print()
    print("Starting transfer...")
    print("Window:", action.window_start, "-", action.window_end)
    print("Blocks transmitted:", [
        block for block, _ in action.datagrams
    ])

    assert [block for block, _ in action.datagrams] == [0, 1]
    assert (action.window_start, action.window_end) == (0, 1)

    print_pass()


def demo_2():
    print_header(
        2,
        "Minimum window cannot be one",
    )

    print("Attempting to create:")
    print("initial_window_size = 1")
    print("min_window_size = 1")

    try:
        ReliabilityManager(
            initial_window_size=1,
            min_window_size=1,
        )
        raise AssertionError(
            "ReliabilityManager allowed a window size of 1"
        )
    except ValueError:
        print()
        print("Correctly rejected window size of 1.")

    print()
    print("MIN_WINDOW_SIZE =", MIN_WINDOW_SIZE)

    assert MIN_WINDOW_SIZE == 2

    print_pass()


def demo_3():
    print_header(
        3,
        "Full ACK advances and additively increases window",
    )

    manager = ReliabilityManager()
    key = register(manager)

    print("Starting transfer...")
    action = manager.start_transfer(key, now=0.0)

    print("Initial window:", action.window_start, "-", action.window_end)
    print("Sending blocks:", [
        block for block, _ in action.datagrams
    ])

    print()
    print("Receiver sends full ACK: [0, 1]")

    action = manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        window_start=0,
        window_end=1,
        received_blocks=[0, 1],
        now=0.1,
    )

    transfer = manager.get_outbound_transfer(key)

    print("ACK is complete:", action.window_advanced)
    print("Window size increased to:", transfer.window_size)
    print("New window:", action.window_start, "-", action.window_end)
    print("New blocks transmitted:", [
        block for block, _ in action.datagrams
    ])

    assert action.window_advanced
    assert (action.window_start, action.window_end) == (2, 4)
    assert [block for block, _ in action.datagrams] == [2, 3, 4]
    assert transfer.window_size == 3

    print()
    print("AIMD additive increase:")
    print("Window size: 2 → 3")

    print_pass()


def demo_4():
    print_header(
        4,
        "Loss retransmits only missing blocks",
    )

    manager = ReliabilityManager()
    key = register(manager)

    action = manager.start_transfer(key, now=0.0)

    print("Initial window:", action.window_start, "-", action.window_end)
    print("Sending blocks:", [
        block for block, _ in action.datagrams
    ])

    print()
    print("Receiver ACKs only block [0].")
    print("Block 1 is missing.")

    action = manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        window_start=0,
        window_end=1,
        received_blocks=[0],
        now=0.1,
    )

    transfer = manager.get_outbound_transfer(key)

    print()
    print("Window remains:", transfer.window_start, "-", transfer.window_end)
    print("Reduced window size:", transfer.reduced_window_size)
    print("Retransmitting blocks:", [
        block for block, _ in action.datagrams
    ])

    assert (transfer.window_start, transfer.window_end) == (0, 1)
    assert [block for block, _ in action.datagrams] == [1]
    assert transfer.reduced_window_size == 2

    print()
    print("Receiver now ACKs [0, 1].")
    print("All blocks in current window received.")

    next_action = manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        window_start=0,
        window_end=1,
        received_blocks=[0, 1],
        now=0.2,
    )

    print("Window advances to:",
          next_action.window_start,
          "-",
          next_action.window_end)

    assert (next_action.window_start, next_action.window_end) == (2, 3)
    assert manager.get_outbound_transfer(key).window_size == 2

    print_pass()


def demo_5():
    print_header(
        5,
        "Window growth is capped",
    )

    manager = ReliabilityManager(
        initial_window_size=2,
        max_window_size=3,
    )

    key = register(manager, count=12)

    action = manager.start_transfer(key, now=0.0)

    print("Initial window size:", 2)
    print("Maximum window size:", 3)

    print()
    print("Full ACK for blocks [0, 1]")

    action = manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        0,
        1,
        [0, 1],
        now=0.1,
    )

    transfer = manager.get_outbound_transfer(key)

    print("Window size:", transfer.window_size)

    assert transfer.window_size == 3

    print()
    print("Full ACK for blocks [2, 3, 4]")
    print("Attempting another increase...")

    manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        2,
        4,
        [2, 3, 4],
        now=0.2,
    )

    transfer = manager.get_outbound_transfer(key)

    print("Window size remains:", transfer.window_size)
    print("Maximum allowed:", manager.max_window_size)

    assert transfer.window_size == 3

    print_pass()


def demo_6():
    print_header(
        6,
        "Final ACK removes transfer",
    )

    manager = ReliabilityManager()
    key = register(manager, count=2)

    print("Starting transfer with 2 blocks.")

    action = manager.start_transfer(key, now=0.0)

    print("Sending blocks:", [
        block for block, _ in action.datagrams
    ])

    print()
    print("Receiver ACKs [0, 1].")
    print("This is the final ACK.")

    action = manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        0,
        1,
        [0, 1],
        now=0.1,
    )

    print("Transfer completed:", action.completed)
    print("Transfer still exists:",
          manager.has_outbound_transfer(key))

    assert action.completed
    assert not manager.has_outbound_transfer(key)

    print_pass()


def demo_7():
    print_header(
        7,
        "Timeout retransmits only unacknowledged blocks",
    )

    manager = ReliabilityManager(
        ack_timeout=0.1,
        ack_delay=0.01,
    )

    key = register(manager)

    action = manager.start_transfer(key, now=0.0)

    print("Initial blocks:", [
        block for block, _ in action.datagrams
    ])

    print()
    print("Receiver ACKs only block [0].")
    print("Block 1 remains unacknowledged.")

    manager.process_ack(
        DEST,
        PacketType.SENSOR_DATA,
        7,
        10,
        0,
        1,
        [0],
        now=0.02,
    )

    print()
    print("Waiting for ACK timeout...")
    print("ACK timeout:", manager.ack_timeout, "seconds")

    actions = manager.service_outbound(now=0.2)

    retransmitted = [
        block for block, _ in actions[0].datagrams
    ]

    print("Timeout triggered.")
    print("Retransmitting:", retransmitted)

    assert retransmitted == [1]

    print_pass()


def demo_8():
    print_header(
        8,
        "Retry exhaustion fails and cleans up",
    )

    manager = ReliabilityManager(
        ack_timeout=0.1,
        ack_delay=0.01,
        max_retries=1,
    )

    key = register(manager, retries=1)

    print("Maximum retries:", 1)

    manager.start_transfer(key, now=0.0)

    print()
    print("First timeout...")
    first = manager.service_outbound(now=0.2)[0]

    print("Transfer failed:", first.failed)
    print("Retrying...")

    print()
    print("Second timeout...")
    second = manager.service_outbound(now=0.4)[0]

    print("Transfer failed:", second.failed)
    print("Transfer still exists:",
          manager.has_outbound_transfer(key))

    assert not first.failed
    assert second.failed
    assert not manager.has_outbound_transfer(key)

    print_pass()


def demo_9():
    print_header(
        9,
        "Out-of-order fragments are accepted and reassembled",
    )

    manager = ReliabilityManager()

    print("Sending block 1 first...")
    second = manager.receive_fragment(
        DEST,
        sensor_fragment(1, 1),
        now=0.0,
    )

    print("Complete:", second.complete_fragments is not None)

    assert second.complete_fragments is None

    print()
    print("Sending block 0...")
    first = manager.receive_fragment(
        DEST,
        sensor_fragment(0, 1),
        now=0.01,
    )

    print("Received blocks in ACK:",
          [
              a.received_blocks
              for a in first.acknowledgements
          ])

    print("Reassembling in block order...")

    payload = b"".join(
        fragment["payload"]
        for fragment in first.complete_fragments
    )

    print("Reassembled payload:", payload)

    assert [
        a.received_blocks
        for a in first.acknowledgements
    ] == [(0, 1)]

    assert payload == b"B0B1"

    print_pass()


def demo_10():
    print_header(
        10,
        "Partial window ACK lists received blocks",
    )

    manager = ReliabilityManager(
        ack_timeout=0.2,
        ack_delay=0.05,
    )

    print("Receiving block 0...")
    manager.receive_fragment(
        DEST,
        sensor_fragment(0, 2),
        now=0.0,
    )

    print("Waiting for delayed ACK...")
    pending = manager.service_inbound(now=0.06)

    assert len(pending) == 1

    _key, ack = pending[0]

    print()
    print("ACK window:", ack.window_start, "-", ack.window_end)
    print("Received blocks:", ack.received_blocks)

    assert (ack.window_start, ack.window_end) == (0, 1)
    assert ack.received_blocks == (0,)

    print_pass()


def demo_11():
    print_header(
        11,
        "Duplicate fragment is rejected and ACK is resent",
    )

    manager = ReliabilityManager()

    print("Receiving block 0...")
    manager.receive_fragment(
        DEST,
        sensor_fragment(0, 1),
        now=0.0,
    )

    print("Receiving block 1...")
    manager.receive_fragment(
        DEST,
        sensor_fragment(1, 1),
        now=0.01,
    )

    print()
    print("Sending block 0 again...")
    duplicate = manager.receive_fragment(
        DEST,
        sensor_fragment(0, 1),
        now=0.02,
    )

    print("Duplicate:", duplicate.duplicate)
    print("Accepted:", duplicate.accepted)

    if duplicate.acknowledgements:
        print(
            "Resent ACK:",
            duplicate.acknowledgements[0].received_blocks,
        )

    assert duplicate.duplicate
    assert not duplicate.accepted
    assert (
        duplicate.acknowledgements[0].received_blocks
        == (0, 1)
    )

    print_pass()


def demo_12():
    print_header(
        12,
        "Same identifier with new sequence is a new transfer",
    )

    manager = ReliabilityManager(
        completed_retention=10.0,
    )

    print("Transfer 1:")
    print("  Data ID = 7")
    print("  Sequence = 10")

    first = manager.receive_fragment(
        DEST,
        sensor_fragment(
            0,
            0,
            sequence=10,
        ),
        now=0.0,
    )

    print("Transfer 1 complete:",
          first.complete_fragments is not None)

    print()
    print("Transfer 2:")
    print("  Data ID = 7")
    print("  Sequence = 11")

    second = manager.receive_fragment(
        DEST,
        sensor_fragment(
            0,
            0,
            sequence=11,
        ),
        now=0.1,
    )

    print("Transfer 2 complete:",
          second.complete_fragments is not None)
    print("Transfer 2 considered duplicate:",
          second.duplicate)

    assert first.complete_fragments is not None
    assert second.complete_fragments is not None
    assert not second.duplicate

    print_pass()


def demo_13():
    print_header(
        13,
        "Incomplete reassembly expires",
    )

    manager = ReliabilityManager(
        reassembly_timeout=0.1,
    )

    print("Receiving only block 0...")
    manager.receive_fragment(
        DEST,
        sensor_fragment(0, 2),
        now=0.0,
    )

    print("Transfer is incomplete.")
    print("Waiting beyond reassembly timeout...")

    expired = manager.cleanup(now=0.2)

    print()
    print("Expired transfers:", len(expired))
    print("Remaining inbound transfers:",
          len(manager.inbound_transfers))

    assert len(expired) == 1
    assert manager.inbound_transfers == {}

    print_pass()


def demo_14():
    print_header(
        14,
        "Fragment preparation",
    )

    payload = b"A" * 2500

    print("Original payload size:", len(payload), "bytes")
    print("Application fragment size: 1024 bytes")

    prepared = prepare_fragments(
        fragment_payload(payload)
    )

    print()
    print("Generated blocks:")

    for block in sorted(prepared):
        print(
            f"  Block {block}: "
            f"{len(prepared[block])} bytes"
        )

    assert sorted(prepared) == [0, 1, 2]
    assert [
        len(prepared[i])
        for i in range(3)
    ] == [1024, 1024, 452]

    print()
    print("Total blocks:", len(prepared))

    print_pass()


# ============================================================
# Test menu
# ============================================================

DEMOS = {
    "1": demo_1,
    "2": demo_2,
    "3": demo_3,
    "4": demo_4,
    "5": demo_5,
    "6": demo_6,
    "7": demo_7,
    "8": demo_8,
    "9": demo_9,
    "10": demo_10,
    "11": demo_11,
    "12": demo_12,
    "13": demo_13,
    "14": demo_14,
}


TEST_NAMES = {
    "1": "Initial window starts at two",
    "2": "Minimum window cannot be one",
    "3": "Full ACK advances and additively increases window",
    "4": "Loss retransmits only missing blocks",
    "5": "Window growth is capped",
    "6": "Final ACK removes transfer",
    "7": "Timeout retransmits only unacknowledged blocks",
    "8": "Retry exhaustion fails and cleans up",
    "9": "Out-of-order fragments are reassembled",
    "10": "Partial window ACK lists received blocks",
    "11": "Duplicate fragment is rejected",
    "12": "New sequence creates a new transfer",
    "13": "Incomplete reassembly expires",
    "14": "Fragment preparation",
}


def print_menu():
    print()
    print("=" * 65)
    print(" FLAME RELIABILITY TEST MENU")
    print("=" * 65)

    for number, name in TEST_NAMES.items():
        print(f"{number:>2}. {name}")

    print()
    print(" A. Run ALL tests")
    print(" 0. Exit")
    print("=" * 65)


def run_single_test(number):
    try:
        DEMOS[number]()
    except Exception as error:
        print_fail(error)


def run_all_tests():
    print()
    print("=" * 65)
    print(" RUNNING ALL RELIABILITY TESTS")
    print("=" * 65)

    passed = 0
    failed = 0

    for number in TEST_NAMES:
        try:
            DEMOS[number]()
            passed += 1
        except Exception as error:
            failed += 1
            print_fail(error)

    print()
    print("=" * 65)
    print(" ALL TESTS COMPLETE")
    print("=" * 65)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total : {passed + failed}")


def interactive_menu():
    while True:
        print_menu()

        choice = input("Select a test: ").strip().lower()

        if choice == "0":
            print()
            print("Exiting reliability test menu.")
            break

        elif choice == "a":
            run_all_tests()

        elif choice in DEMOS:
            run_single_test(choice)

        else:
            print()
            print("Invalid selection.")
            print("Please enter 1-14, A, or 0.")


# ============================================================
# Original unittest test suite
# ============================================================

class OutboundWindowTests(unittest.TestCase):

    def test_initial_window_starts_at_two(self):
        manager = ReliabilityManager()
        key = register(manager)
        action = manager.start_transfer(key, now=0.0)

        self.assertEqual(
            [block for block, _ in action.datagrams],
            [0, 1],
        )

        self.assertEqual(
            (action.window_start, action.window_end),
            (0, 1),
        )

    def test_minimum_window_cannot_be_one(self):
        with self.assertRaises(ValueError):
            ReliabilityManager(
                initial_window_size=1,
                min_window_size=1,
            )

        self.assertEqual(MIN_WINDOW_SIZE, 2)

    def test_full_ack_advances_and_additively_increases(self):
        manager = ReliabilityManager()
        key = register(manager)

        manager.start_transfer(key, now=0.0)

        action = manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            window_start=0,
            window_end=1,
            received_blocks=[0, 1],
            now=0.1,
        )

        self.assertTrue(action.window_advanced)

        self.assertEqual(
            (action.window_start, action.window_end),
            (2, 4),
        )

        self.assertEqual(
            [block for block, _ in action.datagrams],
            [2, 3, 4],
        )

        self.assertEqual(
            manager.get_outbound_transfer(key).window_size,
            3,
        )

    def test_loss_keeps_current_bounds_and_retransmits_only_missing(self):
        manager = ReliabilityManager()
        key = register(manager)

        manager.start_transfer(key, now=0.0)

        action = manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            window_start=0,
            window_end=1,
            received_blocks=[0],
            now=0.1,
        )

        transfer = manager.get_outbound_transfer(key)

        self.assertEqual(
            (transfer.window_start, transfer.window_end),
            (0, 1),
        )

        self.assertEqual(
            [block for block, _ in action.datagrams],
            [1],
        )

        self.assertEqual(
            transfer.reduced_window_size,
            2,
        )

        next_action = manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            window_start=0,
            window_end=1,
            received_blocks=[0, 1],
            now=0.2,
        )

        self.assertEqual(
            (next_action.window_start, next_action.window_end),
            (2, 3),
        )

        self.assertEqual(
            manager.get_outbound_transfer(key).window_size,
            2,
        )

    def test_window_growth_is_capped(self):
        manager = ReliabilityManager(
            initial_window_size=2,
            max_window_size=3,
        )

        key = register(manager, count=12)

        manager.start_transfer(key, now=0.0)

        manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            0,
            1,
            [0, 1],
            now=0.1,
        )

        self.assertEqual(
            manager.get_outbound_transfer(key).window_size,
            3,
        )

        manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            2,
            4,
            [2, 3, 4],
            now=0.2,
        )

        self.assertEqual(
            manager.get_outbound_transfer(key).window_size,
            3,
        )

    def test_final_ack_removes_transfer(self):
        manager = ReliabilityManager()
        key = register(manager, count=2)

        manager.start_transfer(key, now=0.0)

        action = manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            0,
            1,
            [0, 1],
            now=0.1,
        )

        self.assertTrue(action.completed)
        self.assertFalse(manager.has_outbound_transfer(key))

        key = register(
            manager,
            count=2,
        )

        manager.start_transfer(key, now=0.0)

        action = manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            0,
            1,
            [0, 1],
            now=0.1,
        )

        self.assertTrue(action.completed)

        self.assertFalse(
            manager.has_outbound_transfer(key)
        )


# ============================================================
# TIMEOUT TESTS
# ============================================================

class TimeoutTests(unittest.TestCase):

    def test_timeout_retransmits_only_unacknowledged_blocks(self):
        manager = ReliabilityManager(
            ack_timeout=0.1,
            ack_delay=0.01,
        )

        key = register(manager)

        manager.start_transfer(key, now=0.0)

        manager.process_ack(
            DEST,
            PacketType.SENSOR_DATA,
            7,
            10,
            0,
            1,
            [0],
            now=0.02,
        )

        actions = manager.service_outbound(now=0.2)

        self.assertEqual(
            [block for block, _ in actions[0].datagrams],
            [1],
        )

    def test_retry_exhaustion_fails_and_cleans_up(self):
        manager = ReliabilityManager(
            ack_timeout=0.1,
            ack_delay=0.01,
            max_retries=1,
        )
        key = register(manager, retries=1)

        manager.start_transfer(key, now=0.0)

        first = manager.service_outbound(now=0.2)[0]

        self.assertFalse(first.failed)

        second = manager.service_outbound(now=0.4)[0]

        self.assertTrue(second.failed)

        self.assertFalse(
            manager.has_outbound_transfer(key)
        )


# ============================================================
# INBOUND TESTS
# ============================================================

class InboundTests(unittest.TestCase):

    def test_out_of_order_window_is_accepted_and_reassembled(self):
        manager = ReliabilityManager()

        second = manager.receive_fragment(
            DEST,
            sensor_fragment(1, 1),
            now=0.0,
        )

        self.assertIsNone(second.complete_fragments)

        first = manager.receive_fragment(
            DEST,
            sensor_fragment(0, 1),
            now=0.01,
        )

        self.assertEqual(
            [
                a.received_blocks
                for a in first.acknowledgements
            ],
            [(0, 1)],
        )

        self.assertEqual(
            b"".join(
                fragment["payload"]
                for fragment in first.complete_fragments
            ),
            b"B0B1",
        )

    def test_partial_window_ack_lists_received_blocks(self):
        manager = ReliabilityManager(
            ack_timeout=0.2,
            ack_delay=0.05,
        )

        manager.receive_fragment(
            DEST,
            sensor_fragment(0, 2),
            now=0.0,
        )

        pending = manager.service_inbound(now=0.06)

        self.assertEqual(len(pending), 1)

        _key, ack = pending[0]

        self.assertEqual(
            (ack.window_start, ack.window_end),
            (0, 1),
        )

        self.assertEqual(
            ack.received_blocks,
            (0,),
        )

    def test_duplicate_is_not_accepted_and_resends_ack(self):
        manager = ReliabilityManager()

        manager.receive_fragment(
            DEST,
            sensor_fragment(0, 1),
            now=0.0,
        )

        manager.receive_fragment(
            DEST,
            sensor_fragment(1, 1),
            now=0.01,
        )

        duplicate = manager.receive_fragment(
            DEST,
            sensor_fragment(0, 1),
            now=0.02,
        )

        self.assertTrue(duplicate.duplicate)
        self.assertFalse(duplicate.accepted)

        self.assertEqual(
            duplicate.acknowledgements[0].received_blocks,
            (0, 1),
        )

    def test_reused_identifier_with_new_sequence_is_new_transfer(self):
        manager = ReliabilityManager(
            completed_retention=10.0,
        )

        first = manager.receive_fragment(
            DEST,
            sensor_fragment(
                0,
                0,
                sequence=10,
            ),
            now=0.0,
        )

        second = manager.receive_fragment(
            DEST,
            sensor_fragment(
                0,
                0,
                sequence=11,
            ),
            now=0.1,
        )

        self.assertIsNotNone(first.complete_fragments)
        self.assertIsNotNone(second.complete_fragments)
        self.assertFalse(second.duplicate)

    def test_incomplete_reassembly_expires(self):
        manager = ReliabilityManager(
            reassembly_timeout=0.1,
        )

        manager.receive_fragment(
            DEST,
            sensor_fragment(0, 2),
            now=0.0,
        )

        expired = manager.cleanup(now=0.2)

        self.assertEqual(len(expired), 1)
        self.assertEqual(
            manager.inbound_transfers,
            {},
        )

        manager.receive_fragment(
            DEST,
            sensor_fragment(0, 2),
            now=0.0,
        )

        expired = manager.cleanup(
            now=0.2
        )

        self.assertEqual(
            len(expired),
            1,
        )

        self.assertEqual(
            manager.inbound_transfers,
            {},
        )


# ============================================================
# FRAGMENT HELPER TESTS
# ============================================================

class FragmentHelperTests(unittest.TestCase):

    def test_prepare_fragments_keys_by_block_number(self):
        prepared = prepare_fragments(
            fragment_payload(b"A" * 2500)
        )

        self.assertEqual(
            sorted(prepared),
            [0, 1, 2],
        )

        self.assertEqual(
            [
                len(prepared[i])
                for i in range(3)
            ],
            [1024, 1024, 452],
        )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    unittest.main(
        verbosity=2,
        buffer=False,
    )
