import unittest

from flame.communication.message import PacketType
from flame.federated_learning.dataset_manager import DatasetManager
from flame.federated_learning.dataset_storage import DatasetStorage


class TestDatasetManager(unittest.TestCase):

    def test_receive_sensor_data(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        message = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 1,
            "sensor_type": 2,
            "sequence_number": 10,
            "payload": b"OBSERVATION_1",
        }

        entry = manager.receive_sensor_data(message)

        self.assertEqual(entry.data_id, 1)
        self.assertEqual(entry.sensor_type, 2)
        self.assertEqual(entry.sequence_number, 10)
        self.assertEqual(entry.payload, b"OBSERVATION_1")

        self.assertEqual(manager.get_observation_count(), 1)

    def test_receive_multiple_sensor_data(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        for data_id in range(1, 4):
            message = {
                "packet_type": PacketType.SENSOR_DATA,
                "data_id": data_id,
                "sensor_type": 1,
                "sequence_number": data_id,
                "payload": f"OBSERVATION_{data_id}".encode(),
            }

            manager.receive_sensor_data(message)

        observations = manager.get_observations()

        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0].data_id, 1)
        self.assertEqual(observations[1].data_id, 2)
        self.assertEqual(observations[2].data_id, 3)

    def test_reject_non_sensor_message(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        message = {
            "packet_type": PacketType.HELLO,
            "sequence_number": 1,
            "payload": b"HELLO",
        }

        with self.assertRaises(ValueError):
            manager.receive_sensor_data(message)

    def test_missing_required_field(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        message = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 1,
            "sensor_type": 1,
            "payload": b"OBSERVATION",
        }

        with self.assertRaises(ValueError):
            manager.receive_sensor_data(message)

    def test_invalid_payload(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        message = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 1,
            "sensor_type": 1,
            "sequence_number": 1,
            "payload": "NOT_BYTES",
        }

        with self.assertRaises(TypeError):
            manager.receive_sensor_data(message)

    def test_get_latest_observation(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        first = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 1,
            "sensor_type": 1,
            "sequence_number": 1,
            "payload": b"FIRST",
        }

        second = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 2,
            "sensor_type": 1,
            "sequence_number": 2,
            "payload": b"SECOND",
        }

        manager.receive_sensor_data(first)
        manager.receive_sensor_data(second)

        latest = manager.get_latest_observation()

        self.assertEqual(latest.data_id, 2)
        self.assertEqual(latest.payload, b"SECOND")

    def test_clear_observations(self):
        storage = DatasetStorage(capacity=10)
        manager = DatasetManager(storage)

        message = {
            "packet_type": PacketType.SENSOR_DATA,
            "data_id": 1,
            "sensor_type": 1,
            "sequence_number": 1,
            "payload": b"OBSERVATION",
        }

        manager.receive_sensor_data(message)
        manager.clear_observations()

        self.assertEqual(manager.get_observation_count(), 0)
        self.assertEqual(manager.get_observations(), [])


if __name__ == "__main__":
    unittest.main()