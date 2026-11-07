# Node Connection Tracker — CLIVE Observatory

## Open the tracker

Run CLIVE and open the Observatory. Click any Sensor Node (circle) or Cluster Head (square) on the map, or select it from the Nodes table. The right-side **Node inspector** follows the selected node as you scrub the playback timeline.

### The three different kinds of evidence

1. **Observed FLAME association**: An accepted `JOIN_ACCEPT` at the SN (`flame_joined`) and/or confirmed sensor observation at the CH (`flame_sensor_received`). This is protocol history, not a physical link guarantee.
2. **Radio reachability**: CLIVE's current calculated sender and receiver link conditions from the stored mobility trace. Two-way reachability does **not** mean a sensor has joined or a model update has completed.
3. **Observed disconnect**: A recorded `LEAVE`, an inactive/expired neighbor entry, or a new JOIN attempt. A physical radio link can fail *before* FLAME's neighbor timeout detects disconnection.

The inspector deliberately uses explicit wording such as **Trying to join**, **JOIN accepted · radio reachable**, **JOIN accepted earlier · radio currently unavailable**, **Disconnected (recorded)**, **No JOIN recorded**, or **Unknown (no logs)**. It never infers logical membership solely from distance.

### Sensor Node

- Shows the CH targeted by the latest JOIN attempt, repeat attempts, its last accepted CH, and the most recent departure/timeout when recorded.
- Shows possible radio peers with distance and directional reachability; these are candidates only.
- Shows an ordered history with times and packet sequence numbers where packet-based events include them.

### Cluster Head

- Lists Sensor Nodes with logged accepted JOIN/data relationships.
- Lists CHs with observed model-update activity; an attempted transfer is **not** labeled as a completed model exchange.
- Shows neighbor timeouts, received LEAVE messages, and model-transfer outcomes where recorded.

### Logging and scope

For new FLAME runs, CLIVE writes a minimal per-node connection history to `debug/connections/<node>.jsonl` when `logging.protocol_events` is false. When `protocol_events` is true, the existing full `debug/events/<node>.jsonl` is used instead. The compact stream avoids detailed packet logging and records just membership milestones, sensor application acceptance and model-exchange outcomes. `logging.packet_events` remains optional.

Older run directories without either stream cannot recover historical associations: **Unknown** is not the same as **Disconnected**. The Observatory's replay cache is time-scoped, bounded, and can omit very old history in extremely large runs. CH-to-CH peers do not use JOIN; their status is described as **peer model activity**, not an association. Neither the visualizer nor the connection log changes FLAME packets, timers, routing or neighbor-state decisions.

## Where to read the code

- `clive/flame_protocol.py`: emits additional *observational* connection milestones, including timeout/loss and peer-discovery events. Does not modify `flame/`.
- `clive/nodebootstrapper.py`: selects the compact log path when full protocol logging is disabled.
- `clive/visualizer.py`: normalizes the compact/full records and exposes them through `/api/relationships`.
- `clive/visualizer/static/connection_tracker.js`: pure replay logic, membership state, peer activity and retained history.
- `clive/visualizer/static/app.js`: selected-node panel, directional radio calculations and playback.
- `clive/validation/validate_connection_tracker.py`, `clive/validation/validate_connection_tracker_js.js`: backend and replay regression tests.

## Known limitation

This is a visibility feature. It does **not** fix FLAME's identified reliability bugs (including ACK window disagreement under loss). A model transfer may fail even while CHs remain in radio range. Consult transfer failures and their recorded reasons; do not equate worker process success with model delivery.
