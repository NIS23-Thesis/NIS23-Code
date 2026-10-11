"""Read and summarize a FLAME simulation model checkpoint.

This reader is intended for checkpoint files produced by ``run_node.py``.
Only open checkpoint files that you trust because Python pickle files can
execute code while being loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import pickle
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _checksum(value: Any) -> str:
    serialized = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    return hashlib.sha256(serialized).hexdigest()[:16]


def _scalar(value: Any) -> Any:
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError, RuntimeError):
            pass
    return value


def _tensor_summary(value: Any) -> str | None:
    if not hasattr(value, "shape"):
        return None

    shape = tuple(int(dimension) for dimension in value.shape)
    parts = [f"shape={shape}"]
    dtype = getattr(value, "dtype", None)
    if dtype is not None:
        parts.append(f"dtype={dtype}")

    try:
        numeric = value.detach().cpu() if hasattr(value, "detach") else value
        if hasattr(numeric, "numel") and numeric.numel() == 0:
            return ", ".join(parts + ["empty"])
        minimum = _scalar(numeric.min())
        maximum = _scalar(numeric.max())
        mean = _scalar(numeric.float().mean() if hasattr(numeric, "float") else numeric.mean())
        parts.extend(
            [
                f"min={float(minimum):.6g}",
                f"max={float(maximum):.6g}",
                f"mean={float(mean):.6g}",
            ]
        )
    except (TypeError, ValueError, RuntimeError, AttributeError):
        pass

    return ", ".join(parts)


def _print_state(value: Any, path: str = "model_state", indent: int = 2) -> None:
    prefix = " " * indent
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            summary = _tensor_summary(item)
            if summary is not None:
                print(f"{prefix}{item_path}: {summary}")
            elif isinstance(item, Mapping):
                print(f"{prefix}{item_path}:")
                _print_state(item, item_path, indent + 2)
            else:
                print(f"{prefix}{item_path}: {item!r}")
        return

    summary = _tensor_summary(value)
    print(f"{prefix}{path}: {summary or value!r}")


def read_checkpoint(path: Path) -> dict[str, Any]:
    with path.open("rb") as checkpoint_file:
        checkpoint = pickle.load(checkpoint_file)
    if not isinstance(checkpoint, dict) or "model_state" not in checkpoint:
        raise ValueError("File is not a FLAME simulation model checkpoint.")
    return checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read a FLAME simulation model_state.pkl checkpoint."
    )
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()

    checkpoint = read_checkpoint(args.checkpoint)
    model_state = checkpoint["model_state"]
    actual_checksum = _checksum(model_state)

    print(f"Checkpoint: {args.checkpoint}")
    print(f"Event: {checkpoint.get('event', '-')}")
    print(f"Timestamp: {checkpoint.get('timestamp', '-')}")
    print(f"Source: {checkpoint.get('source', '-')}")
    print(f"Model ID: {checkpoint.get('model_id', '-')}")
    print(f"Update ID: {checkpoint.get('update_id', '-')}")
    print(f"Observations: {checkpoint.get('observations', '-')}")
    print(f"Weight checksum: {actual_checksum}")
    stored_checksum = checkpoint.get("weight_checksum")
    if stored_checksum is not None:
        print(f"Checksum matches checkpoint: {stored_checksum == actual_checksum}")
    print("Model state:")
    _print_state(model_state)


if __name__ == "__main__":
    main()
