"""Asynchronous model aggregation for the FLAME federated-learning module."""

from collections import deque
from collections.abc import Mapping, Sequence
import math
from numbers import Number
import pickle
from typing import Any, Deque, Dict, Optional, Set, Tuple

from ..communication.message import PacketType
from .model_manager import ModelManager, deserialize_model_state


class Aggregation:
    """Buffer and aggregate compatible MODEL_UPDATE message objects.

    Aggregation follows the FLAME thesis' WAFL rule:

    * delta_j = M_j - M_local
    * delta_avg = sum(update_id_j * delta_j) / sum(update_id_j)
    * M_new = M_local + eta * delta_avg

    Model state arithmetic is kept generic so the caller can provide scalar,
    sequence, mapping, or model objects that implement the corresponding
    arithmetic operators.
    """

    def __init__(
        self,
        model_manager: ModelManager,
        aggregation_threshold: int = 1,
        aggregation_learning_rate: float = 1.0,
    ) -> None:
        if not isinstance(model_manager, ModelManager):
            raise TypeError("model_manager must be a ModelManager.")
        if type(aggregation_threshold) is not int or aggregation_threshold <= 0:
            raise ValueError("Aggregation threshold must be greater than 0.")
        if not isinstance(aggregation_learning_rate, Number) or not (
            0 <= aggregation_learning_rate <= 1
        ):
            raise ValueError("WAFL aggregation coefficient must be between 0 and 1.")

        self.model_manager = model_manager
        self.aggregation_threshold = aggregation_threshold
        self.aggregation_learning_rate = aggregation_learning_rate
        self._buffer: Deque[Any] = deque()
        self._seen_updates: Set[Tuple[Any, int]] = set()

    def add_update(self, model_update: Any) -> bool:
        """Validate and buffer one compatible, non-duplicate MODEL_UPDATE."""

        if not self._is_valid_update(model_update):
            return False

        update_id = _get_field(model_update, "update_id")
        source_address = _get_field(model_update, "source_address")
        key = (source_address, update_id)
        if key in self._seen_updates:
            return False

        self._seen_updates.add(key)
        self._buffer.append(model_update)
        return True

    def receive_update(self, model_update: Any) -> Optional[Any]:
        """Accept an update and aggregate when the configured threshold is met."""

        if not self.add_update(model_update):
            return None
        return self.aggregate_if_ready()

    def aggregate_if_ready(self) -> Optional[Any]:
        """Aggregate the buffered updates once the threshold is satisfied."""

        if len(self._buffer) < self.aggregation_threshold:
            return None
        return self.aggregate()

    def aggregate(self) -> Optional[Any]:
        """Apply the WAFL aggregation rule to the buffered updates."""

        if not self._buffer:
            return None

        local_model = self.model_manager.get_model_state()
        if local_model is None:
            raise RuntimeError("Cannot aggregate without a local model state.")

        weighted_difference = None
        total_weight = 0
        for model_update in self._buffer:
            received_model = _model_state_from_update(model_update)
            difference = _subtract(received_model, local_model)
            update_weight = _get_field(model_update, "update_id")
            weighted_difference = _add(
                weighted_difference,
                _scale(difference, update_weight),
            )
            total_weight += update_weight

        if total_weight == 0:
            # A zero-progress update is valid but has no influence by itself.
            # Keep it buffered until a positive-weight update is available.
            return None

        average_difference = _scale(weighted_difference, 1 / total_weight)
        new_model = _add(
            local_model,
            _scale(average_difference, self.aggregation_learning_rate),
        )

        if _states_equal(new_model, local_model):
            self._buffer.clear()
            return None

        self.model_manager.update_from_aggregation(new_model)
        self._buffer.clear()
        return new_model

    def _is_valid_update(self, model_update: Any) -> bool:
        packet_type = _get_field(model_update, "packet_type")
        if isinstance(packet_type, str):
            try:
                packet_type = PacketType[packet_type]
            except KeyError:
                return False
        if packet_type != PacketType.MODEL_UPDATE:
            return False

        model_id = _get_field(model_update, "model_id")
        update_id = _get_field(model_update, "update_id")
        if model_id != self.model_manager.get_model_id():
            return False
        if type(update_id) is not int or not 0 <= update_id <= 0xFF:
            return False
        try:
            _model_state_from_update(model_update)
        except (TypeError, ValueError, EOFError, pickle.UnpicklingError):
            return False
        return True


