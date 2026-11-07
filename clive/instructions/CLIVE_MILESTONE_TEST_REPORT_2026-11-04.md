# NIS23 — CLIVE-only integration review, specification alignment and execution evidence

**Date:** November 4, 2026  
**Source:** `NIS23-IntegrationOnly-Readable-Optimized.zip`  
**Reference documents:** `NIS23_Thesis Theoretical and Methodology(20261104-123248).pdf` (especially §§3.2, 3.3, 4.1, 4.7) and `NIS23-Milestones_Form.pdf` (pp. 1–3).  
**Change boundary:** `clive/` and CLIVE-side validation/documentation only. **No file inside `flame/` was changed.**

## Executive verdict

- The **integration is functional** for short, suitably connected real-MNIST/PyTorch demonstrations: packet transport, sensor association, training and decentralized model exchange have been exercised.
- The **300-second methodology run does not establish reliable federated exchange**. It had 238 local training events, but **0/62 completed CH model transfers and zero model aggregations**. It nevertheless reported successful worker/process execution. Keep those statuses separate.
- **All 13 renamed demo presets were exercised**: 12 bounded shortened diagnostic smoke runs passed, plus one full-as-configured 220-node/six-second *transport capacity* run passed. The 220-node run had no neural-network training and no model aggregation; it cannot be presented as federated-learning scalability.
- The **108 five-minute thesis scenarios were generated and validated**, but **only one** of those 108 was executed at its full 300-second duration. The full 108-case empirical campaign has **not** been completed.
- All 183 FLAME unit tests pass, and the complete CLIVE validation suite including the new thesis and event-view tests reports PASS. These tests do not invalidate the full-duration model-transfer failure.

## Changes made in CLIVE (without editing FLAME)

1. **Thesis-spatial MNIST assignment.** Each Sensor Node selects one of ten digit zones based on its *initial* precomputed 2D position (5 columns × 2 rows). It keeps that label while moving; this is a deterministic clustered-data approximation, not an exact reimplementation of the paper's illustrative scatter plot. Legacy sensor-ID assignment remains available through `mnist_distribution: sensor_id`.
2. **Unique MNIST records within the sensor's pool.** The CLIVE cache now selects without replacement. Finite sample pools no longer contain hidden repeated images selected by the sampler. The optional `repeat_sensor_samples` setting still allows deliberate cycles.
3. **Optional final-CH MNIST accuracy and model-weight output.** When `evaluate_accuracy` and/or `save_final_model` are enabled, CLIVE evaluates the final model on the 10,000-example held-out MNIST test split and optionally writes local `.pt` weights. These are CLIVE-only postprocessing features; FLAME's training and aggregation functions remain untouched.
4. **Out-of-range radio accounting.** Packets emitted into unreachable peers are counted as transmitted, including modeled TX energy and attempted delivery, rather than vanishing from transmission totals. This is bookkeeping, not proof that the energy equation is physically calibrated.
5. **Thesis matrix.** Added 108 reproducible configurations crossing 4 mobility modes, 3 density levels, 3 observation intervals, and 3 area sizes. These use real MNIST/PyTorch and a nominal five-minute duration. `run_evaluations.py` now rejects a short thesis duration unless the caller explicitly requests a diagnostic override.
6. **Demonstration naming.** Renamed 13 ambiguous or inaccurate demo filenames. Particularly, the former `large_10ch_100sn.json` actually contained **10 CH and 1 SN**, and is now `topology-gauss-markov-10ch-1sn.json`. The former 220-node capacity demo is now clearly labeled *transport-only/no neural training*. Read `clive/configs/demos/README.md` for the complete 13-entry catalog and the workload for each preset.
7. **GUI validity and readability.** Metadata `manifest.json` is no longer incorrectly listed or validated as a scenario. The Control Center accepts its bundled 20 CH + 200 SN capacity preset (individual role maximum 200, total maximum 220). Thesis-matrix presets display human-readable dimensions instead of `m-..._d-...` abbreviations. A prior event-stream refactor with sequence-number visibility and human-readable descriptions remains intact.
8. **Tests.** Configuration validation ignores metadata files; custom integration and frontend validations are included in `clive/validation/run_all.py`. Fixed an indefinite stderr-read hang in the GUI HTTP lifecycle test and removed a stale test-generated GUI preset. Added a reusable bounded demo smoke runner and a one-hop CH connectivity diagnostic.

## Test evidence: observed results, not expectations

| Check | Result | Evidence / interpretation |
|---|---|---|
| FLAME baseline Python tests | **183/183 PASS** | `pytest_last.log`; FLAME unchanged |
| CLIVE full non-stress validation | **PASS**, including 15 scripts/check groups | `complete_suite.log`; includes FLAME integration and visualizer validation |
| All scenario JSONs | **139/139 PASS** configuration planning | Includes 108 matrix cases; not process execution |
| Thesis alignment tests | **7/7 PASS** | Dimensions, duration guard, unique samples, spatial classes, GUI validation |
| Adapter event/retry regressions | **6/6 PASS** | Integration only |
| Event-query/timer regressions | **3/3 PASS** | Event ordering, truncation, housekeeping |
| Control Center HTTP lifecycle | **5/5 PASS** | Load, validation, run, stop, cleanup |
| Event Stream JS validation | **PASS** | Event presentation logic; not a full cross-browser pixel review |
| Bounded demo run matrix | **12/12 PASS** | **Shortened durations** and compact radio execution; these are not thesis evaluations |
| 220-node capacity preset | **PASS (6 seconds)** | 20 CH + 200 SN; 200 joins, 400/400 completed sensor transfers; **0 training** |
| 300-second thesis case | **Worker PASS / FL EXCHANGE FAIL** | Real MNIST/PyTorch, 4 CH + 20 SN; 238 training events, 0/62 model transfers, 0 aggregations |
| Full 108-case, 300-second campaign | **NOT EXECUTED** | 108 plans valid; only one case has full-duration execution evidence |

