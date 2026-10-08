"""Compact read-only mobility trace storage for CLIVE.

The trace is stored once per run as a little-endian float64 binary matrix:
    [tick][node][x,y]
Every radio worker memory-maps the same file read-only, so the operating system can share
file-backed pages instead of every Python process deserializing its own nested dict copy.
"""
from __future__ import annotations

import hashlib
import mmap
import os
import struct
import tempfile
from typing import Dict, Iterable, List, Sequence

MAGIC = b"CLVMOB01"
VERSION = 1
HEADER = struct.Struct("<8sIII")  # magic, version, node_count, tick_count
FORMAT_NAME = "clive-mobility-f64le-v1"


def _expected_size(node_count: int, tick_count: int) -> int:
    return HEADER.size + node_count * tick_count * 2 * 8


def _frame_struct(node_count: int) -> struct.Struct:
    return struct.Struct("<" + ("d" * (node_count * 2)))


def trace_digest_from_dict(trace, node_names: Sequence[str]) -> str:
    ticks = len(trace)
    header = HEADER.pack(MAGIC, VERSION, len(node_names), ticks)
    frame_struct = _frame_struct(len(node_names))
    digest = hashlib.sha256(header)
    for tick in range(ticks):
        values = []
        positions = trace[tick]
        for name in node_names:
            x, y = positions[name]
            values.extend((float(x), float(y)))
        digest.update(frame_struct.pack(*values))
    return digest.hexdigest()


def write_trace(path: str, trace, node_names: Sequence[str]) -> dict:
    """Atomically write a dict-based trace to one compact binary file."""
    node_names = list(node_names)
    tick_count = len(trace)
    if tick_count <= 0 or not node_names:
        raise ValueError("mobility trace and node list must be non-empty")
    for tick in range(tick_count):
        if tick not in trace:
            raise ValueError(f"mobility trace missing tick {tick}")

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=parent, prefix=".clive-mobility-")
    frame_struct = _frame_struct(len(node_names))
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as stream:
            header = HEADER.pack(MAGIC, VERSION, len(node_names), tick_count)
            stream.write(header)
            digest.update(header)
            for tick in range(tick_count):
                positions = trace[tick]
                values = []
                for name in node_names:
                    x, y = positions[name]
                    values.extend((float(x), float(y)))
                row = frame_struct.pack(*values)
                stream.write(row)
                digest.update(row)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return {
        "format": FORMAT_NAME,
        "version": VERSION,
        "file": os.path.basename(path),
        "dtype": "float64-le",
        "node_count": len(node_names),
        "tick_count": tick_count,
        "byte_size": _expected_size(len(node_names), tick_count),
        "sha256": digest.hexdigest(),
    }


def validate_trace_file(path: str, metadata: dict, *, verify_digest: bool = False) -> None:
    node_count = int(metadata["node_count"])
    tick_count = int(metadata["tick_count"])
    expected = _expected_size(node_count, tick_count)
    actual = os.path.getsize(path)
    if actual != expected:
        raise ValueError(f"mobility trace size mismatch: expected {expected}, got {actual}")
    with open(path, "rb") as stream:
        raw = stream.read(HEADER.size)
    magic, version, file_nodes, file_ticks = HEADER.unpack(raw)
    if magic != MAGIC or version != VERSION:
        raise ValueError("unsupported mobility binary format")
    if file_nodes != node_count or file_ticks != tick_count:
        raise ValueError("mobility binary header does not match plan metadata")
    if metadata.get("format") != FORMAT_NAME:
        raise ValueError("unsupported mobility trace metadata format")
    if verify_digest:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != metadata.get("sha256"):
            raise ValueError("mobility binary digest mismatch")


class DictTraceReader:
    """Adapter used only before a prepared plan has been persisted."""
    def __init__(self, trace, node_names: Sequence[str]):
        self.trace = trace
        self.node_names = list(node_names)
        self.node_index = {name: i for i, name in enumerate(self.node_names)}
        self.tick_count = len(trace)

    def __len__(self):
        return self.tick_count

    def frame_flat(self, tick: int):
        positions = self.trace[tick]
        out = []
        for name in self.node_names:
            x, y = positions[name]
            out.extend((float(x), float(y)))
        return tuple(out)

    def position(self, tick: int, name: str):
        x, y = self.trace[tick][name]
        return float(x), float(y)

    def frame_dict(self, tick: int):
        return {name: list(self.trace[tick][name]) for name in self.node_names}

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class MappedTraceReader:
    """Read-only shared file mapping of one CLIVE mobility trace."""
    def __init__(self, path: str, metadata: dict, node_names: Sequence[str]):
        self.path = os.path.abspath(path)
        self.metadata = dict(metadata)
        self.node_names = list(node_names)
        self.node_index = {name: i for i, name in enumerate(self.node_names)}
        self.tick_count = int(metadata["tick_count"])
        self.node_count = int(metadata["node_count"])
        if self.node_count != len(self.node_names):
            raise ValueError("mobility trace node count does not match plan nodes")
        validate_trace_file(self.path, self.metadata, verify_digest=False)
        self._file = open(self.path, "rb")
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self._frame_struct = _frame_struct(self.node_count)
        self._frame_bytes = self._frame_struct.size
        self._cache_tick = None
        self._cache_frame = None

    def __len__(self):
        return self.tick_count

    def _check_tick(self, tick: int):
        if type(tick) is not int or not 0 <= tick < self.tick_count:
            raise IndexError(f"mobility tick out of range: {tick}")

    def frame_flat(self, tick: int):
        self._check_tick(tick)
        if tick == self._cache_tick:
            return self._cache_frame
        offset = HEADER.size + tick * self._frame_bytes
        frame = self._frame_struct.unpack_from(self._mmap, offset)
        self._cache_tick = tick
        self._cache_frame = frame
        return frame

    def position(self, tick: int, name: str):
        index = self.node_index[name] * 2
        frame = self.frame_flat(tick)
        return frame[index], frame[index + 1]

    def frame_dict(self, tick: int):
        frame = self.frame_flat(tick)
        result = {}
        for i, name in enumerate(self.node_names):
            j = i * 2
            result[name] = [frame[j], frame[j + 1]]
        return result

    def close(self):
        if getattr(self, "_mmap", None) is not None:
            self._mmap.close()
            self._mmap = None
        if getattr(self, "_file", None) is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
