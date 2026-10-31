from .aggregation import Aggregation
from .dataset_manager import DatasetManager
from .dataset_storage import DatasetEntry, DatasetStorage
from .model_manager import (
    ModelManager,
    deserialize_model_state,
    serialize_model_state,
)
from .module import FederatedLearningModule
from .trainer import Trainer

__all__ = [
    "Aggregation",
    "DatasetEntry",
    "DatasetManager",
    "DatasetStorage",
    "FederatedLearningModule",
    "ModelManager",
    "MNISTMLP",
    "MNISTTrainer",
    "Trainer",
    "deserialize_model_state",
    "serialize_model_state",
]


def __getattr__(name):
    """Load PyTorch trainers only on demand (never in sensor-only workers).

    Keeping MNISTMLP/MNISTTrainer public maintains existing import compatibility.
    """
    if name in ("MNISTMLP", "MNISTTrainer"):
        from . import mnist_trainer
        return getattr(mnist_trainer, name)
    raise AttributeError(name)
