# Reading CLIVE × FLAME (integration-only)

This guide covers **CLIVE's adapter and viewer**, not FLAME's implementation.
**Never edit `flame/` while making CLIVE-only changes.** Packet formatting,
ACK/retry state, membership rules, and model training come from FLAME's own code.

## Where to start

| I want to understand... | Read this file / function | What it does |
| --- | --- | --- |
| What happens on every simulated tick? | `clive/flame_protocol.py` → `on_timer()` | Calls four short helpers in a fixed order: HELLO, sensor sample, LEAVE, communication servicing |
| How does a packet enter FLAME? | `flame_protocol.py` → `on_datagram()` | Passes real datagram bytes into FLAME's `CommunicationModule` and drains its parsed queue |
| Who processes JOIN, SENSOR_DATA, MODEL_UPDATE? | `flame_protocol.py` → `_on_message()` | Routes a parsed/reassembled packet to one role-specific handler |
| Who starts model transfers? | `flame_protocol.py` → `_collect_model_update()` → `_send_pending_models()` | Sends complete model states to compatible, reachable CH candidates |
| Why is a transfer retried? | `flame_protocol.py` → `_on_comm_event()` → `_track_model_transfer()` | Observes FLAME's completed/failed result and schedules bounded adapter-level reattempts; does *not* alter FLAME ACKs |
| Where do GUI records come from? | `clive/visualizer.py` → `RunData._normalize_event()` | Reads per-node JSONL logs and adds display fields (`seq` record ID, source, target, packet sequence) |
| Why are events out of time order in the files? | `RunData.events()` | Per-node logs arrive in independent file order; the API sorts records by **experiment time**, then record ID |
| What do TRANSMIT, RECEIVE, DROP, PROTOCOL mean? | `clive/visualizer/static/event_stream.js` | Pure, independently testable event classification and explanation; never sends packets |
| Where are the event controls? | `clive/visualizer/static/app.js` → `renderEventTable()` | Reads UI filters, asks `CliveEventStream.render()` for table HTML, then updates the DOM |

## Follow one sensor observation

1. SN joins CH via FLAME's `JOIN` and `JOIN_ACCEPT` packets.
2. SN collects an MNIST observation (or synthetic observation if explicitly configured).
3. CLIVE's adapter queues `SENSOR_DATA`; queueing is **not** successful delivery.
4. `NodeSocket` emits a UDP datagram (`transmit`). CLIVE may record **multiple** recipient deliveries or losses if this was a broadcast.
5. The destination's `NodeSocket` obtains a datagram (`receive`).
6. FLAME checks packet, fragments, ACKs, and association. If complete and valid, CLIVE records `flame_sensor_received` (`protocol`).
7. When FLAME's training condition is met, `flame_trained` records a *model operation*; no new wire packet sequence number exists for training itself.
8. Cluster Heads may send `MODEL_UPDATE` fragments and record transfer completion or failure.

## Event identifiers: do not confuse these

- **Record ID (`seq`)**: visualizer's index in a merged log. Useful for Inspect or incremental reads. Not transmitted over the network.
- **Packet sequence (`sequence_number`)**: the actual FLAME packet header's sequence number, if the logged event belongs to a wire packet.
- **Trigger sequence (`trigger_sequence_number`)**: original packet that caused an application action such as training. It is not the training event's own wire sequence.
- **Model revision (`update_id`)**: model update progress and/or payload identity. It is not the packet sequence.
- **Message ID (`message_id`)**: identifier associated with an acknowledged fragmented transfer.

## Four event groups

- **TRANSMIT** — outgoing radio/datagram lifecycle events. Technical events such as `attempt`, `admit` and `scheduled_delivery` are **not proof of successful radio delivery**.
- **RECEIVE** — a datagram was delivered to a `NodeSocket`. FLAME may still reject it.
- **DROP** — packet loss, local send failure, or a FLAME-side rejection/transfer failure. **Inspect `reason`**: not every failure is caused by simulated radio loss.
- **PROTOCOL** — FLAME state/actions (join, observation accepted, training, aggregation). The event alone may have no wire packet sequence number.

## Performance and resource boundaries

`RunData` retains a bounded number of event records (currently 200,000). `events()`
selects only the latest K matching events using a heap, O(N log K) rather than
sorting all N every refresh. The viewer displays up to 160 rows from the
server's most recent 1,000 records. **This does not make the log exhaustive**:
older records can be evicted, and short display windows can hide events.

`clive/visualizer/static/app.js` remains responsible for topology rendering,
playback, pointer handling and network requests; `event_stream.js` only owns
Event Stream display rules. Don't add FLAME protocol logic to either file.

## Workload labels matter

`trainer: "mnist"` means PyTorch neural-network training. `trainer: "mean_pixel"`
means a **non-ML communication proxy**; it must never be described as a
220-node federated neural-network scalability result. CLIVE's adapter logs
this distinction in run metadata.

## Run checks after an integration change

```bash
# From repository root
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests
PYTHONDONTWRITEBYTECODE=1 python clive/validation/validate_integration_repair.py
PYTHONDONTWRITEBYTECODE=1 python clive/validation/validate_event_stream_refactor.py
node clive/validation/validate_event_stream_js.js
PYTHONDONTWRITEBYTECODE=1 python clive/validation/validate_visualizer.py
PYTHONDONTWRITEBYTECODE=1 python clive/validation/validate_flame_integration.py
```

## Known unresolved FLAME and evaluation limitations

FLAME still owns ACK-window divergence, progressive-partial-ACK retry exhaustion,
model revision overflow and unsafe deserialization. The adapter does not patch
these. The research methodology's long-duration, spatial MNIST, accuracy and
large-node tests require separate experiment designs and verification.
