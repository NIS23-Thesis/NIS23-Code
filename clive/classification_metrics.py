"""Evaluation-only classification statistics. No FLAME training code is modified.

Every metric uses the same held-out predictions and includes all 10 MNIST classes.
Classes absent from the evaluated subset contribute zero to macro precision/recall/F1
(zero_division=0). Weighted F1 weights classes by their true-label support.
"""
from __future__ import annotations


def classification_metrics(truth, predictions, *, class_count=10):
    import numpy as np
    truth = np.asarray(truth, dtype=np.int64).reshape(-1)
    predictions = np.asarray(predictions, dtype=np.int64).reshape(-1)
    if truth.shape != predictions.shape or truth.size == 0:
        raise ValueError("Ground truth and predictions must be non-empty and equal length")
    if (truth < 0).any() or (predictions < 0).any() or (truth >= class_count).any() or (predictions >= class_count).any():
        raise ValueError("Class index outside expected range")
    matrix = np.bincount(truth * class_count + predictions,
                         minlength=class_count * class_count).reshape(class_count, class_count)
    true_positive = matrix.diagonal()
    support = matrix.sum(axis=1)
    predicted = matrix.sum(axis=0)
    # Define undefined precision/recall as zero, consistent across runs.
    precision = np.divide(true_positive, predicted, out=np.zeros(class_count, float), where=predicted != 0)
    recall = np.divide(true_positive, support, out=np.zeros(class_count, float), where=support != 0)
    f1 = np.divide(2 * precision * recall, precision + recall,
                   out=np.zeros(class_count, float), where=(precision + recall) != 0)
    total = int(matrix.sum())
    return {
        "accuracy": float(true_positive.sum() / total),
        "macro_precision": float(precision.mean()),
        "macro_recall": float(recall.mean()),
        "macro_f1": float(f1.mean()),
        "weighted_f1": float(np.dot(f1, support) / total),
        "per_class": [{"digit": i, "support": int(support[i]), "precision": float(precision[i]),
                       "recall": float(recall[i]), "f1": float(f1[i])} for i in range(class_count)],
        "confusion_matrix": matrix.tolist(),
        "correct": int(true_positive.sum()), "total": total,
        "zero_division": 0,
    }
