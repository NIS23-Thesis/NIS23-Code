"""Thesis-aligned WAFL-MLP training for FLAME MNIST observations."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional


try:
    import torch.nn as _torch_nn
except ImportError:
    _torch_nn = None


if _torch_nn is not None:

    class MNISTMLP(_torch_nn.Module):
        """The WAFL-MLP network: FC(784,128)-ReLU-FC(128,10)."""

        def __init__(self) -> None:
            super().__init__()
            self.fc1 = _torch_nn.Linear(784, 128)
            self.fc2 = _torch_nn.Linear(128, 10)

        def forward(self, inputs):
            inputs = inputs.view(-1, 784)
            return self.fc2(_torch_nn.functional.relu(self.fc1(inputs)))

else:

    class MNISTMLP:
        def __init__(self) -> None:
            _torch_components()


class MNISTTrainer:
    """Train the WAFL-MLP model on FLAME's 785-byte MNIST observations.

    Each observation contains 784 row-major grayscale pixels followed by one
    label byte. The model and optimizer match the WAFL-MLP workload:
    ``784 -> 128 -> 10`` with ReLU, CrossEntropyLoss, and Adam.
    """

    INPUT_SIZE = 28 * 28
    LABEL_COUNT = 10
    OBSERVATION_SIZE = INPUT_SIZE + 1

    def __init__(
        self,
        model_state: Optional[Mapping[str, Any]] = None,
        *,
        local_epochs: int = 1,
        batch_size: int = 32,
        learning_rate: float = 0.001,
    ) -> None:
        torch, nn, optim = _torch_components()
        if type(local_epochs) is not int or local_epochs <= 0:
            raise ValueError("local_epochs must be greater than zero.")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be greater than zero.")
        if not isinstance(learning_rate, (int, float)) or learning_rate <= 0:
            raise ValueError("learning_rate must be greater than zero.")

        self.local_epochs = local_epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.model = MNISTMLP()
        if model_state is not None:
            self.set_local_model(model_state)

        self._criterion = nn.CrossEntropyLoss()
        self._optimizer = optim.Adam(self.model.parameters(), lr=learning_rate)
        self._torch = torch

    def __call__(self, observations: Iterable[Any]) -> dict[str, Any]:
        """Train on one new observation batch and return a detached state."""

        samples = [
            _observation_to_tensors(self._torch, observation)
            for observation in observations
        ]
        if not samples:
            raise ValueError("At least one observation is required for training.")

        features = self._torch.stack([item[0] for item in samples])
        labels = self._torch.tensor(
            [item[1] for item in samples],
            dtype=self._torch.long,
        )

        self.model.train()
        for _ in range(self.local_epochs):
            for start in range(0, len(samples), self.batch_size):
                batch_features = features[start : start + self.batch_size]
                batch_labels = labels[start : start + self.batch_size]
                self._optimizer.zero_grad()
                outputs = self.model(batch_features)
                loss = self._criterion(outputs, batch_labels)
                loss.backward()
                self._optimizer.step()

        return self.get_model_state()

    def get_model_state(self) -> dict[str, Any]:
        """Return a detached CPU state suitable for model serialization."""

        return {
            name: value.detach().cpu().clone()
            for name, value in self.model.state_dict().items()
        }

    def set_local_model(self, model_state: Mapping[str, Any]) -> None:
        """Load an aggregated state before the next local training cycle."""

        if not isinstance(model_state, Mapping):
            raise TypeError("MNIST model state must be a mapping.")
        self.model.load_state_dict(dict(model_state), strict=True)


def _torch_components():
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
    except ImportError as exc:
        raise RuntimeError(
            "MNISTTrainer requires PyTorch. Install the WAFL-MLP runtime "
            "dependencies before enabling real local training."
        ) from exc
    return torch, nn, optim


def _observation_to_tensors(torch, observation: Any):
    payload = getattr(observation, "payload", None)
    if not isinstance(payload, bytes):
        raise TypeError("MNIST observations must contain a bytes payload.")
    if len(payload) != MNISTTrainer.OBSERVATION_SIZE:
        raise ValueError(
            "MNIST observation payload must contain 784 pixels and one label byte."
        )

    label = payload[-1]
    if label >= MNISTTrainer.LABEL_COUNT:
        raise ValueError("MNIST label must be between 0 and 9.")

    features = torch.tensor(
        list(payload[:-1]),
        dtype=torch.float32,
    ) / 255.0
    return features, label
