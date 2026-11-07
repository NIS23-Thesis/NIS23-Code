# CLIVE configurations

`all_options.json` is the complete canonical configuration reference. It is valid JSON and can be passed directly to `clive.py`. Model-specific fields may be present even when the selected model does not use them.

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
