from typing import Callable, List, Optional, Set, Tuple

from .dataset_manager import DatasetManager
from .dataset_storage import DatasetEntry


class Trainer:
    """Handles local training using observations stored by a Cluster Head."""

    def __init__(
        self,
        dataset_manager: DatasetManager,
        expected_observations: int,
        training_threshold: float = 0.80,
        train_function: Optional[Callable] = None,
    ):
        if not isinstance(dataset_manager, DatasetManager):
            raise TypeError(
                "dataset_manager must be a DatasetManager."
            )

        if expected_observations <= 0:
            raise ValueError(
                "Expected observations must be greater than 0."
            )

        if not 0 < training_threshold <= 1:
            raise ValueError(
                "Training threshold must be between 0 and 1."
            )

        self.dataset_manager = dataset_manager
        self.expected_observations = expected_observations
        self.training_threshold = training_threshold
        self.train_function = train_function

        # Stores data IDs that have already been used for training.
        self._used_observations: Set[Tuple[object, int, int]] = set()

        # Stores the most recently produced local model.
        self._local_model = None

    def get_new_observations(self) -> List[DatasetEntry]:
        """Return observations that have not been used for training."""

        observations = self.dataset_manager.get_observations()

        return [
            observation
            for observation in observations
            if self._observation_key(observation) not in self._used_observations
        ]

    @staticmethod
    def _observation_key(observation: DatasetEntry) -> Tuple[object, int, int]:
        return (
            observation.source_address,
            observation.data_id,
            observation.sequence_number,
        )

    def should_train(self) -> bool:
        """
        Return whether the configured percentage of expected
        observations has been received.
        """

        received_count = len(
            self.dataset_manager.get_observations()
        )

        received_ratio = (
            received_count / self.expected_observations
        )

        return received_ratio >= self.training_threshold

    def train(self):
        """
        Perform local training using new observations.

        Training starts only when the configured training condition
        is satisfied.
        """

        if not self.should_train():
            return None

        new_observations = self.get_new_observations()

        if not new_observations:
            return None

        if self.train_function is None:
            raise RuntimeError(
                "No training function has been configured."
            )

        model = self.train_function(new_observations)

        for observation in new_observations:
            self._used_observations.add(self._observation_key(observation))

        self._local_model = model

        return model

    def get_local_model(self):
        """Return the most recently produced local model."""

        return self._local_model

    def set_local_model(self, model):
        """Synchronize an externally aggregated model with the trainer."""

        self._local_model = model
        setter = getattr(self.train_function, "set_local_model", None)
        if callable(setter):
            setter(model)

    def get_used_data_ids(self) -> Set[int]:
        """Return the IDs of observations already used for training."""

        return {data_id for _source, data_id, _sequence in self._used_observations}
