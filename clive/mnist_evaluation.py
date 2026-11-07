"""Optional CLIVE-side final-model assessment; FLAME remains unchanged.

Accuracy uses the held-out 10,000-example MNIST test split, never training
samples. This runs only after a Cluster Head finishes its emulation and is
explicitly enabled by the scenario (so capacity demonstrations stay lightweight).
"""
from __future__ import annotations

from pathlib import Path


def evaluate_and_save(state, dataset_path, output_path, *, accuracy=False, save_model=False):
    if state is None:
        return {"status": "no_model"}
    import torch
    from flame.federated_learning.mnist_trainer import MNISTMLP

    model = MNISTMLP()
    model.load_state_dict(state, strict=True)
    result = {"status": "ok", "model_architecture": "WAFL-MLP(784,128,10)"}
    if accuracy:
        import numpy as np
        with np.load(dataset_path) as data:
            images = data["x_test"].copy()
            labels = data["y_test"].copy()
        model.eval()
        from classification_metrics import classification_metrics
        predicted_chunks = []
        with torch.no_grad():
            for start in range(0, len(labels), 512):
                x = torch.from_numpy(images[start:start+512].reshape(-1, 784).copy())
                x = x.float().div_(255.0)
                predictions = model(x).argmax(dim=1)
                predicted_chunks.append(predictions.cpu())
        stats = classification_metrics(labels.astype("int64"), torch.cat(predicted_chunks).numpy())
        result.update({"dataset": "MNIST held-out test split", **stats})
    if save_model:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        torch.save({name: tensor.detach().cpu() for name, tensor in state.items()}, target)
        result["weights_file"] = str(target.name)
    return result
