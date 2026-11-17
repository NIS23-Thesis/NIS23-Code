"""Optional CLIVE observability for actual 785-byte MNIST training observations.

Each record is a snapshot of a real sample collected or accepted by the existing
FLAME path; the wire payload is never modified. We store source and receipt
separately so the UI cannot mistake local collection for confirmed delivery.
These are *training* images, not MNIST's held-out test images.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path


def sample_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class SampleHistory:
    def __init__(self, results_dir, node_name: str):
        target = Path(results_dir) / 'flame' / f'{node_name}-samples.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self.node_name = node_name
        self.generated = 0

    def record(self, event: str, payload: bytes | None = None, **details) -> None:
        row = {'event': event, 'node': self.node_name, **details}
        if payload is not None:
            if not isinstance(payload, bytes) or len(payload) != 785 or payload[784] > 9:
                raise ValueError('Only actual 784-byte MNIST images with a 0-9 label may be captured')
            row.update(digit=payload[784], sha256=sample_digest(payload),
                       pixels_b64=base64.b64encode(payload[:784]).decode('ascii'))
        with self.path.open('a', encoding='utf8') as out:
            out.write(json.dumps(row, separators=(',', ':')) + '\n')
