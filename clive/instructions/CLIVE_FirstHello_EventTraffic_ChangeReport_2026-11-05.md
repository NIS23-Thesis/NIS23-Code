# CLIVE – first HELLO selection, Event Stream and traffic-chart tooltip

**Release:** November 5, 2026  
**Based on:** `NIS23-CLIVE-Visualizer-SecondPass-20261105.zip`  
**Scope:** CLIVE and its integration adapter only. **No file within `flame/` was modified.**

## What changed

1. **First compatible HELLO wins.** In `clive/flame_protocol.py`, SN JOIN chooses the earliest compatible, active CH whose HELLO reached that particular SN, not the lowest IP/port. When that peer becomes inactive and the SN needs to rejoin, selection proceeds through still-active discovered CHs in first-receipt order. It does not imply nearest-CH or strongest-RSSI selection. Delayed/lost HELLO messages can produce different first-choice CHs for different SNs.
2. **Traffic / Events / Nodes visible again.** A prior panel stylesheet hid the entire diagnostics workbench unless “Show advanced diagnostics” was enabled. Now the three tabs are available below the map; extra metric fields remain optional under “Show extra metrics.”
3. **Event filters show what their labels claim.** TRANSMIT now means the actual `transmit` packet log, RECEIVE means `receive`, PROTOCOL means FLAME application/connection events, and DROP means actual network drops and failed/rejected application events. Internal queue/admission/kernel events remain available under “All.” The same event classification is applied by the API and browser renderer.
4. **No more misleading stale filter content.** Changing Event type, time window, or search immediately displays “Loading matching recorded events” until the matching on-disk query finishes; earlier category records are not misrepresented as newly selected results.
5. **Topology radio traffic no longer depends on the Events tab filter.** In Traffic map mode, the map independently requests receive/drop markers. Switching the Event tab to Protocol can no longer wipe the map’s traffic animation.
6. **Traffic chart has hover inspection.** Hovering a one-second bucket displays its actual recorded TX, RX, and drop counts, with a vertical guide and highlighted data points. Empty/unrecorded areas have no fabricated measurements.
7. **Traffic timeline no longer drops old counts.** The backend reads and aggregates append-only on-disk packet logs incrementally, instead of using the capped 200,000-record live cache. Counts are per recorded event, not a measure of end-to-end FLAME model acceptance; broadcasts may have multiple RX events per TX.

## Verification

- FLAME unit tests: **183/183 passed** (without modifying the FLAME source tree).
- Full CLIVE non-stress validation: **ALL CLIVE VALIDATIONS: PASS**, including the new first-HELLO/event/traffic test.
- Browser fixture interaction: desktop 1440px and phone 390px — TX, RX, Protocol filters and the traffic hover tooltip passed without JavaScript errors. The fixture uses controlled event responses; it is not a live run.
- Read a saved real MNIST CLIVE run: at 0–5.4 s, **109 key events, 164 protocol events, 5,922 TX, 5,950 RX, and 0 recorded drops**; the zero-drop result is correct for the tested interval. Results are not a claim that all run intervals are loss-free.
- Source integrity: all **25 FLAME files** in the ZIP match the source ZIP byte-for-byte (SHA-256 inventory in `CLIVE_FirstHello_FLAME_Integrity_20261105.json`).

## Important limits

- This is a CLIVE association-policy change, **not** a fix to FLAME's own ACK-window/retry defects. It can change which CH a sensor joins and thus the dataset distribution, so record the policy in the methodology when evaluating association.
- A “first” HELLO means **first received by that SN**, not first broadcast by a CH or closest geographically.
- A one-second traffic bucket shows recorded datagram events at NodeSocket, not packets guaranteed to be accepted by FLAME. RX can exceed TX for broadcasts. If packet logging was disabled, earlier packet events cannot be recovered.
- Browser tests use local fixture data because direct Chromium navigation to localhost was blocked in this environment. No claim is made of a successful interactive test on the user's actual desktop.
- This patch is not a complete reliability, security, or scientific-accuracy certification for the full thesis campaign.

## How to run

Extract the ZIP, change into `NIS23-Code-integration/`, and execute `./start_gui.sh`. Select a real-MNIST panel demo; the Event Stream is available in the Traffic / Events / Nodes workbench below the topology. Select `Traffic` on the map to see actual received/drop markers, and hover the traffic chart to inspect per-bucket counts.
