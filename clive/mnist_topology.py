"""Deterministic, geographically clustered MNIST assignment for CLIVE.

The 200/350/500 m thesis areas are divided into ten contiguous label zones
(5 columns x 2 rows). Adjacent sensors in the same zone observe the same digit,
creating spatially local, non-IID data without changing FLAME packet contents.
An SN retains its initial label while moving: this represents a sensor carrying
its localized dataset, not re-labeling an image when crossing a boundary.
"""
from __future__ import annotations

import math


def spatial_digit(position, area_size):
    """Return a MNIST digit (0..9) from an initial (x,y) coordinate."""
    x, y = map(float, position)
    width, height = map(float, area_size)
    if not all(map(math.isfinite, (x, y, width, height))) or width <= 0 or height <= 0:
        raise ValueError("Position and area must be finite, with positive dimensions")
    if not (0 <= x <= width and 0 <= y <= height):
        raise ValueError("Sensor initial position lies outside the configured area")
    column = min(int((x / width) * 5), 4)
    row = min(int((y / height) * 2), 1)
    return row * 5 + column
