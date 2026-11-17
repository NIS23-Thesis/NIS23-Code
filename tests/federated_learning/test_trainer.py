import unittest

from flame.communication.message import PacketType
from flame.federated_learning.dataset_manager import DatasetManager
from flame.federated_learning.dataset_storage import DatasetStorage
from flame.federated_learning.trainer import Trainer


class TestTrainer(unittest.TestCase):

    def create_manager(self):
        storage = DatasetStorage(capacity=100)
        return DatasetManager(storage)

    def add_observation(
        self,
        manager,
        data_id,
        payload=b"MNIST_DATA",
    ):
        return manager.receive_sensor_data(
            {
                "packet_type": PacketType.SENSOR_DATA,
                "data_id": data_id,
                "sensor_type": 1,
                "sequence_number": data_id,
                "payload": payload,
            }
        )

    def create_trainer(
        self,
        manager,
        expected_observations=10,
        training_threshold=0.80,
        train_function=None,
    ):
        return Trainer(
            dataset_manager=manager,
            expected_observations=expected_observations,
            training_threshold=training_threshold,
            train_function=train_function,
        )

    def test_no_observations(self):
        manager = self.create_manager()

        trainer = self.create_trainer(manager)

        self.assertFalse(
            trainer.should_train()
        )

    def test_training_condition_below_80_percent(self):
        manager = self.create_manager()

        for data_id in range(1, 8):
            self.add_observation(manager, data_id)

        trainer = self.create_trainer(manager)

        # 7 / 10 = 70%
        self.assertFalse(
            trainer.should_train()
        )

    def test_training_condition_at_80_percent(self):
        manager = self.create_manager()

        for data_id in range(1, 9):
            self.add_observation(manager, data_id)

        trainer = self.create_trainer(manager)

        # 8 / 10 = 80%
        self.assertTrue(
            trainer.should_train()
        )

    def test_training_condition_above_80_percent(self):
        manager = self.create_manager()

        for data_id in range(1, 10):
            self.add_observation(manager, data_id)

        trainer = self.create_trainer(manager)

        # 9 / 10 = 90%
        self.assertTrue(
            trainer.should_train()
        )

    def test_new_observations_are_detected(self):
        manager = self.create_manager()

        self.add_observation(manager, 1)
        self.add_observation(manager, 2)

        trainer = self.create_trainer(
            manager,
            expected_observations=2,
        )

        new_observations = trainer.get_new_observations()

        self.assertEqual(
            len(new_observations),
            2,
        )

        self.assertEqual(
            {entry.data_id for entry in new_observations},
            {1, 2},
        )

    def test_training_uses_new_observations(self):
        manager = self.create_manager()

        for data_id in range(1, 9):
            self.add_observation(manager, data_id)

        trained_data = []

        def fake_train(observations):
            trained_data.append(
                [entry.data_id for entry in observations]
            )
            return "trained_model"

        trainer = self.create_trainer(
            manager,
            train_function=fake_train,
        )

        result = trainer.train()

        self.assertEqual(
            result,
            "trained_model",
        )

        self.assertEqual(
            trained_data,
            [[1, 2, 3, 4, 5, 6, 7, 8]],
        )

        self.assertEqual(
            trainer.get_used_data_ids(),
            {1, 2, 3, 4, 5, 6, 7, 8},
        )

    def test_previous_observations_are_not_retrained(self):
        manager = self.create_manager()
        trained_data = []
        def fake_train(observations):
            trained_data.append([entry.data_id for entry in observations])
            return "trained_model"
        trainer = self.create_trainer(manager, train_function=fake_train)
        for data_id in range(1, 9):
            self.add_observation(manager, data_id)
        self.assertEqual(trainer.train(), "trained_model")
        # A new image alone cannot cause another training run.
        for data_id in range(9, 16):
            self.add_observation(manager, data_id)
            self.assertFalse(trainer.should_train())
            self.assertIsNone(trainer.train())
        self.add_observation(manager, 16)
        self.assertTrue(trainer.should_train())
        self.assertEqual(trainer.train(), "trained_model")
        self.assertEqual(trained_data, [list(range(1, 9)), list(range(9, 17))])
        self.assertEqual(trainer.get_used_data_ids(), set(range(1, 17)))
        self.assertFalse(trainer.should_train())

    def test_fractional_requirement_uses_eight_of_ten_and_nine_of_eleven(self):
        manager = self.create_manager()
        trainer = self.create_trainer(manager, expected_observations=11,
                                      training_threshold=.8, train_function=lambda x: 'model')
        for index in range(1, 9):
            self.add_observation(manager, index)
        self.assertFalse(trainer.should_train())
        self.add_observation(manager, 9)
        self.assertTrue(trainer.should_train())
        self.assertEqual(trainer.train(), 'model')
        self.assertFalse(trainer.should_train())

    def test_training_does_not_happen_below_80_percent(self):
        manager = self.create_manager()

        for data_id in range(1, 8):
            self.add_observation(manager, data_id)

        training_called = False

        def fake_train(observations):
            nonlocal training_called
            training_called = True
            return "trained_model"

        trainer = self.create_trainer(
            manager,
            train_function=fake_train,
        )

        result = trainer.train()

        self.assertIsNone(result)
        self.assertFalse(training_called)

        self.assertEqual(
            trainer.get_used_data_ids(),
            set(),
        )

    def test_model_is_stored_after_training(self):
        manager = self.create_manager()

        for data_id in range(1, 9):
            self.add_observation(manager, data_id)

        def fake_train(observations):
            return "local_model_v1"

        trainer = self.create_trainer(
            manager,
            train_function=fake_train,
        )

        trainer.train()

        self.assertEqual(
            trainer.get_local_model(),
            "local_model_v1",
        )


if __name__ == "__main__":
    unittest.main()