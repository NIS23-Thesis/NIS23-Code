# CLIVE — localhost MANET emulator

CLIVE runs virtual MANET nodes as independent localhost Python processes using real UDP sockets. Each virtual node has a dedicated **node/protocol process** and a dedicated **radio/TX child process**. The emulation runtime uses no Python worker threads, so node/protocol execution and CPU-heavy radio calculations never contend for the same process GIL. Mobility ticks do not drive protocol timers.

## Clean project layout

```text
clive/
├── clive.py                    # CLI entry point
├── orchestrator.py             # scenario config + mobility/topology trace generation
├── mobility_store.py           # compact binary trace + read-only mmap access
├── emulator.py                 # process supervisor
├── nodebootstrapper.py         # single-threaded per-node protocol/RX process
├── nodesocket.py               # RX proxy + separate radio/TX child process
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


## Process/GIL architecture

```text
Emulator supervisor
│
├── CH-1 node/protocol process     (single Python execution thread / own GIL)
│   └── CH-1 radio/TX child process (single Python execution thread / own GIL)
├── CH-2 node/protocol process
│   └── CH-2 radio/TX child process
└── ...
```

The node process handles UDP receive, protocol callbacks, and experiment-time protocol timers.
The radio child handles TX scheduling, broadcast fan-out, distance/RSSI/bandwidth/PLR/IPDV, TX
energy, and the actual UDP `sendto()`. Communication between the two is through
`multiprocessing.Pipe`. There is no sender thread and the validation probe no longer creates a
timer thread. The browser visualizer is a separate application process; its HTTP implementation
is not part of the emulation execution path.

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

## Overlay and underlay support

CLIVE now separates FLAME's **overlay** node address from the real localhost
UDP **underlay** address. Configure either `addressing` for generated nodes or
`nodes` for a named per-node list. Existing scenarios retain their original
`127/8` transport/overlay identity unless explicitly changed. See
[`configs/README.md`](configs/README.md#overlay-and-underlay-endpoints-localhost-deployment)
for example JSON, Wireshark capture settings, and the localhost-only boundary.

## Run any JSON directly

```bash
python3 clive.py --scenario configs/all_options.json --results results
```

FLAME is the default protocol (`flame_protocol:FlameProtocol`). For the integrated FLAME configuration and end-to-end tests, see the root `README-INTEGRATION.md`. To run a different protocol:

```bash
python3 clive.py --scenario configs/demos/topology-static-star-2ch-4sn.json \
  --protocol example_protocol:IndependentProbeProtocol
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

Optional 200-node stress check (200 node processes + 200 radio child processes):

```bash
python3 validation/validate_clive.py --stress-200
```

## Configuration

See `configs/README.md`. The main mobility modes are `gauss_markov`, `random_waypoint`, and `static`. Initial layouts are `random`, `line`, `star`, `grid`, and `manual`. Link range can be RSSI-derived or explicit, with optional role/node overrides. Random pauses are independent per node and reproducible from the scenario seed.

## Result files

By default each run keeps only the files needed for normal evaluation and replay:

- `plan.json` - small canonical metadata/configuration plan; it does **not** embed the full trace.
- `mobility.bin` - one compact float64 mobility trace shared read-only by radio processes.
- `run-manifest.json` - run status and provenance.
- `metrics.json` - one compact run-level metrics file containing totals and per-node counters.

Verbose per-node `*.events.jsonl` and `*.packets.jsonl` files are disabled by default. They can be enabled with the scenario `logging` section and are then stored under `debug/`. `mobility.tsv` is optional and is only a human-readable export of the canonical `mobility.bin` trace.

For a 110-node, 300-second run at a 0.1-second mobility interval, `mobility.bin` is about 5.28 MB total. The same trace previously produced a ~26 MB JSON plan and was deserialized into every node process.

## Sensor association: first compatible HELLO (CLIVE adapter)

A Sensor Node sends JOIN to the first model-compatible, active Cluster Head whose
HELLO **it received**, in that SN's arrival order. This is not nearest-CH,
strongest-RSSI, or lexicographically lowest-IP selection. Two sensors can join
different CHs because mobility, delay, packet loss, and execution timing may
change the order they observe HELLOs. The first choice is preserved while joined;
when no longer associated, the adapter considers still-active discovered CHs in
that original arrival order. FLAME's own implementation is not modified.

## Visualizer event groups and traffic chart

The Traffic, Events, and Nodes tabs are always available below the topology;
"Show extra metrics" only expands the metric cards. In Events, the Transmissions
filter shows real `transmit` records (not queue/admission/kernel steps). Receptions
shows real `receive` records, and Protocol shows FLAME application actions.
"All events" includes low-level internal operations when needed for debugging.

Traffic chart hover shows **recorded events per one-second bucket**. These are
NodeSocket log counts, not packet-delivery probability or application acceptance.
The chart reads append-only packet logs incrementally, rather than discarding
older counts after the bounded Event Stream cache fills. Link animation queries
are independent of the Events tab filter.

## Thesis evaluation campaign (November 2026)

For the formal 108-case matrix, repeat-seed runner, NodeSocket UDP calibration,
controlled 300-second lossy scenarios and evidence/report formats, see
[`docs/EVALUATION_CAMPAIGN_20261115.md`](docs/EVALUATION_CAMPAIGN_20261115.md).
The tools distinguish configuration validation, short diagnostic runs, complete
emulator executions and FLAME model-transfer outcomes. Native localhost
`ping`/`iperf3` bypasses NodeSocket, so the bundled UDP calibration is explicitly
labeled as an application-level ping-/iperf-style probe rather than external
`iperf3` execution.

### Visualizer light mode

The shared CLIVE header includes a **Light mode / Dark mode** switch on Setup,
Topology, Learning, and Events. Dark mode is the default for existing users.
The selected mode is stored in the browser and restored on reload; links between
standalone Studio and Visualizer ports carry the selection. The topology canvas,
traffic chart, and learning chart redraw immediately when switching themes.
Theme resources are `control_ui/theme.{css,js}` and
`visualizer/static/theme.{css,js}`. The shared header generator
`clive/scripts/sync_ui_shell.py` retains the switch when regenerating markup.
These changes are presentation-only and do not modify FLAME or run results.
