# Five panel demonstrations (real MNIST / real PyTorch)

The Experiment Studio shows only these five demos by default:

| Demo | What the panel sees | Nodes | Duration |
|---|---|---|---|
| `panel-01-static-baseline-real-mnist-2ch-2sn.json` | **Testing first**: static no-loss control, associations and genuine training | 2 CH / 2 SN | 16 s |
| `panel-02-static-sensor-associations-real-mnist-2ch-4sn.json` | Two isolated clusters: HELLO → JOIN/JOIN_ACCEPT → accepted observations | 2 CH / 4 SN | 20 s |
| `panel-03-static-model-exchange-real-mnist-2ch-4sn.json` | Compact zero-loss CH pair for transfer/reassembly/aggregation, using the actual FLAME reliability implementation | 2 CH / 4 SN | 22 s |
| `panel-04-mobile-gauss-markov-real-mnist-2ch-4sn.json` | Moving Gauss–Markov topology; inspect actual reachability, not guaranteed reconnects | 2 CH / 4 SN | 45 s |
| `panel-05-static-loss-real-mnist-2ch-4sn.json` | RSSI distance-dependent packet loss; inspect drops, retries and completed/failed transfers | 2 CH / 4 SN | 40 s |

The demos deliberately sample less frequently than the previous crowded presets so panel viewers can follow individual learning revisions, and the two-CH demo layouts were adjusted to make association/exchange paths clearer. These are **new demo configurations**; do not compare their raw counts directly with results from old preset versions. All five explicitly set `trainer: mnist`, `sample_source: mnist`, and
`track_learning: true`. Final models are evaluated on the full MNIST test set.
The per-revision accuracy curve samples a fixed 512-image test subset and is
for presentation/diagnostics; full accuracy/convergence evaluation requires
longer, controlled runs.

## Sir Fritz 30-minute defense: CLIVE segment

1. **Testing first:** Show the existing pytest/validator results with their exact build identity. Explain unit tests versus end-to-end evidence.
2. **Milestones, briefly:** Orchestrator creates configuration and trace → Emulator spawns Nodes → NodeBootstrapper constructs NodeSocket and the team's FLAME Node. Overlay/underlay is localhost only.
3. **Demonstrate controlled cases:** Open preset 01 (baseline), preset 02 (separate SN associations), then preset 03 (model exchange). In Events, use **FLAME activity** to narrate steps and **CLIVE TX / RX / loss** for radio evidence. In Learning, choose a specific CH and revision to distinguish cumulative received digits from the current training batch.
4. **Stress the network:** Open preset 04 for mobility and preset 05 for RSSI modeled loss. Show actual saved outcomes, not an assumed success or failure. Turn **Protocol action highlights** off for dense maps and **Detailed evidence** off for dense revision tables.
5. **Initial results:** Report sender-ACK completion separately from model buffer admission and successful aggregation; show real training execution and quality only when saved metrics establish it.

**Presentation budget:** This is the CLIVE subsection within **30 minutes for FLAME + CLIVE together**. Reserve time for the FLAME team's protocol presentation and panel questions. For reliable stage navigation, use completed saved runs or a pre-recorded demonstration; running these scenarios live takes wall time and cannot guarantee exact simulated outcomes.

Other JSONs in this folder are retained for preexisting CLI/validation
compatibility but are not part of the five panel demos. In particular, old
`mean_pixel` diagnostics do **not** represent neural-network training.

---

# CLIVE demonstration presets (November 4 review)

These are demonstrations / diagnostics, **not** the 300-second thesis evaluation runs.

| Preset file | Nodes (CH/SN) | Duration | Workload | Use |
|---|---:|---:|---|---|
| `capacity-220-nodes-transport-only-no-neural-training.json` | 20/200 | 6.0 s | Synthetic scalar proxy — NOT neural-network training | high-process-count transport/startup only; no neural training or model-scale claim |
| `diagnostic-event-stream-synthetic-2ch-4sn.json` | 2/4 | 6.0 s | Synthetic scalar proxy — NOT neural-network training | event stream and diagnostic logging |
| `diagnostic-static-line-packet-events-1ch-5sn.json` | 1/5 | 10 s | Non-FLAME topology/radio demonstration | per-packet logging over a line topology |
| `flame-mnist-pytorch-training-2ch-2sn.json` | 2/2 | 12 s | MNIST / PyTorch (real neural network) | real MNIST + PyTorch + model fragmentation / aggregation |
| `flame-synthetic-transport-and-aggregation-smoke.json` | 2/4 | 5.0 s | Synthetic scalar proxy — NOT neural-network training | synthetic payload and scalar mean-pixel model, packet/association acceptance |
| `mobility-gauss-markov-random-pauses-2ch-9sn.json` | 2/9 | 30 s | Non-FLAME topology/radio demonstration | Gauss–Markov movement and independently randomized pauses |
| `mobility-random-waypoint-4ch-20sn.json` | 4/20 | 30 s | Non-FLAME topology/radio demonstration | Random Waypoint mobility |
| `radio-heterogeneous-range-static-2ch-6sn.json` | 2/6 | 10 s | Non-FLAME topology/radio demonstration | heterogeneous radio ranges |
| `topology-gauss-markov-10ch-1sn.json` | 10/1 | 60 s | Non-FLAME topology/radio demonstration | 10 CH / 1 SN mobility topology (previous filename overstated SN count) |
| `topology-static-grid-2ch-10sn.json` | 2/10 | 10 s | Non-FLAME topology/radio demonstration | static grid topology |
| `topology-static-line-1ch-5sn.json` | 1/5 | 10 s | Non-FLAME topology/radio demonstration | static line topology |
| `topology-static-manual-2ch-4sn.json` | 2/4 | 10 s | Non-FLAME topology/radio demonstration | specified static positions |
| `topology-static-star-2ch-4sn.json` | 2/4 | 10 s | Non-FLAME topology/radio demonstration | static star topology |

## How to run

From `clive/`:

```bash
python clive.py --scenario configs/demos/flame-mnist-pytorch-training-2ch-2sn.json --results results
python validation/run_all.py
python scripts/run_evaluations.py thesis_matrix --list
```

**Do not interpret worker `success` as reliable delivery.** The CLI result and per-node `flame/*.json` counters answer different questions.
**The thesis evaluation matrix is in `configs/evaluations/thesis_matrix/`, not the demo folder.**
The validation-repository FLAME implementation is kept byte-for-byte unchanged.