def _model_state_from_update(model_update: Any) -> Any:
    payload = _get_field(model_update, "payload")
    if payload is None:
        raise ValueError("MODEL_UPDATE payload is required.")
    if isinstance(payload, (bytes, bytearray)):
        return deserialize_model_state(payload)
    return payload


def _get_field(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _subtract(left: Any, right: Any) -> Any:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            raise ValueError("Model states must contain the same keys.")
        return {key: _subtract(left[key], right[key]) for key in left}
    if _is_sequence(left) and _is_sequence(right):
        if len(left) != len(right):
            raise ValueError("Model states must have matching lengths.")
        values = [_subtract(a, b) for a, b in zip(left, right)]
        return tuple(values) if isinstance(left, tuple) else values
    try:
        return left - right
    except TypeError as error:
        raise TypeError("Model state does not support subtraction.") from error


def _add(left: Any, right: Any) -> Any:
    if left is None:
        return right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            raise ValueError("Model states must contain the same keys.")
        return {key: _add(left[key], right[key]) for key in left}
    if _is_sequence(left) and _is_sequence(right):
        if len(left) != len(right):
            raise ValueError("Model states must have matching lengths.")
        values = [_add(a, b) for a, b in zip(left, right)]
        return tuple(values) if isinstance(left, tuple) else values
    try:
        return left + right
    except TypeError as error:
        raise TypeError("Model state does not support addition.") from error


def _scale(value: Any, factor: Number) -> Any:
    if isinstance(value, Mapping):
        return {key: _scale(item, factor) for key, item in value.items()}
    if _is_sequence(value):
        values = [_scale(item, factor) for item in value]
        return tuple(values) if isinstance(value, tuple) else values
    try:
        return value * factor
    except TypeError as error:
        raise TypeError("Model state does not support scalar multiplication.") from error


def _states_equal(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _states_equal(left[key], right[key]) for key in left
        )
    if _is_sequence(left) and _is_sequence(right):
        return len(left) == len(right) and all(
            _states_equal(a, b) for a, b in zip(left, right)
        )

    # Tensor/array arithmetic can leave a tiny round-off residue after
    # applying a complete model update (for example, M + (N - M)). Treat
    # numerically equivalent states as unchanged so opportunistic exchange
    # does not create an endless update loop.
    if hasattr(left, "shape") and hasattr(right, "shape"):
        try:
            difference = left - right
            magnitude = difference.abs() if hasattr(difference, "abs") else abs(difference)
            maximum = magnitude.max() if hasattr(magnitude, "max") else magnitude
            maximum = maximum.item() if hasattr(maximum, "item") else maximum
            left_max = left.abs().max() if hasattr(left, "abs") else abs(left).max()
            right_max = right.abs().max() if hasattr(right, "abs") else abs(right).max()
            left_max = left_max.item() if hasattr(left_max, "item") else left_max
            right_max = right_max.item() if hasattr(right_max, "item") else right_max
            scale = max(float(left_max), float(right_max))
            return float(maximum) <= 1e-6 + (1e-5 * scale)
        except (TypeError, ValueError, RuntimeError, AttributeError):
            pass

    if isinstance(left, Number) and isinstance(right, Number):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-12)

    try:
        comparison = left == right
        if isinstance(comparison, bool):
            return comparison
        if hasattr(comparison, "all"):
            result = comparison.all()
            return bool(result.item() if hasattr(result, "item") else result)
        if hasattr(comparison, "item"):
            return bool(comparison.item())
    except (TypeError, ValueError, RuntimeError):
        return False
    return False
