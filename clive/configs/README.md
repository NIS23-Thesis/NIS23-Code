# CLIVE configurations

`all_options.json` is the complete canonical configuration reference. It is valid JSON and can be passed directly to `clive.py`. Model-specific fields may be present even when the selected model does not use them.

## Overlay and underlay endpoints (localhost deployment)

CLIVE maintains **two endpoint identities** per node. The **overlay** is the IPv4/UDP
endpoint FLAME uses for HELLO, JOIN, SENSOR_DATA, MODEL_UPDATE, ACKs and neighbor
membership. The **underlay** is the actual Linux UDP socket endpoint. NodeSocket
translates overlay destinations to their underlay endpoints and converts the
underlay sender IP back into its overlay identity on reception. The wireless
model (range/loss/rate/delay) is still applied before UDP sendto. No FLAME
header or protocol behavior changes.

The default remains backward compatible: overlay = underlay = a distinct
`127.0.0.x:27062` per node. To enable distinct, automatically generated
identities for a standard `num_ch` / `num_sn` scenario, add:

```json
"addressing": {
  "overlay_network": "10.27.0.0/24",
  "underlay_network": "127.27.0.0/24",
  "overlay_port": 27062,
  "underlay_port": 27072
}
```

To supply **the exact node list** called for by the Orchestrator milestone,
use `nodes` instead of auto-generated `addressing`:

```json
"nodes": [
  {"name": "CH-1", "role": "CH",
   "overlay": {"ip": "10.29.0.2", "port": 28062},
   "underlay": {"ip": "127.29.0.2", "port": 28072}},
  {"name": "SN-1", "role": "SN",
   "overlay": {"ip": "10.29.0.3", "port": 28062},
   "underlay": {"ip": "127.29.0.3", "port": 28072}}
]
```

For flat-file compatibility, the same values can be supplied as `ip`, `port`,
`underlay_ip` and `underlay_port`. Nodes may use different logical and physical
UDP ports. Each physical source IP must be unique, even when physical ports
are different: the TX radio socket intentionally uses an ephemeral source
port, so the receiver maps by source IP rather than source port. All underlay
IP addresses must be canonical **127/8 loopback** addresses; this is **not**
LAN or multi-host deployment support. Overlay addresses may be unicast IPv4
addresses outside 127/8. Underlay sockets never bind to overlay addresses.

`plan.json` persists both endpoint pairs and is passed unchanged through
Emulator → NodeBootstrapper → NodeSocket. The visualizer and FLAME events refer
to overlay identities; packet debug events also provide `actual_source` on
receive and `actual_target` for kernel sends. In Wireshark, capture **loopback
(`lo`)** and filter the **underlay UDP destination port** (27072/28072 for
the examples above), not the overlay port. Playback of a stored run does not
re-transmit UDP.

Examples:
- `demos/overlay-underlay-flame-2ch-4sn.json`: automatic addressing and a
  short **synthetic** protocol-traffic demonstration, not ML accuracy evidence.
- `demos/overlay-underlay-real-mnist-2ch-2sn.json`: real PyTorch/MNIST with
  automatically generated overlay and underlay endpoints.
- `demos/overlay-underlay-explicit-real-mnist-2ch-2sn.json`: explicitly listed
  nodes/roles/endpoints with real PyTorch/MNIST.

There is **no prescribed post-aggregation retransmission requirement** in the
user-confirmed thesis scope. This addressing feature does not change any
post-aggregation behavior, which belongs to FLAME.

## Demo scenarios

`configs/demos/` contains short scenarios intended for demonstrations and controlled checks:

- `topology-static-line-1ch-5sn.json` — fixed chain with explicit node order.
- `topology-static-star-2ch-4sn.json` — fixed star with an explicitly selected center node.
- `topology-static-grid-2ch-10sn.json` — fixed grid.
- `topology-static-manual-2ch-4sn.json` — exact user-provided `(x,y)` coordinates.
- `mobility-gauss-markov-random-pauses-2ch-9sn.json` — Gauss-Markov with independent probabilistic pauses.
- `mobility-random-waypoint-4ch-20sn.json` — Random Waypoint mobility.
- `radio-heterogeneous-range-static-2ch-6sn.json` — role- and node-specific TX ranges.
- `topology-gauss-markov-10ch-1sn.json` — short 110-node scalability demo.

Run one with:

```bash
scripts/run_scenario.sh static_star --protocol example_protocol:IndependentProbeProtocol
```

## Evaluation scenarios

`configs/evaluations/` groups controlled experiments by independent variable:

- `mobility/` — none, low, medium, high mobility speeds and pause-duration ranges.
- `density/` — 4/20, 7/50, and 10/100 CH/SN counts.
- `area/` — 200×200, 350×350, and 500×500 m areas.
- `radio/` — controlled explicit TX-range sensitivity tests at 40, 70, and 100 m.
- `topology/` — static line, star, and grid controlled tests.

