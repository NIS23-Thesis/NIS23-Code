# CLIVE process architecture

The emulation runtime intentionally uses **no Python worker threads**.

```text
Emulator supervisor process
│
├── Node process: CH-1
│   ├── UDP receive
│   ├── protocol callbacks
│   └── experiment-time protocol timers
│   └── Radio/TX child process: CH-1
│       ├── TX scheduling / token buckets
│       ├── broadcast fan-out
│       ├── distance and RSSI
│       ├── bandwidth / PLR / IPDV
│       ├── TX energy
│       └── UDP sendto
│
├── Node process: CH-2
│   └── Radio/TX child process: CH-2
└── ...
```

The node/protocol process and radio/TX process have separate Python interpreters and therefore
separate GILs. They communicate through `multiprocessing.Pipe`. Each of these processes is
single-threaded from CLIVE's perspective, so there is no intra-node Python worker-thread GIL
contention in the emulation execution path.

This does **not** remove normal OS scheduling limits. For N virtual nodes CLIVE creates
approximately 2N emulation worker processes (N node/protocol + N radio/TX), plus the supervisor.
At very high load, the host can still miss real-time deadlines because there are more runnable
processes than CPU cores.

## Mobility memory architecture

The full mobility trace is no longer embedded in `plan.json` and no worker deserializes a nested
Python dictionary containing every node at every tick.

```text
plan.json
  small metadata/configuration only
        │
        └── trace_storage -> mobility.bin

mobility.bin
  one compact [tick][node][x,y] float64 matrix
        │
        ├── mmap READ-ONLY -> Radio CH-1
        ├── mmap READ-ONLY -> Radio CH-2
        ├── mmap READ-ONLY -> Radio SN-1
        └── ...
```

Only radio/TX child processes open the trace, because they perform distance/link calculations.
Node/protocol processes do not map the mobility trace. Every radio child maps the same file
read-only, allowing Linux to share the file-backed pages through the page cache instead of
maintaining a full Python-object copy per process.

For 110 nodes, 300 s, and a 0.1 s mobility interval, the raw trace contains 3001 × 110 × 2
coordinates. Stored as float64 this is about 5.28 MB total for the entire run, not per node.

The browser visualizer also reads `mobility.bin` through the plan metadata. It still shows the
exact stored trace ticks and does not interpolate mobility.

## Scalability optimizations

Normal evaluation uses the process-isolated architecture without paying debug-mode costs:

- Broadcast send requests no longer serialize a list of every recipient through the node→radio IPC pipe. The radio child expands the broadcast from its immutable node table.
- Packet metrics update compact counters directly when `packet_events=false`; CLIVE does not construct per-recipient diagnostic JSON records that will never be written.
- Explicit-range radio checks use squared distance first. Out-of-range peers in normal evaluation avoid unnecessary square-root and RSSI/logarithm work.
- The emulator supervisor blocks on worker control pipes using `multiprocessing.connection.wait()` instead of polling every worker every few milliseconds.
- Idle radio children can sleep longer because their pipe wakes them immediately for new commands and their action heap supplies the next exact TX/delivery deadline.
- The browser loads `mobility.bin` directly as an `ArrayBuffer` and reads exact float64 tick coordinates with `DataView`; Python no longer expands the entire trace into a large nested JSON payload for normal playback.

These changes preserve exact discrete mobility ticks and the existing link model. Diagnostic packet/event JSONL remains available only when explicitly enabled.
