"""Integration-only tests: geographic dominant classes and unique MNIST observations."""
import random
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from mnist_topology import mixed_digit_labels, spatial_digit
from mnist_cache import mixed_class_samples
from collections import Counter
from flame_protocol import _MNISTSource, DEFAULTS

cfg = dict(DEFAULTS, sample_source='mnist', mnist_samples_per_sensor=128,
           mnist_distribution='spatial_clusters', mnist_dominant_fraction=0.75)
src = _MNISTSource('SN-18', 104, cfg, initial_position=(21, 43), area_size=(200, 200))
assert src.digit == spatial_digit((21,43),(200,200))
assert len(src.samples) == 128
assert src.label_counts[src.digit] == 96
assert sum(src.label_counts.values()) == 128
assert len(set(src.samples)) == 128, 'No repeated MNIST images in a per-sensor pool'
assert len(src.label_counts) >= 2
assert all(len(sample) == 785 and sample[-1] in range(10) for sample in src.samples)
repeat = _MNISTSource('SN-18', 104, cfg, initial_position=(21,43), area_size=(200,200))
assert src.samples == repeat.samples, 'Must be reproducible across independent runs'
assert src.label_counts == repeat.label_counts
other = _MNISTSource('SN-18', 104, cfg, initial_position=(155,143), area_size=(200,200))
assert other.digit != src.digit, 'Geographic zones must determine dominant class'
assert other.label_counts[other.digit] == 96
labels = mixed_digit_labels(5, 128, .75, random.Random(7))
assert Counter(labels)[5] == 96 and len(labels) == 128
cfg['sample_source'] = 'synthetic'
syn = _MNISTSource('SN-18', 104, cfg, initial_position=(21,43), area_size=(200,200))
assert syn.label_counts[syn.digit] == 96
print('PASS: seeded 75/25 non-IID MNIST samples, no duplicates, 785-byte wire records')
