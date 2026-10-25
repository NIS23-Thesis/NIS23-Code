
import unittest
from unittest.mock import Mock

from flame.communication.message import PacketType
from flame.sensing import DataAcquisition, SensingModule


class TestSensingModule(unittest.TestCase):

    def setUp(self):
        self.communication = Mock()
        self.available = True

        # Simulate the real Communication Module's Send Buffer.
        self.communication.try_enqueue_message.side_effect = (
            lambda message, destination: self.available
        )

        self.acquisition = DataAcquisition(
            lambda: b"sensor observation"
        )

        self.sensing = SensingModule(
            data_acquisition=self.acquisition,
            communication_module=self.communication,
            destination=("192.168.1.20", 50000),
            sensor_type=1,
            sample_buffer_capacity=2,
        )

    def test_collect_and_submit_sensor_data(self):
        message = self.sensing.collect_once()

        self.communication.try_enqueue_message.assert_called_once()

        submitted = (
            self.communication.try_enqueue_message.call_args.args[0]
        )

        self.assertEqual(
            submitted["packet_type"],
            PacketType.SENSOR_DATA,
        )
        self.assertEqual(submitted["payload"], b"sensor observation")
        self.assertEqual(submitted["sensor_type"], 1)
        self.assertEqual(message["data_id"], 1)
        self.assertEqual(self.sensing.sample_buffer_size, 0)

    def test_buffer_when_send_buffer_is_full(self):
        self.available = False

        self.sensing.collect_once()

        self.communication.send_message.assert_not_called()
        self.assertEqual(self.sensing.sample_buffer_size, 1)

    def test_flush_sample_buffer(self):
        # Simulate a full Send Buffer.
        self.available = False

        self.sensing.collect_once()
        self.sensing.collect_once()

        self.assertEqual(self.sensing.sample_buffer_size, 2)

        # Make the Send Buffer available and track new enqueue attempts.
        self.available = True
        self.communication.try_enqueue_message.reset_mock()

        submitted = self.sensing.flush_sample_buffer()

        self.assertEqual(submitted, 2)
        self.assertEqual(self.sensing.sample_buffer_size, 0)
        self.communication.try_enqueue_message.assert_called()
        self.assertEqual(
            self.communication.try_enqueue_message.call_count,
            2,
        )

    def test_sample_buffer_drops_oldest_when_full(self):
        self.available = False

        self.sensing.collect_once()
        self.sensing.collect_once()
        self.sensing.collect_once()

        pending = self.sensing.get_sample_buffer()

        self.assertEqual(len(pending), 2)
        self.assertEqual(self.sensing.dropped_samples, 1)
        self.assertEqual(
            pending[0]["payload"],
            b"sensor observation",
        )

    def test_acquisition_rejects_non_bytes(self):
        acquisition = DataAcquisition(lambda: "not bytes")

        with self.assertRaises(TypeError):
            acquisition.collect()


if __name__ == "__main__":
    unittest.main()
