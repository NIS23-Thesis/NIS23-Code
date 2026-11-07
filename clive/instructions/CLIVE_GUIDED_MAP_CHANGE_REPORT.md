# CLIVE Topology UI — Guided Protocol Playback and Optional Link Labels

**Baseline:** `NIS23-CLIVE-Clear-Protocol-Stages-Map-20261105.zip`  
**Scope:** CLIVE visualizer and CLIVE validation files only. The `flame/` implementation is preserved byte-for-byte.

## Why the old display was confusing

- A HELLO, JOIN and JOIN_ACCEPT could occur within tens of milliseconds of emulated time. At normal 1× wall-clock playback they were presented and replaced so rapidly that the protocol stages looked simultaneous. The 1 ms slider allowed manual precision but did not change automatic playback speed around events.
- The old click/hover link popup and pinned details card competed with the new, plain-language single-action protocol-stage strip.
- Link names were not available as a simple optional layer.

## Changes

1. **Guided playback (default on).** Recorded FLAME protocol milestones are displayed sequentially with an **850 ms presentation hold** each. Time remains at the recorded event timestamp during the hold. This does not modify emulation time, packet timings, protocol behavior, or saved logs. Uncheck **Guided** to resume ordinary time-based playback. The speed selector still governs the intervals between events, not the explanatory hold.
2. **Next action.** An explicit, single button steps through individual protocol actions. Records with identical timestamps can be stepped separately, without falsely assigning different timestamps. The millisecond-precision slider remains available.
3. **Map focus prioritizes chronology.** The prior map selected a higher-priority action from a ±45 ms cluster, hiding some intermediate stages. Normal scrubbing now shows the latest actually recorded stage; guided stepping shows the *specific* selected event.
4. **Removed old link inspector.** The large hovered link popup, clickable pinned card, and their pointer hit-testing code were removed. Clicking a **node** still opens the Node Inspector with membership and disconnection history.
5. **Optional link-name layer.** **Layers → Link labels** draws unobtrusive node-pair tags directly on the drawn links. It defaults off to avoid clutter. Node labels are a separate existing option. No extra popup duplicates the focused map explanation.
6. **Usability:** Time readout now displays three decimal places. Next action is disabled if no action is recorded or the end of the recorded action list is reached. The presentation remains responsive at desktop and phone widths.

## Verification performed

- `python -m pytest -q tests`: **183/183 passed** (no FLAME source edits).
- `node clive/validation/validate_guided_map.js`: **PASS** (holds, accurate event time, distinct 0.010/0.020/0.040/0.044 s stages, duplicate timestamp handling, UI removal and link layer).
- `node clive/validation/validate_clear_map_stages.js`, `validate_simple_event_map_js.js`, and `validate_event_stream_js.js`: **PASS**.
- `python clive/validation/validate_visualizer.py`: **PASS**.
- `python clive/validation/run_all.py`: **ALL CLIVE VALIDATIONS: PASS**, including the six-node end-to-end FLAME acceptance test and the added guided-map regression.
- Chromium/Playwright fixture using a generated CLIVE mobility trace and real-shaped JSONL event records: **PASS** for HELLO → discovered → JOIN → JOIN_ACCEPT; 850 ms hold, next action stepping, preserving Node Inspector, Layers → Link labels, and mobile/desktop layout. No page JS errors or phone-wide horizontal overflow.
- Source integrity: every original entry under `NIS23-Code-integration/flame/` SHA-256 matched exactly. No files added under `flame/`.

## Limitations

- Guided playback is deliberately *slower* than recorded experiment time. Do not use its elapsed wall-clock duration as a protocol performance metric. Timestamps in logs are authoritative.
- The single-action stage strip cannot convey every simultaneous packet at once; the full Event History tab remains the correct source for exhaustive packet traces.
- Long experiments with thousands of protocol actions can take significantly longer to play in Guided mode. Turn Guided off for real-time or accelerated overview.
- This UI patch does not repair existing FLAME reliability defects or alter Cluster Head selection, radio conditions, or experimental measurements.
