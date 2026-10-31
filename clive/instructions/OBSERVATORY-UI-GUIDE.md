# CLIVE Observatory — visualizer UI redesign

This revision changes the visualizer frontend only. FLAME packets, ACK handling,
training, aggregation, CLIVE radio emulation, experiment control, and MNIST
sampling remain unchanged.

## Launch

On Linux/Fedora, from the extracted project folder:

```bash
./start_gui.sh
```

Run a scenario from Experiment Studio and open **Topology visualizer**.
The standalone read-only visualizer is also available with
`python clive/visualizer.py --results clive/results --port 8765` (adjust the
results path to your actual experiment output folder).

## Visual layout

- The map is the main focus. **FLAME protocol**, **Radio range**, **Traffic**,
  and **Nodes only** are direct mode buttons.
- **Layers** contains CH backbone candidates, selected-node candidate links,
  and always-visible node labels. No JSON editing is required.
- **FIT** zooms to the current node group; **100%** returns to the full scenario
  field. Wheel zoom and drag-to-pan still work.
- **Hide panel** collapses the Node Inspector / Network Metrics / Experiment
  Config sidebar. **Focus map** temporarily hides secondary panels.
- Traffic timeline, event stream, and per-node metrics now occupy separate tabs.
  Tabs are unavailable when their corresponding log stream was disabled.

## Contextual links: hover or click

Move the pointer near a displayed line to open a contextual description. Click
its line to pin the description, or click **×** to close it. For dense groups,
use **FIT** to separate nearby nodes. Press **Esc** to dismiss the pinned card.

Each explanation is based on the current playback time and view mode:

| View | Explanation |
| --- | --- |
| FLAME protocol, SN–CH (observed) | Recorded JOIN and CH-accepted sensor observation times, last accepted sample, estimated radio state |
| FLAME protocol, CH–CH (observed) | Last logged model-update event; this does **not** prove a completed model transfer |
| FLAME candidate | The two roles permit that communication and the radio model considers it reachable; there is no logged association implied |
| Radio range | Current directional radio reachability, separation, estimated RSSI, loss band; SN–SN is *not* a FLAME relationship |
| Observed traffic | Actual recorded NodeSocket receive or modeled drop, packet type, timestamp, endpoint, and logged reason if available |
| Historical/out-of-range | Past logged relationship at a time when the pair is currently out of range; not proof of current membership |

Link cards also show the latest logged protocol actions involving the pair up to the
playback cursor. They do **not** invent live actions between event timestamps.

### Important evidence boundaries

- Radio reachability is **inferred from CLIVE's current mobility tick and radio
  configuration**, not an observed packet exchange.
- Protocol associations / model exchange marks require **Protocol events** to have
  been enabled for the run. These are historical observations. Timeout or
  application-processing state may differ.
- Traffic dots/crosses require **Packet events** enabled. Received traffic is
  NodeSocket delivery, **not** proof that a whole fragmented sample/model was
  successfully processed. Packet animation is illustrative, not measured
  propagation time.
- Playback is read-only and never advances or modifies FLAME protocol timers.
- The original FLAME reliability/window bugs identified in the previous audit
  have not been changed by this frontend revision.

## Validation completed

- `pytest -q tests`: **183 passed**.
- `python clive/validation/validate_visualizer.py`: **passed**.
- `python clive/validation/validate_visualizer_links.py`: **passed**.
- GUI control and membership regression suite: **5 passed**.
- CLIVE–FLAME six-node end-to-end integration: **passed**.
- Memory optimization validation: **passed**.
- Headless Chromium tests using authentic six-node diagnostic data: no JS page
  exceptions; mode selection, tabs, sidebar, Focus map, and Layers worked;
  hover and click-to-pin showed recorded SN–CH activity; no horizontal overflow
  at 800px and 390px viewports.

Full training with 220 nodes and multi-host behavior were not revalidated in
this frontend-only update.