The formal evaluation JSONs use a 300 s experiment duration unless the scenario is explicitly a structural topology check. `scripts/run_evaluations.py` validates/plans them by default and only launches them with `--run`.

The thesis specifies pause durations for mobility levels but does not specify how often pauses begin. CLIVE's periodic-pause evaluation configs therefore use a fixed implementation cadence (`move_time_range: [10, 30]`) so that pause duration can be varied reproducibly. Treat that cadence as an emulator setting, not as a thesis-derived value.

Sensor observation frequency is not represented here because it belongs to FLAME/protocol behavior, not CLIVE's mobility/link emulator.

## Static placement controls

For a line or grid, control ordering with:

```json
"node_order": ["SN-5", "SN-4", "CH-1", "SN-1", "SN-2"]
```

For a star, choose the hub with:

```json
"center_node": "CH-2"
```

For exact coordinates:

```json
"initial_topology": "manual",
"manual_positions": {
  "CH-1": [100.0, 100.0],
  "SN-1": [150.0, 100.0]
}
```

A manual layout must provide exactly one position for every generated node.

## Output logging

Normal scenarios default to compact evaluation output: one run-level `metrics.json`; protocol and packet JSONL logs are disabled.
Enable diagnostic files only when needed:

```json
"logging": {
  "protocol_events": true,
  "packet_events": true,
  "mobility_tsv": true
}
```

Diagnostic JSONL files are written under `debug/events/` and `debug/packets/`. `mobility.tsv` is an optional human-readable export; `plan.json` remains the canonical runtime plan and already contains the mobility trace.

## Important: real federated learning versus capacity tests

The `capacity-220-nodes-transport-only-no-neural-training.json` preset intentionally uses `flame.trainer: mean_pixel`.
This is **NOT PyTorch/MLP training**. Its six-second duration and 10,000 expected
observations prevent training from starting; use it only for short node/process,
join and sensor-transport capacity tests. It is not evidence of 220-node
federated-learning throughput, convergence, accuracy, or sustained memory use.

`demos/flame-mnist-pytorch-training-2ch-2sn.json` uses **real** MNIST + PyTorch (`trainer: mnist`).
Its shorter timeouts and accelerated samples are demo settings, not calibrated
thesis network parameters. The integration writes `flame_outcome` separately
from `execution_success`; neither implies classifier accuracy. Packet-loss and
ACK-window reliability defects inside the unmodified FLAME implementation remain.

By default the adapter stops sampling after its configured finite sample pool
is exhausted (`repeat_sensor_samples: false`). Set `true` only when deliberate
repetition is appropriate for a transport stress test; otherwise size the pool
for `duration / sample_interval` or more unique observations. In the thesis matrix, a sensor receives images from the geographically clustered digit region containing its **initial** position (a 5-column by 2-row zoning approximation). Set `mnist_distribution: sensor_id` only for legacy comparisons.

## Thesis Chapter 4.7 full factorial scenarios (new)

The single-axis `mobility/`, `density/`, and `area/` configs are diagnostic
scenarios; they are **not** the complete 108-case factorial evaluation.
Generate the exact 4 x 3 x 3 x 3 combinations with:

```bash
python scripts/generate_thesis_matrix.py --seed 810
python scripts/run_evaluations.py thesis_matrix --list
python clive.py --scenario configs/evaluations/thesis_matrix/m-low_d-low_f-high_a-small.json --results results/evaluations
```

The generated configs set `trainer=mnist`, `sample_source=mnist`, `mnist_distribution=spatial_clusters`,
`sample_interval_s=60/30/15`, `evaluate_accuracy=true`, and `save_final_model=true`.
The final model is saved as local PyTorch weights and accuracy is calculated using
the held-out MNIST test set. *Generation does not run tests or establish capacity.*
At 10 CH / 100 SN, real PyTorch can exhaust memory: validate resources before
running the entire matrix. Chapter 4's Gauss-Markov alpha and pause-initiation
cadence are implementation assumptions, not independently prescribed factors.

`clive/` runs localhost only. The thesis describes LAN capability, but that
has not been implemented and must not be presented as validated.

## Per-role capacity overrides (GUI and CLI)

Use **Experiment setup → Buffers & storage** to edit separate Sensor Node and
Cluster Head capacities without hand-editing JSON. Overrides are saved under
`flame.sn_*` and `flame.ch_*` in scenarios; `null` inherits the older default.
The full list and limitations are in `clive/docs/CAPACITY_CONTROLS.md`.

These settings bound specific FLAME buffers and CLIVE's NodeSocket TX queues,
not a virtual node's total RAM or CPU. Inbound model-reassembly transfer maps
remain under FLAME's unchanged implementation and have no independently
configurable memory limit.
