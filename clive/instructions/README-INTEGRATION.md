# CLIVE v2 + final FLAME protocol — integrated repository

The `clive/` directory originates from **NIS23-Code-clive-v2.zip**, while `flame/`, `mnist/`, `tests/`, and the other top-level FLAME-related resources originate from **NIS23-Code-flame-protocol-validation.zip**. CLIVE v2's process, socket, radio, mobility, visualizer, and scenario implementations are retained. An integration adapter was added at `clive/flame_protocol.py`.

**Running `clive/clive.py` or instantiating `Emulator` now launches the real FLAME protocol by default.** The old independent probe remains available with `--protocol example_protocol:IndependentProbeProtocol`; the original CLIVE no-traffic baseline remains available with `--protocol protocol_interface:NullProtocol`.

## Quick verification

```bash
cd clive
python3 validation/validate_flame_integration.py
python3 validation/run_all.py
cd ..
python3 -m pytest -q tests
```

The first validation executes an end-to-end six-node FLAME smoke experiment through *actual CLIVE NodeSocket processes*, including discovery, joining, sensing, reliability/ACKs, training, and CH-to-CH aggregation. For speed, it uses a **synthetic sample source and a tiny scalar training function** (it is not a machine-learning accuracy test).

## Running full MNIST/PyTorch FLAME

Install Python dependencies:

```bash
python3 -m pip install numpy torch pytest
```

Use the bundled MNIST archive at `mnist/mnist.npz` (part of the uploaded source ZIP). Launch:

```bash
cd clive
python3 clive.py --scenario configs/demos/flame-mnist-pytorch-training-2ch-2sn.json --results results
```

This uses **real 785-byte MNIST samples** (784 grayscale pixels + one label), a **784→128→10 PyTorch MLP**, PyTorch local training on Cluster Heads, pickled model state exchange through genuine FLAME `MODEL_UPDATE` packets, 1,024-byte fragmentation, sliding-window acknowledgments, and buffered FL aggregation.

`flame-mnist-pytorch-training-2ch-2sn.json` is a **short functional integration example**, not a research-quality experiment and not a reproduction of the official WAFL GitHub repository. The training code is exactly the `MNISTTrainer` provided in the user-supplied FLAME validation ZIP, not the official WAFL implementation. Its optimizer/network follow the supplied trainer code.

## Normal experiments

The FLAME adapter can be used with CLIVE v2's regular scenarios, including mobility traces, different radio ranges and loss conditions, logging, and visualizer:

```bash
cd clive
python3 clive.py --scenario configs/demos/mobility-gauss-markov-random-pauses-2ch-9sn.json --results results
```

A standalone CLIVE v2 configuration without a `flame` section uses the default FLAME timing: 5 seconds between CH HELLO broadcasts, 30 seconds between SN samples. A shorter scenario may finish before any sensor sampling; override the timings in its optional `flame` block if that is not intended.

```json
"flame": {
  "model_id": 1,
  "hello_interval_s": 5.0,
  "sample_interval_s": 30.0,
  "service_interval_s": 0.02,
  "trainer": "mnist",
  "sample_source": "mnist",
  "expected_observations": 10,
  "training_threshold": 0.8,
  "aggregation_threshold": 2,
  "aggregation_learning_rate": 0.5,
  "ack_timeout": 0.25
}
```

Available adapter settings and defaults are declared in `clive/flame_protocol.py` (`DEFAULTS`). Scenario settings are serialized into the CLIVE `plan.json` as `flame_settings` and passed to each node via the existing `EmulationPlan` mechanism. No host address or LAN translation is required: this is the localhost-only CLIVE v2.

### Role-dependent execution

- **CH (role 1):** broadcasts serialized FLAME `HELLO`; validates joins and issues `JOIN_ACCEPT` through the existing FLAME communication/neighbor modules; accepts/reassembles `SENSOR_DATA`, uses FLAME's `DatasetManager`/`Trainer`/`ModelManager`; accepts `MODEL_UPDATE` through FLAME's buffered aggregation; disseminates the latest local model to compatible discovered CHs.
- **SN (role 0):** receives CLIVE-delivered `HELLO`; invokes FLAME's `NeighborModule` to `JOIN` a compatible CH; uses `DataAcquisition` and `SensingModule` to produce real MNIST samples; buffers samples during temporary disconnection; sends FLAME `LEAVE` before planned emulation shutdown.
- **Communication:** FLAME's `CommunicationModule` runs with `auto_start=False` and `poll_socket=False`, using the injected CLIVE `NodeSocket`. CLIVE alone owns UDP RX and the emulated TX/radio child; the adapter services FLAME's send/reliability queues from `ProtocolInterface.on_timer()`, not from mobility ticks or a FLAME thread.

## Results

Every run creates `results/run-.../` containing the original CLIVE output (`plan.json`, `mobility.bin`, `metrics.json`, `run-manifest.json`) **plus** per-node `flame/CH-1.json`, `flame/SN-1.json`, etc. Those extra files report FLAME packet event counters, membership state, observation counts, and local model update IDs. Enable `logging.protocol_events` in the scenario to capture detailed protocol events under `debug/events/`.

The normal CLIVE browser visualizer reads the same canonical mobility and packet debug streams; no separate networking path was introduced by this integration. Optional packet-event logging still uses `logging.packet_events`.

## Validation and scope limits

Validated using the supplied repository's **183 upstream FLAME unit tests**, CLIVE v2's complete non-stress validation suite, a six-node synthetic transport/aggregation smoke run, and a real PyTorch/MNIST functional experiment. These checks verify wiring and basic behavior, **not** model convergence, numerical accuracy, or experimental fairness across mobility regimes.

Known scope limitations inherited from the supplied implementations: the official WAFL repository is not included; `ModelManager` uses an 8-bit `update_id` and raises on overflow rather than wrapping; FLAME membership expiry uses wall time, consistent with CLIVE's real-time emulation rather than a discretely stepped simulator; and full MNIST model states are fairly large (roughly 399 FLAME data fragments per update), making large-node experiments resource-intensive. CH association selection depends on observed HELLO availability, not an omniscient distance oracle. The protocol's existing `Aggregation` may propagate repeated local model revisions across opportunistic contacts; assess this separately when evaluating convergence.
