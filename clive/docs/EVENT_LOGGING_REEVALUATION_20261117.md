# CLIVE event logging — classifier correction and reevaluation

**Scope:** CLIVE visualizer and tests only; the team-owned `flame/` protocol source was not edited.

## Errors corrected

1. **Transfer family falsely fixed to MODEL_UPDATE.** `flame_transfer_complete` and `flame_transfer_failed` are *generic* reliable-transfer callbacks. The recorded `packet_type` is authoritative: `SENSOR_DATA` = 2; `MODEL_UPDATE` = 4. The visualizer now generates the title from the real type in both the live timeline and full archive. Records with no type are marked *type unrecorded*; invalid recorded values are marked *unknown type*, never inferred to be MODEL_UPDATE.
2. **Packet-family search/filter falsely inferred MODEL_UPDATE for unknown transfers.** Removed the generic transfer callbacks from the action-to-packet mapping in frontend JavaScript and backend Python. The packet filter works only with a recorded type for these callbacks.
3. **Backpressure dropped packets were not flagged as failures.** `flame_radio_backpressure_drop` now has a failure outcome in frontend and backend. The CLIVE origin remains unchanged: a transport/adapter failure does not become a FLAME protocol event.
4. **Sensing ownership was ambiguous.** `flame_sample_collected` stays under CLIVE because the CLIVE adapter writes the event. The event text now states that the adapter observed FLAME `SensingModule.collect_once()` invoked with a sample provided by the CLIVE data source. No packet emission is inferred.
5. **Outcome filter implied success.** Replaced “No failures” / “Without failures” with **Not flagged as failure**. An unflagged event is not confirmation of success.
6. **Two conflicting title renderers.** Full Event History now calls the same `CliveEventStream.displayTitle()` as the playback timeline. FLAME packet TX/RX remains distinct from FLAME Action.
7. **Stale map validator.** Replaced a legacy validation script for a removed group-of-moments API with event-by-event chronological playback assertions. Runtime map semantics were not changed.

## Verification

- **Full Python test suite:** 327 passed, 4 `fork()` deprecation warnings (Python 3.13 environment).
- **Frontend JavaScript validators:** 8 of 8 passed, including transfer type, packet provenance, failure filtering, and UI text safety.
- **Saved baseline's raw logs:** 30 `flame_transfer_complete` records: **20 SENSOR_DATA** (`packet_type=2`, SNs) and **10 MODEL_UPDATE** (`packet_type=4`, CH). API archive and recent-window filtering each return the correct 20/10 classification; an unknown-type transfer cannot match either family.
- **Extended `run_all.py` suite:** NodeSocket, mobility, emulator, visualizer, and end-to-end FLAME integration checks passed before the runner was interrupted by the environment timeout during `validate_control_center.py`. The *extended suite* is not claimed complete. Its test-launched workers were explicitly terminated after timeout.
- **FLAME code integrity:** SHA-256 comparisons against the starting ZIP confirm all 25 `flame/` files are byte-for-byte identical.

## Runtime process count

`Emulator.run()` starts **one `multiprocessing.Process` per configured node**, named `CLIVE-<node>`; `NodeSocket` then runs the radio path as either an independent process or a thread:

| `radio_execution` | Node-related OS processes | Additional threads |
|---|---:|---:|
| `process` (default) | 2 per node | None specifically for the radio worker |
| `thread` (explicit) | 1 per node | 1 radio thread per node |

The experiment additionally uses **one shared emulator/supervisor process**. If started from the Experiment Studio, the HTTP control server is another shared process, not a per-node worker. FLAME is loaded into the node process; there is **no additional FLAME process**. Other Python/BLAS/PyTorch internal threads are not counted above.

For 2 Cluster Heads and 4 Sensor Nodes (six virtual nodes), process mode has **12 node-related processes + 1 emulator process = 13 experiment processes**. Thread mode has **6 node processes + 1 emulator = 7 experiment processes**, plus six radio threads.

## Semantics and limitations

- FLAME Packet = a real decoded NodeSocket TX/RX record; packet RX is not proof of FLAME acceptance.
- FLAME Action = a logged FLAME protocol decision or submission; no packet TX is implied by `flame_hello_sent` alone.
- FLAME Internal = training, aggregation, reassembly or internal association state changes.
- CLIVE = runtime, dataset source, and adapter-origin diagnostics (even if the adapter calls a FLAME operation).
- Network = NodeSocket radio/kernel outcomes and failures. Network TX/RX inspection overlaps with FLAME Packet when the datagram is decoded as a FLAME packet.
- The recorded simulation timestamps may not prove causal ordering for simultaneous events in different worker processes.
- Missing packet logs cannot be reconstructed after a run.
- **Not flagged as failure** does not mean a transfer, association, or learning objective succeeded.