The 220-node run peaked at **3,780,739,072 bytes (about 3.78 GB decimal / 3.52 GiB)** of whole-cgroup memory, against a 4 GiB limit. The real 220-node PyTorch FL workload is *not* validated.

## Full 300-second scenario: significant finding

**Scenario:** `clive/configs/evaluations/thesis_matrix/m-none_d-low_f-high_a-small.json`. Real MNIST, real PyTorch trainer, 4 CH + 20 SN, 200 m × 200 m, 15-second sensing, no mobility, seed 810, five minutes.

**Application results:** 238 local training events; 62 tracked model transfer versions started; **0 completed, 62 failed**; 0 model aggregations. Final per-CH held-out accuracy: CH-1 17.14%, CH-2 27.17%, CH-3 9.58%, CH-4 27.46%; mean **20.3375%**. Worker exit success: true. The standard run emitted 13,128 datagrams, 18,408 recipient-delivery attempts, 5,558 recipient drops. Broadcast fanout is why receive/delivery counters must not be divided naively to calculate a PDR.

**Initial CH topology:** Only CH-1↔CH-3 were within one-hop range, separated by 48.27 m and assigned 10% per-datagram loss. CH-2 and CH-4 were isolated. Because this is a static scenario, the isolation persists. Thus no model exchanges from CH-2 or CH-4 is consistent with the network topology. Failure of *all* attempted model transfers between CH-1 and CH-3 is still unresolved; the known FLAME ACK/partial-ACK defects, radio loss, and transfer overlap are plausible contributors and have not been isolated by this run.

**Radio-model caveats:** The configured `range_mode=rssi`, reference −30 dBm and path-loss exponent 3 give a calculated −85 dBm cutoff at **68.13 m**, not exactly the nominal 70 m in the methodology. Modeled TX energy for this run was about **831,541 J**; this is the simulator's estimate and should **not** be interpreted as calibrated hardware power consumption. The energy formula's distance exponent and coefficient units require methodology review.

## Milestone/requirement review

| Milestone / thesis requirement | Status supported by tests | Qualifications |
|---|---|---|
| FLAME initialization and protocol state | Exercised | FLAME untouched; tested via integration/unit checks |
| Sensing module / real MNIST observations | Exercised | Spatial-region approximation; sensors retain initial digit class |
| Communication / UDP / fragments / ACKs | **Partial** | Short controls passed; 300-second lossy CH exchange failed |
| Neighbor discovery, JOIN/JOIN_ACCEPT/LEAVE, timeout | Exercised | Six-node E2E exercises all eight packet types; no claim of all mobility schedules |
| FL training and aggregation | **Partial** | Short real-PyTorch aggregation succeeds; full 300-second run trained but did not aggregate |
| CLIVE orchestrator & reproducible mobility | Plan validation PASS | 139 runnable configs, includes 108 planned thesis scenarios |
| Emulator, bootstrapper, Node/NodeSocket | E2E checks PASS | Localhost paths verified; multi-host LAN not validated |
| Link effects & packet logging | Unit + end-to-end checks PASS | Radio calibration and energy realism not fully validated |
| Ping/iperf-style link validation | Partial | Link-throttling checks run; this review did **not** execute a fresh external ping/iperf comparison |
| Four-variable testing permutations | Prepared, **not executed in full** | 108 cases × minimum 300 seconds, plus repetitions if required |
| MNIST clustered-topology interface | Implemented approximation, unit tested | Initial geographic zones; distribution assumptions should be documented |
| Evaluation metrics and saved models | **Partial** | Final accuracy and weights implemented. Per-training-round goodput/throughput and normalized broadcast-aware PDR require further work |
| Final scalability and FL learning claims | **Not established** | Six-second 220-node proxy cannot demonstrate PyTorch learning scalability |

## Unchanged FLAME-owned limitations

No fixes were made to the FLAME ACK-window synchronization failure, partial-ACK retry exhaustion, 255-update-ID limit, pickle model deserialization, or underlying aggregation semantics. The previous integrated baseline already contained two historical differences from the older standalone FLAME validation repository (first-sensor state ordering and lazy trainer imports); this release does not change either.

## Reproduction commands

From the extracted project root:

```bash
python -m pytest -q tests
cd clive
python validation/run_all.py
python validation/execute_demo_smoke_matrix.py --results results/demo-smokes
python scripts/run_evaluations.py thesis_matrix --list
# Full, genuine 300-second thesis case:
python clive.py --scenario configs/evaluations/thesis_matrix/m-none_d-low_f-high_a-small.json --results results/thesis
# Interpret a finished run's initial CH connectivity and FL completion:
python scripts/analyze_connectivity.py results/thesis/<actual-run-directory>
```

Run the 108-case matrix only on appropriately provisioned hardware and preserve the independent seed, topology, environment, experiment version, and run outcome. Running this full campaign at 300 seconds each is at least nine hours of nominal simulated wall-clock time if executed serially, excluding startup, repetitions, and resource failures. Merely generating/validating 108 JSON files is not equivalent to executing them.

**Research interpretation:** The revised archive is a more truthful, more reproducible CLIVE/integration release. It is **not** a certified FLAME-reliable research release; the five-minute test is affirmative evidence that substantial model-exchange failures remain.
