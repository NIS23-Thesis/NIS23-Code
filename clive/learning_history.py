"""CLIVE-side evaluation snapshots for a panel-friendly learning-history page.

This is observational: it reads FLAME model state without changing FLAME training,
aggregation, weighting, or packet algorithms. A fixed *held-out test subset* is used
for periodic measurements; full 10,000-image evaluation remains separate.
"""
from __future__ import annotations
import json
from pathlib import Path

class LearningHistory:
    def __init__(self, dataset_path, output_path, *, seed, samples=512):
        import numpy as np
        import torch
        from flame.federated_learning.mnist_trainer import MNISTMLP
        count = int(samples)
        if not 32 <= count <= 10000:
            raise ValueError('learning_eval_samples must be between 32 and 10,000')
        with np.load(dataset_path) as ds:
            total = len(ds['y_test'])
            indices = np.random.default_rng(int(seed)).choice(total, size=count, replace=False)
            images = ds['x_test'][indices].reshape(count, 784).copy()
            labels = ds['y_test'][indices].astype('int64').copy()
        # A fixed, reproducible example for each true digit (0–9). These
        # examples are from the held-out test split, never used for training.
        # Their softmax scores illustrate model preference, not calibrated odds
        # that a prediction is correct.
        with np.load(dataset_path) as ds:
            test_labels = ds['y_test'].astype('int64')
            test_images = ds['x_test']
            chosen = [int(np.flatnonzero(test_labels == digit)[0]) for digit in range(10)]
            examples = test_images[chosen].reshape(10, 784).copy()
        self.examples = torch.from_numpy(examples).float().div_(255.0)
        self.inputs = torch.from_numpy(images).float().div_(255.0)
        self.labels = torch.from_numpy(labels)
        self.model = MNISTMLP().eval()
        self.target = Path(output_path)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.count = count

    def record(self, model_state, *, node, time_s, revision, cause, observations,
               revision_context=None):
        import torch
        if model_state is None:
            return None
        self.model.load_state_dict(model_state, strict=True)
        self.model.eval()
        from classification_metrics import classification_metrics
        predictions = []
        with torch.no_grad():
            for start in range(0, self.count, 256):
                predictions.append(self.model(self.inputs[start:start+256]).argmax(dim=1).cpu())
        stats = classification_metrics(self.labels.cpu().numpy(),
                                       torch.cat(predictions).numpy())
        with torch.no_grad():
            example_probs = torch.softmax(self.model(self.examples), dim=1).cpu().numpy()
        # Each row: one fixed test image whose true digit is 'digit'.
        example_confidence = [
            {'true_digit': digit, 'predicted_digit': int(row.argmax()),
             'confidence': [round(float(p), 7) for p in row]}
            for digit, row in enumerate(example_probs)
        ]
        record = {'node': str(node), 'time_s': float(time_s),
                  'revision': int(revision), 'cause': str(cause),
                  'observations_retained': int(observations),
                  'accuracy': stats['accuracy'], 'correct': stats['correct'],
                  'macro_precision': stats['macro_precision'], 'macro_recall': stats['macro_recall'],
                  'macro_f1': stats['macro_f1'], 'weighted_f1': stats['weighted_f1'],
                  'per_class': stats['per_class'], 'confusion_matrix': stats['confusion_matrix'],
                  'example_confidence': example_confidence,
                  'evaluated_samples': self.count,
                  'dataset': 'MNIST held-out test subset (fixed seed)',
                  'measurement': 'sampled_test_accuracy'}
        if revision_context:
            record.update(revision_context)
        with self.target.open('a', encoding='utf8') as out:
            out.write(json.dumps(record, separators=(',', ':'))+'\n')
        return record
