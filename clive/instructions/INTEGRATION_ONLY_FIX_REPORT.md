# CLIVE–FLAME integration-only repair — November 4, 2026

**Starting archive:** `NIS23-Code-integration(1).zip` (the user's latest upload).  
**Mutation boundary:** `flame/` is **byte-for-byte identical** to the starting archive. Nothing was changed in the original standalone validation archive either. Patches are restricted to CLIVE's adapter, event viewer, controller, scenario description, reporting, and integration tests.

## Summary

This is an integration/diagnostics improvement, **not a fix to the FLAME reliability implementation**. Existing FLAME AIMD-window, partial-ACK retry, 8-bit revision, aggregation, and untrusted-pickle issues remain, and an FLAME team member should own their resolution. An integration-only model resend policy may reduce one-shot peer-delivery failures, but **cannot** repair FLAME's underlying ACK-loss deadlock. Avoid using uncorrected ACK-loss results as final protocol performance evidence.

## What changed in this edition

| Area | Concrete modification | Why |
|---|---|---|
| Visualizer event normalization (`clive/visualizer.py`) | Preserve protocol-level `sequence_number`, `packet_type`, `message_id`, `update_id`, fragment count, and input-trigger sequence. Keep `seq` as a **separate viewer record ID**. Correct the source/destination of reassembly and rejected-input events. Sort by experiment time before displaying a limited window. | Packet-related protocol events previously lost their wire identity and event timestamps looked disordered across node logs. |
| Visualizer Event Stream (`clive/visualizer/static/`) | Replaced uninformative event codes with plain-English titles/explanations, glossary of TRANSMIT/RECEIVE/DROP/PROTOCOL, key-events default, technical-view toggle, time-window selector, node/packet search, and raw-field Inspect details. | Operators can explain the four categories and correlate a packet with protocol processing without treating model-training actions as packets. |
| Packet naming | `SENSOR_ACK` → `SENSOR_DATA_ACK`; `MODEL_ACK` → `MODEL_UPDATE_ACK`. | Matches the actual FLAME PacketType enum. |
| CLIVE–FLAME adapter (`clive/flame_protocol.py`) | Mark whether a sensor enqueue actually succeeded and report enqueue failures rather than silently claiming success. | Previously ignored the Boolean returned by FLAME's `try_enqueue_message`. |
| CLIVE–FLAME adapter | For applicable receive/join events, record the original FLAME packet sequence. For training/aggregation, record `trigger_sequence_number` instead of inventing a packet sequence on a non-packet operation. | Accurate diagnostic provenance. |
| Model resend at CLIVE adapter | Track sender-reported model completion/failure per CH peer and model version, allow bounded retries after failure and backoff, and expose final pending/completed/failed counts. Default: **two adapter-level reattempts**, initial backoff 2 seconds. | Prevents the adapter's prior permanent “already sent” flag from suppressing all retry after a failed first attempt. Retry is not a transport fix, and may increase bandwidth. |
| MNIST integration source | By default, stop sampling when the configured per-sensor pool has been used once. `repeat_sensor_samples: true` explicitly enables reuse for deliberate transport stress tests. | Avoid silently treating repeated examples as new observations. |
| `flame-mnist-pytorch-training-2ch-2sn.json` | Increase per-sensor sample pool to 64 for the short MNIST demo. | Real training demo now has sufficient distinct source observations for its accelerated interval. |
| CLIVE experiment metadata (`clive/emulator.py`) | Keep legacy `success` meaning *worker completion*. Add `execution_success`, `protocol_success` and `flame_outcome` with real/proxy workload flag and model-delivery started/completed/failed/unfinished counts. | A run with model-transfer failures can exit successfully; status must not imply ML correctness. |
| Control Center | Persistent workload label identifying **actual MNIST/PyTorch training** vs **non-ML mean-pixel proxy**; explicit confirmation before starting a proxy test, richer final result cards; Boolean settings are edited as checkboxes. | Prevent 220-node capacity/proxy tests from being presented as real federated learning. |
| Visualizer metric terminology | Identify modeled TX energy and per-recipient drop ratio; warn that broadcast fanout invalidates aggregate RX/TX as PDR. | Avoid incorrectly interpreting measurement denominators. |
| Test suite | Added `clive/validation/validate_integration_repair.py` with six regression tests for normalized event IDs, trigger IDs, sample repeat policy, retry bounds and proxy/outcome labels. | Regression protection without modifying `flame/`. |

## Four event categories, exactly

- **TRANSMIT:** CLIVE emitted a UDP datagram. `attempt` and `admit` are lower-level *pretransmit* technical events; the visualizer explicitly labels these as attempted/queued, not emitted.
- **RECEIVE:** NodeSocket delivered the UDP datagram. That alone does **not** confirm application acceptance.
- **DROP:** CLIVE could not deliver or queue a datagram, or a FLAME operation failed/rejected an input. The reason distinguishes radio loss from a protocol rejection.
- **PROTOCOL:** FLAME application-side actions (joined, reassembled, trained, aggregated, transfer completed/failed). Only packet-associated events have `sequence_number`; actions such as training have a **trigger** sequence when their initiating observation is known.

The visualizer's `seq` is a sequential **event record ID**, not FLAME's 16-bit sequence number. New table columns show FLAME packet sequence separately. Two technical phases (`attempt`, `admit`) are grouped under the outward-traffic category but explicitly named in Inspect.

## Actual proof from tests of this edition

- `python -m pytest -q tests`: **183 passed**.
- `python clive/validation/validate_integration_repair.py`: **6 passed**.
- `python clive/validation/validate_visualizer.py`: **PASS**.
- `python clive/validation/validate_visualizer_links.py`: **PASS**.
- `python clive/validation/validate_control_center.py`: **5 passed**.
- `python clive/validation/validate_configs.py`: **31 scenario files validated**.
- `python clive/validation/validate_flame_integration.py`: **6-node packet/association/training/aggregation acceptance PASS**.
- `python clive.py --scenario configs/demos/flame-mnist-pytorch-training-2ch-2sn.json`: fresh real MNIST/PyTorch run, 2 Cluster Heads and 2 Sensor Nodes, **31/31 sender-confirmed model transfers**, 0 failed or unfinished, 31 local training events and 31 aggregation events **across Cluster Heads**. These results describe **this one short run** and do **not** demonstrate convergence, long-duration reliability or 220-node neural-network scalability.
- Backend visualizer normalization queried against that real run and confirmed both real `sequence_number` on `flame_sensor_received` and distinct `trigger_sequence_number` on `flame_trained`.
- `node --check` passed on both visualizer and control-center scripts.
- Browser integration render verification **not completed**: Chromium in the test container blocked navigating to loopback and even locally mocked browser pages by administrator policy. API checks and standalone JavaScript syntax checks succeeded. A user-side visual inspection is still required.

## Important unresolved items — require FLAME owner or a research-design decision

### FLAME-owned, deliberately untouched

1. Sender/receiver window-size divergence on lost ACKs; partial-progress retry exhaustion (FLAME reliability manager).
2. Eight-bit model revision limit at 255 and valid versioning/wrap behavior (FLAME model manager).
3. Unsafe pickle model-state parsing and incompatible model-state aggregation poisoning (FLAME FL module).
4. No aggregation-window deadline, duplicate retention limitation, unbounded fragment inbound groups, and FLAME's own buffer/timeout behavior.
5. FLAME's training threshold uses total historical samples; local training may repeat too frequently without research-controlled gating.

### CLIVE/emulator and evaluation limitations not changed in this conservative patch

1. MNIST digit selection remains based on Sensor Node ID modulo 10; **spatially clustered non-IID data placement is NOT implemented**. Correcting it requires a documented choice of geographic distribution and association strategy.
2. CH choice is determined by address ordering among discovered compatible Cluster Heads, **not link proximity**. Do not substitute omniscient emulator positions in the protocol without describing the design change.
3. 220-node `capacity-220-nodes-transport-only-no-neural-training.json` uses `trainer: mean_pixel` and is too short to reach its training threshold. It measures node/transport capacity, **not neural-network FL scalability**. Control Center now warns explicitly.
4. The full thesis experiment permutation matrix and observation-frequency factors are not supplied by the 31 existing scenario presets. A reproducible generator with distinct seeds and an analysis pipeline is still needed.
5. The current energy and PDR accounting require a dedicated statistical-method/physics calibration review. No new radio-physics model was imposed during this integration-focused patch.
6. Standard runs do not compute final held-out MNIST accuracy or demonstrate convergence against the WAFL baseline; the new model-delivery status only reports ACKs.
7. Multi-host/LAN results, prolonged 5-minute/220-node runs and full browser UI verification are not established by this release.

## Existing differences between the user's two supplied FLAME trees

Comparing the **incoming integration ZIP's `flame/`** against `NIS23-Code-flame-protocol-validation (1).zip` shows **two pre-existing** nonidentical Python files:

1. `flame/communication/communication_module.py`: the integration previously moved a valid first-SENSOR_DATA membership promotion before the application delivery callback. This fixed the first accepted sensor observation being dropped at the adapter. The standalone validation file has the older ordering.
2. `flame/federated_learning/__init__.py`: the integration previously replaced eager trainer imports with a module-level `__getattr__` lazy import to avoid loading PyTorch in sensor-only workers. The standalone validation file imports `MNISTTrainer` eagerly.

**This release did not modify either of those files**. If absolute source parity to the original standalone validation ZIP is a milestone requirement, resolve the two inherited differences with the FLAME maintainers before merging. No new FLAME divergence is introduced here.

## Run instructions

```bash
# From the extracted NIS23-Code-integration directory
./start_gui.sh
# Experiment Studio and read-only visualizer are local-only by default.
# Load demos/flame-mnist-pytorch-training-2ch-2sn for REAL PyTorch / MNIST, or
# demos/capacity-220-nodes-transport-only-no-neural-training for NON-ML node capacity.

# For terminal demo:
cd clive
python clive.py --scenario configs/demos/flame-mnist-pytorch-training-2ch-2sn.json --results results

# Integration-only regression:
PYTHONPATH=.:.. python validation/validate_integration_repair.py
```

**Compatibility:** Old JSONL event files remain readable. Existing metrics `success` field still means *process completion*; new outcome fields are additional. Events themselves are never emitted by the visualizer and do not advance protocol timers.
