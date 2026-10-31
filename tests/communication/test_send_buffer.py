import unittest
from unittest.mock import Mock

from flame.communication.communication_module import CommunicationModule
from flame.communication.message import PacketType


class TestCommunicationSendBuffer(unittest.TestCase):

    def setUp(self):
        self.socket = Mock()

        self.module = CommunicationModule(
            self.socket,
            role=1,
            auto_start=False,
            send_buffer_capacity=1,
        )

        self.destination = ("192.168.1.20", 50000)

        self.message = {
            "packet_type": PacketType.HELLO,
            "role": 1,
            "sequence_number": 1,
            "model_id": 1,
        }

    def tearDown(self):
        self.module.stop()

    def test_enqueue_and_capacity(self):
        accepted = self.module.try_enqueue_message(
            self.message,
            destination=self.destination,
        )

        rejected = self.module.try_enqueue_message(
            self.message,
            destination=self.destination,
        )

        self.assertTrue(accepted)
        self.assertFalse(rejected)
        self.assertEqual(self.module.send_buffer_size, 1)
        self.assertFalse(self.module.send_buffer_available)

        # Enqueueing does not transmit immediately.
        self.socket.sendto.assert_not_called()

    def test_processing_queue_transmits_message(self):
        self.module.try_enqueue_message(
            self.message,
            destination=self.destination,
        )

        processed = self.module._service_send_buffer()

        self.assertTrue(processed)
        self.socket.sendto.assert_called_once()
        self.assertEqual(self.module.send_buffer_size, 0)
        self.assertTrue(self.module.send_buffer_available)

    def test_existing_send_message_still_transmits_immediately(self):
        self.module.send_message(
            self.message,
            destination=self.destination,
        )

        self.socket.sendto.assert_called_once()
        self.assertEqual(self.module.send_buffer_size, 0)


if __name__ == "__main__":
    unittest.main()