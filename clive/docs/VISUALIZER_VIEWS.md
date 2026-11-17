# CLIVE visualizer: display modes and evidence

This document describes **view presentation only**, not changes to FLAME or emulation semantics.

| View | Draws | What it does **not** prove |
|---|---|---|
| **Protocol evidence** | Observed FLAME Sensor Node-to-CH associations and recent CH-to-CH model activity, using the recorded relationship/event stream | Radio range, receipt of every packet, successful training quality, or a persistent peer connection |
| **Radio reachability** | Dashed predicted one-hop paths from CLIVE's scenario radio/path-loss model. Blue = modeled good link; green = strong; amber = poor. | JOIN, actual received packet, or any FLAME acceptance |
| **Packet activity** | Logged NodeSocket transmission/receive/drop events near playback time (if packet logging was enabled) | FLAME message validation or acceptance |
| **Nodes only** | Positions and node labels | Connectivity |

**Important:** In Radio reachability, a dashed blue line is a **modeled possible radio link**. It is not a confirmed connection. The small colored arrows only indicate possible transmit direction; unidirectional reachability may occur if radio configurations differ.

## Display settings

The menu only shows options that affect the selected view. In Protocol evidence, only recorded model exchanges are drawn between CHs, not hypothetical CH backbone candidates. A faded/red association can remain recorded even after nodes move outside radio range until the corresponding FLAME timeout is observed.

**Node labels** can be Name, Overlay IP (FLAME identity), Underlay IP (localhost UDP transport), Name + Overlay IP, or None. The choice is saved in local storage. Selecting an IP-based label automatically enables labels for all nodes; disable 'Labels on every node' to reduce clutter. The node inspector lists both address families.

Playback has a visible time readout and a precise **At (s) → Go** control. Enter a simulation timestamp (up to microsecond precision for display/seek). The selected mobility frame still reflects the trace's discrete position-tick interval; packet and protocol event timestamps are read separately.

## Event stream

**Highlights** is the default category. It shows meaningful FLAME actions/failures, not routine runtime startup, dataset buffering, or adapter bookkeeping. The Technical Details / CLIVE filter and All Events remain available. Repetitive nearby HELLO, CH discovery, JOIN request and drop entries may appear as one row, with the individual timestamped records expandable under Inspect. Raw JSONL logs are never discarded or modified. For complete history, use the Event History page.
