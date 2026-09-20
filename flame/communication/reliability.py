class ReliabilityManager:

    def __init__(self):
        self.received_blocks = set()
        self.outbound_transfers = {}

    def is_duplicate(
        self,
        source_address,
        packet_type,
        message_id,
        block_number
    ):
        key = (
            source_address,
            packet_type,
            message_id,
            block_number,
        )

        if key in self.received_blocks:
            return True

        self.received_blocks.add(key)
        return False

    def process_received_fragment(
        self,
        source_address,
        packet_type,
        message_id,
        block_number,
        fragment
    ):
        duplicate = self.is_duplicate(
            source_address,
            packet_type,
            message_id,
            block_number,
        )

        if duplicate:
            return None

        return fragment

    def create_ack(
        self,
        packet_type,
        message_id,
        received_blocks
    ):
        return {
            "packet_type": packet_type,
            "message_id": message_id,
            "received_blocks": sorted(received_blocks),
        }

    def find_missing_blocks(self, received_blocks, last_block_number):
        expected_blocks = set(range(last_block_number + 1))
        received_blocks = set(received_blocks)

        return sorted(expected_blocks - received_blocks)

    def create_ack(
        self,
        packet_type,
        message_id,
        received_blocks,
        last_block_number
    ):
        return {
            "packet_type": packet_type,
            "message_id": message_id,
            "received_blocks": sorted(received_blocks),
            "missing_blocks": self.find_missing_blocks(
                received_blocks,
                last_block_number
            ),
        }

    def register_transfer(
        self,
        destination_address,
        packet_type,
        message_id,
        fragments,
        window_size=3
    ):
        key = (
            destination_address,
            packet_type,
            message_id,
        )

        self.outbound_transfers[key] = {
            "fragments": fragments,
            "window_start": 0,
            "window_size": window_size,
        }

    def get_fragment(
        self,
        destination_address,
        packet_type,
        message_id,
        block_number
    ):
        key = (
            destination_address,
            packet_type,
            message_id,
        )

        transfer = self.outbound_transfers.get(key)

        if transfer is None:
            return None

        return transfer["fragments"].get(block_number)

    def get_missing_fragments(
        self,
        destination_address,
        packet_type,
        message_id,
        missing_blocks
    ):
        return [
            self.get_fragment(
                destination_address,
                packet_type,
                message_id,
                block_number,
            )
            for block_number in missing_blocks
        ]

    def get_current_window(
        self,
        destination_address,
        packet_type,
        message_id
    ):
        key = (
            destination_address,
            packet_type,
            message_id,
        )

        transfer = self.outbound_transfers.get(key)

        if transfer is None:
            return []

        start = transfer["window_start"]
        size = transfer["window_size"]

        return list(
            range(
                start,
                min(start + size, len(transfer["fragments"]))
            )
        )

    def advance_window(
        self,
        destination_address,
        packet_type,
        message_id,
        acknowledged_blocks
    ):
        key = (
            destination_address,
            packet_type,
            message_id,
        )

        transfer = self.outbound_transfers.get(key)

        if transfer is None:
            return

        window_start = transfer["window_start"]
        window_size = transfer["window_size"]

        window_end = window_start + window_size

        if all(
            block in acknowledged_blocks
            for block in range(window_start, window_end)
            if block in transfer["fragments"]
        ):
            transfer["window_start"] = window_end

    def process_ack(
        self,
        destination_address,
        packet_type,
        message_id,
        received_blocks,
        missing_blocks
    ):
        if missing_blocks:
            return {
                "advanced": False,
                "retransmit": missing_blocks,
            }

        self.advance_window(
            destination_address,
            packet_type,
            message_id,
            received_blocks,
        )

        return {
            "advanced": True,
            "retransmit": [],
        }