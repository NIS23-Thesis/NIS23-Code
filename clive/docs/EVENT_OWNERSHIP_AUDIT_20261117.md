# Event classification and provenance — November 17, 2026

This is a visualizer-only change. **No files in `flame/` have been changed.**
The log `stream` describes how CLIVE saved the record, not necessarily which
subsystem owns the behavior. Every `flame_`-prefixed event is recorded through
CLIVE's adapter, and not all such events are protocol transitions.

| Example recorded event | New displayed group | What it establishes |
|---|---|---|
| `started` | CLIVE | Node worker started; not a wire packet |
| `flame_started` | CLIVE | CLIVE adapter initialized FLAME runtime; not protocol traffic |
| `flame_sample_collected` | CLIVE | CLIVE's sample source submitted an observation |
| `flame_model_retry_scheduled` | CLIVE | Adapter retry bookkeeping, not team FLAME retransmission logic |
| `flame_radio_backpressure_drop` | CLIVE | CLIVE send-queue backpressure |
| `flame_ch_discovered` | FLAME | HELLO reception observed at protocol layer; does not imply joining |
| `flame_join_sent` | FLAME | JOIN submitted; not proof of acceptance |
| `flame_joined` | FLAME | Association accepted by node processing JOIN_ACCEPT |
| `flame_sensor_received` | FLAME | CH accepted SENSOR_DATA, not merely NodeSocket RX |
| `flame_trained` | FLAME | CH performed local training; not a packet |
| `flame_model_received` | FLAME | MODEL_UPDATE buffered; does not prove aggregation |
| `flame_aggregated` | FLAME | Aggregation applied at CH |
| `flame_transfer_failed` | FLAME (failed) | FLAME transfer failure, **not** radio packet loss |
| `transmit`, `receive` | TX, RX | CLIVE NodeSocket network observations |
| `delivery_drop`, `local_drop` | DROP | CLIVE network packet drop |

A separate **Failures and drops** filter collects recorded failure outcomes across
all of these groups, without changing the source badges. Explicitly deduplicated
model updates (`rejection_kind=duplicate`) do not count as failures.

## Available views

- **FLAME activity:** significant FLAME behavior observed through CLIVE's adapter
- **CLIVE lifecycle / adapter:** worker startup, adapter initialization, sample source and diagnostics
- **Packets sent:** NodeSocket TX and local drop (transmission is not receiver acceptance)
- **CLIVE network:** observed TX, RX, and network packet loss
- **Failures and drops:** cross-source outcome filter
- **All events:** includes packet queue/kernel diagnostics and detailed protocol records

## Validation

- All 38 named `_record()` event types in `clive/flame_protocol.py` were manually
  checked against the explicit origin categories.
- Confirmed category filtering in both the indexed playback preview and the
  paginated event archive using real saved CLIVE runs and regression fixtures.
- Python regression suite and JS presentation assertions must pass before use.

No events were added, suppressed, generated, or rewritten in experiment logs.
This affects only classification, filter selection, explanatory text, and styling.
