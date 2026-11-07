# CLIVE node-tracking integration update — 2026-11-04

## 399-fragment question

The FLAME MNIST MLP architecture is FC(784,128)-ReLU-FC(128,10): **101,770 float32 parameters**, **407,080 B of raw weights**. This exact integrated FLAME serialization produces **408,445 B**, split into **399** blocks of **1,024 B**. These are MODEL_UPDATE data fragments, excluding ACKs, retransmissions, headers, and other packets. FLAME defines the 1024-byte maximum; the count varies with model-state size and serialization.

## Source ownership and integrity

All edits in this update are under `clive/`; `flame/` must match `NIS23-CLIVE-Only-Milestone-Reviewed-20261104.zip` exactly. A byte-level comparison is recorded when building the final release. This update does not change the FLAME ACK/window/reassembly model, ML model, UDP packet formats or training behavior.

## Changes

- The Node inspector now shows the latest JOIN target, accepted CH, CH-side accepted data, member relationships, peer model activity and explicit LEAVE/timeout events.
- Radio reachability is shown **separately** from logical association, including directional reachability and the last two-way radio transition calculated from the canonical mobility trace.
- Added a compact connection log when detailed protocol logging is disabled, enabling the tracker for thesis runs without full packet JSONL overhead.
- Expanded normalized relationship telemetry and Event Stream classification. Connection-log records are protocol events, not TRANSMIT events.
- Added a dedicated, testable JavaScript state machine with replay-backward support and durable JOIN/LEAVE history.
- Corrected packet help text for `SENSOR_DATA_ACK` and `MODEL_UPDATE_ACK`; removed an old obsolete configuration label that incorrectly stated geographic MNIST grouping was absent.
- Added backend and frontend connection-tracking regression tests, and documented the evidence and remaining limitations.

## Test evidence

- FLAME tests: **183 / 183 passed** (source not edited).
- CLIVE `validate_visualizer.py`, `validate_visualizer_links.py`, compact connection provenance tests, Event Stream JavaScript tests: **passed**.
- CLIVE six-node acceptance test: **passed**; all eight FLAME packet types and training/aggregation present.
- Real six-node synthetic smoke with `protocol_events=false`, `packet_events=false`: **passed** and generated compact logs with 198 connection events (including JOIN/LEAVE/model activity).
- Real 7-second MNIST/PyTorch smoke with compact logging: **passed**, 2 CHs had 8/8 sender-completed model transfers each and 0 failed transfers.
- Browser-level visual regression exercised actual canvas-node click and replay-state transitions from a real saved run; **passed with no page JavaScript errors** using locally injected API responses. Direct HTTP navigation in headless Chromium was blocked by environment policy, so this is not a test of direct browser network navigation.
- `validation/run_all.py` did **not** complete end-to-end in this environment because `validate_control_center.py` hung during its HTTP lifecycle test; individual other validations were run separately. Do not claim full suite success.

## Interpret the connection states carefully

The tracker cannot certify immediate, persistent connectivity under MANET mobility. It can identify observed FLAME JOIN/LEAVE/timeout transitions and separately show instantaneous modeled link reachability. Historical logs may be truncated under extreme scale; no log means **unknown**, not **disconnected**. It is not a FLAME protocol fix.
