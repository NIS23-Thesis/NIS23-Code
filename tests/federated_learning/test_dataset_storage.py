import unittest

from flame.federated_learning.dataset_storage import (
    DatasetEntry,
    DatasetStorage,
)


class TestDatasetStorage(unittest.TestCase):

    def test_add_entry(self):
        storage = DatasetStorage(capacity=3)

        entry = DatasetEntry(
            data_id=1,
            sensor_type=1,
            sequence_number=10,
            payload=b"OBSERVATION",
        )

        storage.add(entry)

        self.assertEqual(storage.get_count(), 1)
        self.assertEqual(storage.get_latest(), entry)

    def test_multiple_entries(self):
        storage = DatasetStorage(capacity=3)

        for data_id in range(1, 4):
            storage.add(
                DatasetEntry(
                    data_id=data_id,
                    sensor_type=1,
                    sequence_number=data_id,
                    payload=f"DATA_{data_id}".encode(),
                )
            )

        entries = storage.get_all()

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].data_id, 1)
        self.assertEqual(entries[1].data_id, 2)
        self.assertEqual(entries[2].data_id, 3)

    def test_capacity_removes_oldest(self):
        storage = DatasetStorage(capacity=2)

        storage.add(
            DatasetEntry(
                data_id=1,
                sensor_type=1,
                sequence_number=1,
                payload=b"DATA_1",
            )
        )

        storage.add(
            DatasetEntry(
                data_id=2,
                sensor_type=1,
                sequence_number=2,
                payload=b"DATA_2",
            )
        )

        storage.add(
            DatasetEntry(
                data_id=3,
                sensor_type=1,
                sequence_number=3,
                payload=b"DATA_3",
            )
        )

        entries = storage.get_all()

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].data_id, 2)
        self.assertEqual(entries[1].data_id, 3)

    def test_empty_storage(self):
        storage = DatasetStorage(capacity=3)

        self.assertEqual(storage.get_all(), [])
        self.assertEqual(storage.get_count(), 0)
        self.assertIsNone(storage.get_latest())

    def test_clear(self):
        storage = DatasetStorage(capacity=3)

        storage.add(
            DatasetEntry(
                data_id=1,
                sensor_type=1,
                sequence_number=1,
                payload=b"DATA",
            )
        )

        storage.clear()

        self.assertEqual(storage.get_count(), 0)
        self.assertEqual(storage.get_all(), [])

    def test_invalid_capacity(self):
        with self.assertRaises(ValueError):
            DatasetStorage(capacity=0)

    def test_invalid_entry(self):
        storage = DatasetStorage(capacity=3)

        with self.assertRaises(TypeError):
            storage.add("not an entry")

    def test_invalid_payload(self):
        storage = DatasetStorage(capacity=3)

        entry = DatasetEntry(
            data_id=1,
            sensor_type=1,
            sequence_number=1,
            payload="NOT_BYTES",
        )

        with self.assertRaises(TypeError):
            storage.add(entry)


if __name__ == "__main__":
    unittest.main()