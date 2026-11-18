"""Federated-learning workflow for a Cluster Head."""

from __future__ import annotations

from typing import Any, List

from ..communication.message import PacketType
from .aggregation import Aggregation
from .dataset_manager import DatasetManager
from .model_manager import ModelManager
from .trainer import Trainer


class FederatedLearningModule:
    """Connect the FLAME communication buffers to the FL submodules.

    The module is intended for Cluster Heads. Sensor observations trigger
    local training when the Trainer's new-observation condition is met.
    Complete model updates are buffered and aggregated independently of new
    sensor observations. Model updates produced by local training remain in
    the ModelManager outbound buffer until the application sends them to a
    reachable Cluster Head.
    """

    def __init__(
        self,
        dataset_manager: DatasetManager,
        trainer: Trainer,
        model_manager: ModelManager,
        aggregation: Aggregation,
    ) -> None:
        if not isinstance(dataset_manager, DatasetManager):
            raise TypeError("dataset_manager must be a DatasetManager.")
        if not isinstance(trainer, Trainer):
            raise TypeError("trainer must be a Trainer.")
        if not isinstance(model_manager, ModelManager):
            raise TypeError("model_manager must be a ModelManager.")
        if not isinstance(aggregation, Aggregation):
            raise TypeError("aggregation must be an Aggregation.")

        self.dataset_manager = dataset_manager
        self.trainer = trainer
        self.model_manager = model_manager
        self.aggregation = aggregation

    def handle_message(self, message: dict) -> Any:
        """Process one complete message delivered by CommunicationModule."""
        packet_type = message.get("packet_type")
        if packet_type == PacketType.SENSOR_DATA:
            self.dataset_manager.receive_sensor_data(message)
            current_model = self.model_manager.get_model_state()
            if current_model is not None:
                self.trainer.set_local_model(current_model)
            model = self.trainer.train()
            if model is not None:
                self.model_manager.update_from_trainer(model)
                self._queue_model_update()
            return model

        if packet_type == PacketType.MODEL_UPDATE:
            aggregated_model = self.aggregation.receive_update(message)
            if aggregated_model is not None:
                self.trainer.set_local_model(aggregated_model)
                # Only training on fresh sensor observations queues an update.
            return aggregated_model

        return None

    def pop_outbound_updates(self) -> List[dict]:
        """Return model updates ready to send to reachable peers."""
        return self.model_manager.pop_outbound_updates()

    def _queue_model_update(self, exclude_source=None) -> dict:
        """Queue a model update and retain its local loop-prevention hint."""
        update = self.model_manager.create_model_update()
        if exclude_source is not None:
            # This is application metadata only; the packet serializer ignores
            # unknown fields, so it never changes the FLAME wire format.
            update["_exclude_source"] = tuple(exclude_source)
        return update
