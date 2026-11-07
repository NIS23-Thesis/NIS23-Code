# CLIVE Visualizer — FLAME-aware topology update

The visualization is **read-only**. It uses `plan.json`, the exact binary mobility trace,
CLIVE's optional packet JSONL events, and FLAME's optional protocol JSONL events. It does not
advance protocol timers, alter association behavior, or change the emulator's radio engine.

## Start

```bash
./start_gui.sh
```

Open **Topology visualizer** from the experiment monitor (normally
`http://127.0.0.1:8766/`). For a standalone results directory:

```bash
cd clive
python visualizer.py --results results --port 8765 --open
```

## What links mean

| View | What is drawn | What it does NOT mean |
|---|---|---|
| FLAME protocol (default) | Historical SN→CH JOIN acceptance, CH-side sensor acceptance, model-traffic evidence between CHs, and dashed role-compatible radio paths to selected nodes | A dashed link is not an established association; a past association does not guarantee present radio reachability |
| Physical radio | Outbound radio reachability from the **selected node**, regardless of role, using CLIVE's link-band and per-sender range formulas | SN→SN radio range is **not** SN→SN FLAME communication |
| Observed traffic | Packet endpoints for recorded NodeSocket **receive** events and modeled delivery drops | Moving markers illustrate a path; animation speed is not measured packet propagation or latency |
| No links | Exact node locations at each stored mobility tick | Does not depict network paths |

Legend: **cyan** association: Sensor Node logged JOIN acceptance; **green**: a Cluster Head also logged consuming valid sensor data; **red dashed**: a previously observed relationship is now out of radio range; **gray/purple dashed**: candidate only. Both send directions are checked using the sender's configured radio range; arrows are directional. Cluster Heads may exchange models, while Sensor Nodes never exchange FLAME messages with other Sensor Nodes.

This visualization does **not** claim to reconstruct CLIVE routing paths or infer membership from physical proximity. It replays observed relationship events in experiment-time order. Sensor Node LEAVE is an **outgoing event**, not proof that a Cluster Head received the notice. Some membership expiration transitions are not logged by the original adapter; the UI therefore labels relationships as *observed history*, not authoritative current membership.

## Capture event-level relationships

In **Experiment Studio → Diagnostics** enable:

- `logging.protocol_events = true` to display accepted JOINs and CH model-exchange history.
- `logging.packet_events = true` to display detailed packet event flow and the traffic timeline.

These debug logs consume disk I/O and space, particularly at 20 CH + 200 SN. Leave them disabled for clean performance benchmarks unless per-event visualization is needed. When they are off, the visualizer clearly reports that observed relationships are **unknown**, never fabricating a topology.

A ready-made example with both enabled is `clive/configs/demos/diagnostic-event-stream-synthetic-2ch-4sn.json` (2 CH + 4 SN, synthetic samples, mean-pixel trainer). It is a **diagnostic**, not an ML-accuracy scenario.

## Verification

- Unmodified FLAME tests: **183 passed**.
- Full CLIVE non-stress validation suite: **passed**, including new visualization provenance test.
- Real CLIVE 6-node, 6-second diagnostic run completed successfully. Its packet and protocol logs contained time-indexed JOIN acceptance, accepted sensor data, CH model exchanges, and LEAVE events.
- Headless Chromium rendering test with those recorded artifacts: link-mode controls, relationship replay and event activity displayed without JavaScript page errors. Direct access from that browser to a local HTTP server was blocked by the test environment, so the browser used the **recorded server responses**, not an arbitrary mock topology.
- Synthetic **220-node canvas-only rendering** probe (20 CH + 200 SN): measured approximately 1.0 ms per protocol-link drawing iteration and 0.8 ms per selected-node radio drawing iteration in headless Chromium. This is **not** a 220-node live experiment or proof of runtime memory capacity.

## Known unrelated limitations

- The original FLAME AIMD/ACK window synchronization bug is **not fixed** by this visualization update.
- Large/long event logs may be truncated by the visualizer's in-memory bounded event buffers; the UI warns when relationship event history is truncated.
- Debug traces are optional and may be absent from a previously completed run. The visualizer will still show mobility, metrics, physical reachability, and eligible CH/SN roles.
