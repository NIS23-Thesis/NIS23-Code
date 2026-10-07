# CLIVE — localhost MANET emulator

CLIVE runs virtual MANET nodes as independent localhost Python processes using real UDP sockets. The Orchestrator precomputes mobility, the Emulator supervises processes, NodeSocket applies link conditions, and a protocol implementation plugs in through `ProtocolInterface`. Mobility ticks do not drive protocol timers.

## Clean project layout

```text
clive/
├── clive.py                    # CLI entry point
├── orchestrator.py             # scenario config + mobility/topology trace generation
├── emulator.py                 # process supervisor
├── nodebootstrapper.py         # per-node runtime
├── nodesocket.py               # UDP/link emulation + metrics
├── protocol_interface.py       # protocol integration boundary
├── example_protocol.py         # validation/demo traffic only
├── visualizer.py               # read-only visualization server
├── visualizer/static/          # browser UI
├── configs/
│   ├── all_options.json        # every supported setting in one valid JSON
│   ├── demos/                  # short demo scenarios
│   └── evaluations/            # grouped evaluation scenarios
├── scripts/
│   ├── run_scenario.sh
│   ├── run_visualizer.sh
│   └── run_evaluations.py
└── validation/
    ├── run_all.py
    ├── validate_configs.py
    ├── validate_features.py
    ├── validate_nodesocket.py
    ├── validate_visualizer.py
    └── validate_clive.py
```

Generated results, Python caches, old LAN/deployment files, unrelated coursework files, and duplicate documentation are intentionally excluded.

## Demo tomorrow

Terminal 1:

```bash
scripts/run_visualizer.sh
```

Terminal 2:

```bash
scripts/run_scenario.sh static_line --protocol example_protocol:IndependentProbeProtocol
scripts/run_scenario.sh static_star --protocol example_protocol:IndependentProbeProtocol
scripts/run_scenario.sh gauss_markov_random_pause --protocol example_protocol:IndependentProbeProtocol
scripts/run_scenario.sh heterogeneous_ranges --protocol example_protocol:IndependentProbeProtocol
```

`IndependentProbeProtocol` is validation/demo traffic only; it is not FLAME.

## Run any JSON directly

```bash
python3 clive.py --scenario configs/all_options.json --results results
```

Use a real protocol with:

```bash
python3 clive.py --scenario configs/demos/static_star.json \
  --protocol your_module:YourProtocol
```

## Evaluation sets

List all evaluation configs:

```bash
python3 scripts/run_evaluations.py --list
```

Validate/build all plans without launching 5-minute experiments:

```bash
python3 scripts/run_evaluations.py
```

Validate one group:

```bash
python3 scripts/run_evaluations.py mobility
python3 scripts/run_evaluations.py density
python3 scripts/run_evaluations.py area
python3 scripts/run_evaluations.py radio
python3 scripts/run_evaluations.py topology
```

Quickly execute a group with a shortened duration:

```bash
python3 scripts/run_evaluations.py topology --run --duration 10 \
  --protocol example_protocol:IndependentProbeProtocol
```

Formal evaluation execution uses the duration stored in each JSON when `--duration` is omitted.

## Validation

Run the complete non-stress suite:

```bash
python3 validation/run_all.py
```

Optional 200-process stress check:

```bash
python3 validation/validate_clive.py --stress-200
```

## Configuration

See `configs/README.md`. The main mobility modes are `gauss_markov`, `random_waypoint`, and `static`. Initial layouts are `random`, `line`, `star`, `grid`, and `manual`. Link range can be RSSI-derived or explicit, with optional role/node overrides. Random pauses are independent per node and reproducible from the scenario seed.

## Result files

By default each run keeps only the files needed for normal evaluation and replay:

- `plan.json` - canonical emulation plan, including the precomputed mobility trace.
- `run-manifest.json` - run status and provenance.
- `metrics.json` - one compact run-level metrics file containing totals and per-node counters.

Verbose per-node `*.events.jsonl` and `*.packets.jsonl` files are disabled by default. They can be enabled with the scenario `logging` section and are then stored under `debug/`. `mobility.tsv` is also optional because its coordinates duplicate the trace already stored in `plan.json`.
