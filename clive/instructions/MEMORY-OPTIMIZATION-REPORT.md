# CLIVE v2 + FLAME: memory optimization and reproducibility report

## Scope and non-changes

This project is based on `NIS23-CLIVEv2-FLAME-GUI-first-sensor-fixed.zip`. The first-SENSOR_DATA membership fix is retained. The code changes in this edition concern data loading and CLIVE's runtime execution choice **only**. FLAME's packet wire format, ACK/retry state machine, model-update serialization, training algorithm, aggregation algorithm, protocol timers, dataset capacities, and FLAME application behavior are unchanged. Known ACK/AIMD protocol bugs identified in the prior audit remain unresolved.

## Memory sources found

1. Previously, importing the FLAME adapter also imported `flame.federated_learning.mnist_trainer`, which imports `torch.nn` even when running a Sensor Node. Hundreds of sensors paid the memory cost of PyTorch though they never train.
2. Each real MNIST sensor previously ran `np.load(mnist.npz)` and materialized the full training images just to choose 128 images of one label. Concurrent sensor startup caused high temporary memory usage.
3. The existing CLIVE runtime creates one node process **and** one independent radio process for each virtual node. At 220 nodes this can mean 440 processes. Most of the radio engines need the same immutable mobility data, which was already memory-mapped by CLIVE v2.
4. Debug packet/event JSONL output can use substantial disk storage, though it is already disabled by default. Each node still allocates bounded send queues. Theoretical aggregate configured queue limits do not imply these bytes are actually allocated.
5. Original FLAME's Trainer retains observation identity keys for its lifetime, and inbound reassembly buffering should be separately reviewed before very long, adversarial or dense trials. These were **not** modified because changing duplicate/training acceptance or reassembly limits can change experimental/protocol behavior.

## Changes

- **Lazy import:** `flame/federated_learning/__init__.py` exposes its public MNIST classes on demand. `clive/flame_protocol.py` imports MNISTTrainer only for actual Cluster Heads using the `mnist` trainer. A clean sensor import no longer loads torch.
- **Shared class-sample cache:** `clive/mnist_cache.py` creates ten class-specific binary shards in `mnist/.clive-mnist-cache/`. This operation happens once before launching nodes; shards retain the original MNIST row order. Each sensor reads only the chosen 784-byte images and appends its original digit byte, using the **same RNG seed and random picks**. Cache reuses source metadata and verifies shard lengths, and supports locking and atomic writes. Cache files are excluded from the release ZIP; they are regenerated locally on first use (~47 MB of disk space, independent of node count).
- **Optional radio thread:** `runtime.radio_execution` accepts `process` (the unchanged default) or `thread`. Both call the same `_radio_worker_main` and `_RadioEngine` and use the same UDP traffic path, packet-level calculations, counters, and token buckets. `thread` runs within each Node process and avoids a second Python interpreter. It shares a GIL, so high CPU use may alter scheduling accuracy. Use `process` mode for timing baselines unless you have validated the equivalence under your actual workload.
- **GUI:** `runtime.radio_execution` appears as a dropdown in General settings. Existing GUI controls (Save/Validate/Start/Stop) remain available.
- **Capacity preset:** `configs/demos/capacity_20ch_200sn_memory.json` can be loaded and modified from the GUI. It uses **actual MNIST samples** but the lightweight `mean_pixel` testing trainer; **it does not test real PyTorch FL, model exchange, or accuracy**. Its isolated clusters are deliberate so the trial measures 220-node overhead and the first observation/ACK path.

## Measurements

Recorded through `benchmarks/profile_run.py` using cgroup `memory.current`, sampled at approximately 0.15 s intervals. These are **additional measured peak cgroup memory above the pre-run baseline**, not a per-process private-heap measurement. This particular test container had a 4 GiB memory limit and five logical CPUs; file cache and unrelated cgroup activity affect readings. Measurements are approximate and environment-dependent.

| Scenario | Radio backend | Additional peak memory | Outcome |
|---|---|---:|---|
| 2 CH + 58 SN, real MNIST sampling and PyTorch trainer initialization, short (no local training) | `process` | 1.28 GiB | Passed |
| 2 CH + 58 SN, otherwise identical | `thread` | 1.11 GiB | Passed |
| 10 CH + 100 SN, real MNIST sampling, PyTorch trainer initialized, short (no local training) | `thread` | 3.30 GiB | Passed, nearly at 4 GiB limit |
| 2 CH + 2 SN, **real MNIST training & 399-fragment model transfers**, 12 s | `thread` | 0.56 GiB | Passed; 15 training and 15 aggregation events per CH |
| 20 CH + 200 SN, real MNIST samples, **mean_pixel** trainer, 6 s | `thread` | 2.28 GiB | Passed; 200 JOINs, 400/400 completed sensor transfers, zero transfer failures |
| 20 CH + 200 SN, attempted longer trial, mean_pixel | `thread` | — | **Did not complete: 4 GiB container OOM during startup**. Child processes were cleaned up. |
| 6 CH + 60 SN, real MNIST samples, mean_pixel, 26 s | `thread` | 0.78 GiB | Passed; 480/480 sensor transfers |

The 6CH/60SN sustained trial's cgroup memory was around 1,056 MB at six seconds and 1,058 MB near completion, approximately a 2 MB difference. It shows no rapid monotonic growth within this bounded test, not a proof of zero leaks in arbitrary scenarios.

### Why the two 220-node trials differ

The test container is memory-constrained (4 GiB and no swap). Parallel worker creation and runtime/page-cache activity can have variable peaks. A successful 6-second trial is not sufficient to guarantee that all future trials will initialize successfully. With 20 real PyTorch Cluster Heads and model traffic, **220-node capacity is not validated** on this machine. Run the relevant full-size scenario on a machine with substantially more available RAM and monitor `memory.current` or PSS throughout the experiment.

## Verification

- `python -m pytest -q tests flame` → **183/183 passed**.
- `python clive/validation/run_all.py` → **all CLIVE validation modules passed**, including the new memory regression suite.
- Exact RNG/payload parity with the original MNIST algorithm verified for SN-1, SN-3, SN-6, SN-10, and SN-31 (128 samples each).
- Repeatedly constructing 100 sensor sample sources and releasing them three times left about 18 KB of additional traced Python allocations.
- Compact radio end-to-end synthetic FLAME smoke test passed (training and aggregation included).
- Real MNIST compact radio test passed: both CHs received all 16 observations, trained 15 times, and successfully reassembled and aggregated 15 peer models (399 model fragments each).
- 220-node capacity trial: 200/200 sensors joined and 400/400 sensor transfers succeeded, without transfer failures.

## How to run

```bash
./start_gui.sh
```

Open the browser GUI. In **General**, choose the number of CH/SN, select **Radio Execution → Thread** to save memory, and load the `capacity_20ch_200sn_memory` preset for a tested transport capacity example. Other runtime, radio, mobility, and FLAME settings remain editable from the GUI.

For automated verification from the extracted project root:

```bash
python -m pytest -q tests flame
python clive/validation/run_all.py
python benchmarks/profile_run.py --output benchmarks/my-run.json -- \
  python clive/clive.py --scenario clive/configs/demos/capacity_20ch_200sn_memory.json --results results
```

**Be careful with large trials on low-memory machines.** The monitor reports whole-cgroup memory and cannot attribute unrelated use to an experiment. For final thesis timing metrics, repeat comparable experiments with the original process backend, check dropped events and ACK failures, and validate timing overhead; the threaded backend may become CPU-bound.
