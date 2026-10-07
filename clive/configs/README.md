# CLIVE configurations

`all_options.json` is the complete canonical configuration reference. It is valid JSON and can be passed directly to `clive.py`. Model-specific fields may be present even when the selected model does not use them.

## Demo scenarios

`configs/demos/` contains short scenarios intended for demonstrations and controlled checks:

- `static_line.json` — fixed chain with explicit node order.
- `static_star.json` — fixed star with an explicitly selected center node.
- `static_grid.json` — fixed grid.
- `static_manual.json` — exact user-provided `(x,y)` coordinates.
- `gauss_markov_random_pause.json` — Gauss-Markov with independent probabilistic pauses.
- `random_waypoint.json` — Random Waypoint mobility.
- `heterogeneous_ranges.json` — role- and node-specific TX ranges.
- `large_10ch_100sn.json` — short 110-node scalability demo.

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
