# CLIVE event log views (17 November 2026)

The visualizer preserves original on-disk JSONL records. Grouping is a UI/API classification only; no FLAME protocol or packet semantics are changed.

| Display group | Source and meaning |
| --- | --- |
| FLAME · Packet | **Only** a decoded HELLO/JOIN/JOIN_ACCEPT/LEAVE/SENSOR_DATA/ACK/MODEL_UPDATE datagram that NodeSocket actually recorded at its `transmit` or `receive` boundary. TX is not proof of receiver acceptance; RX is not proof FLAME accepted the message. |
| FLAME · Action | CLIVE adapter observations of protocol outcomes: submitting HELLO/JOIN to the communication queue, processing JOIN_ACCEPT, CH discovery, accepted SENSOR_DATA, model transfer and ACK completion, etc. A related packet name is metadata, not an extra wire packet. |
| FLAME · Internal | Reassembly, training, aggregation, membership timeouts, and other local protocol state transitions observed by CLIVE's adapter. |
| CLIVE | Node process lifecycle, dataset/sample provision, adapter retry bookkeeping, emulator diagnostics. |
| NETWORK | NodeSocket transport-level packet events including modeled drops and socket errors. The `Network · TX/RX/drops` filter includes decoded FLAME packet TX/RX for network troubleshooting; group badges remain FLAME · Packet for those actual datagrams. |

**Highlights** shows a compact set of relevant FLAME actions/internal events, not routine packet flood or startup. **Failures / drops** can find both FLAME errors and network drops while preserving each record's actual group.

The full Event History provides global simulation-time sorting (oldest/newest), time range, node (producer *or* known endpoint), packet family (including related actions), outcome and free-text search. These filters are applied on disk **before** page limits. Equal-time events use a stable per-file tie-break for pagination. The live playback preview shows an eight-second time window and supports the same category/packet/node/outcome filters with a latest/earliest order. Raw events remain available under *All events (advanced)*.

A packet sequence number is shown as `seq` only on a recorded NodeSocket packet event. A sequence stored with a FLAME Action is labeled `ref seq` or `trigger seq`, not invented as a new transmission. If packet JSONL logging was disabled, the wire TX/RX stages will not appear; never infer or reconstruct them from the presence of related actions.

Examples to demonstrate: `HELLO submitted` (FLAME Action) → `HELLO · Sent` (FLAME Packet) → `HELLO · Received` (FLAME Packet) → `Cluster Head discovered` (FLAME Action) → `JOIN submitted` (FLAME Action) → `JOIN · Sent` (FLAME Packet) → `JOIN_ACCEPT · Received` (FLAME Packet) → `Associated after JOIN_ACCEPT` (FLAME Action). Actual order and completeness depend on the recorded scenario and available logs.
