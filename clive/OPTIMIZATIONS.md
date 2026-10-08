# CLIVE optimization notes

The current build keeps the user's requested process isolation while reducing avoidable CPU, memory, IPC, disk, and visualizer overhead.

## Kept intentionally

- One node/protocol OS process per virtual node.
- One radio/TX child OS process per virtual node.
- No Python worker threads in the emulation path.
- Exact precomputed discrete mobility positions; no visual interpolation.
- Full per-recipient radio semantics for distance, reachability, bandwidth, PLR, IPDV, energy, and delivery.

## Optimized

1. **Mobility memory** — one read-only `mobility.bin` is memory-mapped by radio processes; node processes do not load the full trace.
2. **Broadcast IPC** — a broadcast command no longer carries all N−1 target names. The radio child already knows the node table and expands the broadcast locally.
3. **Radio calculations** — normal explicit-range evaluation checks squared distance before `sqrt`/`log10`; unreachable peers avoid calculations only needed by debug logs.
4. **Metrics** — normal evaluation updates counters directly. Per-recipient event dictionaries/JSON serialization are performed only when packet debugging is enabled.
5. **Supervisor** — readiness/completion uses blocking connection waits rather than O(N) tight polling loops.
6. **Idle wakeups** — default node I/O health polling is 10 ms instead of 2 ms; socket traffic and protocol deadlines still wake the node immediately/at the required deadline. Idle radio workers block up to 250 ms but wake immediately on IPC or a scheduled radio action.
7. **Visualizer** — the browser directly downloads the canonical binary trace once, then selects exact ticks locally. Python does not materialize the full trace as JSON for normal playback.
8. **Disk output** — normal runs retain only `plan.json`, `mobility.bin`, `run-manifest.json`, and compact run-level `metrics.json`; JSONL diagnostics and `mobility.tsv` remain opt-in.

## Remaining real limit

Process isolation removes same-process GIL contention, but it does not create CPU cores. A 110-node run creates about 220 emulation worker processes, and broadcast-heavy workloads still perform O(N²)-scale pairwise link work. If the host cannot execute that work in real time, CLIVE should treat that as a host-capacity/timing limitation rather than a mobility-trace error.
