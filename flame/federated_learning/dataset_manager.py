from typing import List

from flame.communication.message import PacketType

from .dataset_storage import DatasetEntry, DatasetStorage


class DatasetManager:
    """Manages sensor observations received by a Cluster Head."""

    def __init__(self, storage: DatasetStorage):
        if not isinstance(storage, DatasetStorage):
            raise TypeError("storage must be a DatasetStorage.")

        self.storage = storage

    def receive_sensor_data(self, message: dict) -> DatasetEntry:
        """
        Receive a complete SENSOR_DATA message and store it.

        The message is expected to have already been reassembled
        by the Communication Module.
        """

        if not isinstance(message, dict):
            raise TypeError("message must be a dictionary.")

        if message.get("packet_type") != PacketType.SENSOR_DATA:
            raise ValueError("Message must be a SENSOR_DATA message.")

        required_fields = (
            "data_id",
            "sensor_type",
            "sequence_number",
            "payload",
        )

        for field in required_fields:
            if field not in message:
                raise ValueError(
                    f"SENSOR_DATA message is missing field: {field}"
                )

        payload = message["payload"]

        if not isinstance(payload, bytes):
            raise TypeError("SENSOR_DATA payload must be bytes.")

        source_address = message.get("source_address")
        for existing in self.storage.get_all():
            if (
                existing.source_address == source_address
                and existing.data_id == message["data_id"]
                and existing.sequence_number == message["sequence_number"]
            ):
                return existing

        entry = DatasetEntry(
            data_id=message["data_id"],
            sensor_type=message["sensor_type"],
            sequence_number=message["sequence_number"],
            payload=payload,
            source_address=source_address,
        )

        self.storage.add(entry)

        return entry

    def get_observations(self) -> List[DatasetEntry]:
        """Return all currently stored observations."""

        return self.storage.get_all()

    def get_observation_count(self) -> int:
        """Return the number of currently stored observations."""

        return self.storage.get_count()

    def get_latest_observation(self):
        """Return the most recently stored observation."""

        return self.storage.get_latest()

    def clear_observations(self) -> None:
        """Remove all stored observations."""

        self.storage.clear()
