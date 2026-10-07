"""Local model state and outbound model-update buffering for FLAME."""

from collections import deque
from typing import Any, Deque, Dict, List, Optional

from ..communication.message import PacketType


class ModelManager:
    """Maintain a Cluster Head's local model state.

    The manager receives model states from the Trainer and Aggregation
    submodules.  It creates MODEL_UPDATE message objects for the communication
    module when a compatible neighboring Cluster Head is reachable.
    """

    def __init__(self, model_id: int, model_state: Any = None) -> None:
        if type(model_id) is not int or not 0 <= model_id <= 0xFF:
            raise ValueError("Model ID must fit within 8 bits.")

        self._model_id = model_id
        self._model_state = model_state
        self._update_id = 0
        self._outbound_update_buffer: Deque[Dict[str, Any]] = deque()

    def get_model_id(self) -> int:
        """Return the model identifier carried by MODEL_UPDATE messages."""

        return self._model_id

    def get_model_state(self) -> Any:
        """Return the current local model state."""

        return self._model_state

    def get_update_id(self) -> int:
        """Return the local model-update progress indicator."""

        return self._update_id

    def update_from_trainer(self, model_state: Any) -> Any:
        """Store a model produced by the Trainer submodule."""

        return self._update_state(model_state)

    def update_from_aggregation(self, model_state: Any) -> Any:
        """Store a model produced by the Aggregation submodule."""

        return self._update_state(model_state)

    def _update_state(self, model_state: Any) -> Any:
        if self._update_id >= 0xFF:
            raise OverflowError("Model update ID cannot exceed 8 bits.")

        self._model_state = model_state
        self._update_id += 1
        return self._model_state

    def create_model_update(
        self,
        *,
        version: int = 1,
        sequence_number: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create and buffer a MODEL_UPDATE for the communication module."""

        if self._model_state is None:
            raise RuntimeError("Cannot create a MODEL_UPDATE without a model state.")
        if type(version) is not int or not 0 <= version <= 0x0F:
            raise ValueError("Version must fit within 4 bits.")
        if sequence_number is not None and (
            type(sequence_number) is not int or not 0 <= sequence_number <= 0xFFFF
        ):
            raise ValueError("Sequence number must fit within 16 bits.")

        message = {
            "packet_type": PacketType.MODEL_UPDATE,
            "version": version,
            "role": 1,
            "update_id": self._update_id,
            "model_id": self._model_id,
            "payload": self._model_state,
        }
        if sequence_number is not None:
            message["sequence_number"] = sequence_number

        self._outbound_update_buffer.append(message)
        return message

    def pop_outbound_updates(self) -> List[Dict[str, Any]]:
        """Remove and return buffered MODEL_UPDATE message objects."""

        updates = list(self._outbound_update_buffer)
        self._outbound_update_buffer.clear()
        return updates
