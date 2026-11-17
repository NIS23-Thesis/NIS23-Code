"""On-disk class shards for memory-efficient non-IID MNIST sensor sampling.

The compressed npz is decoded once per dataset version, not once per Sensor Node.
Each shard preserves the original training-set order within its label.  Sensors
read only their selected 785-byte records; no torch or numpy import is required
in a Sensor Node process. This changes data loading, NOT sample selection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

SAMPLE_BYTES = 784
CACHE_VERSION = 2


def _source_signature(path: Path):
    # Fingerprint the file bytes rather than mtime (zip extraction changes mtime).
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _is_ready(cache: Path, fingerprint: str, *, verify_hashes: bool = True):
    try:
        manifest = json.loads((cache / 'manifest.json').read_text())
        if (manifest.get('version') != CACHE_VERSION or
                manifest.get('source_sha256') != fingerprint):
            return False
        counts = manifest['counts']
        sha_by_digit = manifest.get('shard_sha256')
        if not isinstance(sha_by_digit, dict) or len(sha_by_digit) != 10:
            return False
        if not all(
            type(counts.get(str(d))) is int and counts[str(d)] > 0 and
            (cache / f'digit-{d}.bin').stat().st_size == counts[str(d)] * SAMPLE_BYTES and
            isinstance(sha_by_digit.get(str(d)), str)
            for d in range(10)
        ):
            return False
        return (not verify_hashes or all(
            _source_signature(cache / f'digit-{d}.bin') == sha_by_digit[str(d)]
            for d in range(10)
        ))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_mnist_cache(archive_path, cache_path=None, *, verify_shards=True):
    """Return validated cache directory; safe for concurrent standalone callers on Unix."""
    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f'MNIST dataset missing: {archive}')
    cache = Path(cache_path) if cache_path else archive.parent / '.clive-mnist-cache'
    cache.mkdir(parents=True, exist_ok=True)
    # Cheap hot path: the supervisor verifies SHA-256 before launching children.
    # For standalone sensors, archive size/mtime and every shard size must match.
    stat = archive.stat()
    manifest_path = cache / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get('source_size') == stat.st_size and
                manifest.get('source_mtime_ns') == stat.st_mtime_ns and
                _is_ready(cache, manifest['source_sha256'], verify_hashes=verify_shards)):
            return cache
    except (OSError, ValueError, KeyError, TypeError):
        pass
    fingerprint = _source_signature(archive)

    # The lock is held only for generation, never for sensor sampling.
    import fcntl
    with (cache / '.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _is_ready(cache, fingerprint, verify_hashes=verify_shards):
            return cache
        # Import NumPy only in the creating process, and release the entire array
        # before the caller launches sensor workers.
        import numpy as np
        with np.load(archive) as dataset:
            labels = dataset['y_train']
            images = dataset['x_train']
            if images.dtype != np.uint8 or images.shape[1:] != (28, 28):
                raise ValueError('expected uint8 MNIST training images of shape Nx28x28')
            if len(labels) != len(images):
                raise ValueError('MNIST images and labels are misaligned')
            counts = {}
            shard_sha256 = {}
            for digit in range(10):
                selected = images[labels == digit]  # preserves original row ordering
                counts[str(digit)] = len(selected)
                raw = selected.tobytes(order='C')
                shard_sha256[str(digit)] = hashlib.sha256(raw).hexdigest()
                fd, temp = tempfile.mkstemp(prefix=f'.digit-{digit}-', dir=cache)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp, cache / f'digit-{digit}.bin')
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            del images, labels
        meta = {'version': CACHE_VERSION, 'source_sha256': fingerprint, 'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns, 'counts': counts, 'shard_sha256': shard_sha256}
        fd, temp = tempfile.mkstemp(prefix='.manifest-', dir=cache)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(meta, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, cache / 'manifest.json')  # commit marker LAST
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return cache


def class_samples(archive_path, digit, rng, count, *, cache_path=None):
    """Read the same random class-index sequence used by the original _MNISTSource."""
    # The supervisor verifies every shard. Sensor workers check only the shard
    # they sample, avoiding ten full-file hashes in every Sensor Node process.
    cache = prepare_mnist_cache(archive_path, cache_path, verify_shards=False)
    manifest = json.loads((cache / 'manifest.json').read_text())
    n = manifest['counts'][str(digit)]
    shard = cache / f'digit-{digit}.bin'
    if _source_signature(shard) != manifest['shard_sha256'][str(digit)]:
        raise OSError(f'MNIST shard integrity mismatch for digit {digit}')
    if count > n:
        raise ValueError(f"Requested {count} unique class-{digit} images, but only {n} exist")
    # Without replacement: source exhaustion now means unique MNIST records,
    # not just a finite number of draws that may contain hidden duplicates.
    picks = rng.sample(range(n), count)
    with (cache / f'digit-{digit}.bin').open('rb') as stream:
        result = []
        for index in picks:
            stream.seek(index * SAMPLE_BYTES)
            image = stream.read(SAMPLE_BYTES)
            if len(image) != SAMPLE_BYTES:
                raise IOError('truncated MNIST class shard')
            result.append(image + bytes([digit]))
    return result
