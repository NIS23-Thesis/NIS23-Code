# CLIVE NodeSocket Validator (`validate_clive.py`)

## Purpose

`validate_clive.py` is an integration-style validator for CLIVE's `NodeSocket`.
It creates controlled Cluster Head (CH) topologies, sends real UDP traffic through
`NodeSocket`, and checks whether the observed behavior matches the configured link
model.

This validator tests the emulation layer. It does **not** implement or validate the
full FLAME protocol, application-layer reliability, MANET routing, or forwarding.
CLIVE is treated as single-hop.

## What It Tests

The validator has four selectable test modes:

### `single`
Creates two CHs at a fixed distance and validates one emulated link.

Checks:
- RSSI-derived link band
- reachability
- UDP echo delivery
- round-trip packet loss
- IPDV samples
- expected link bandwidth and UDP goodput
- optional real `iperf3` throughput

Example:

```bash
python3 validate_clive.py --tests single --distances 1,20,40,100
```

With throughput measurement:

```bash
python3 validate_clive.py --tests single --iperf --distances 1,20,40,100
```

### `pairwise`
Creates multiple fixed CHs and tests each CH pair individually.

For example:

```bash
python3 validate_clive.py --tests pairwise --positions 0,20,40,100
```

This creates:

```text
CH-1 = 0 m
CH-2 = 20 m
CH-3 = 40 m
CH-4 = 100 m
```

It then checks links such as `CH-1 -> CH-2`, `CH-1 -> CH-3`, `CH-2 -> CH-4`, and so on.

Use `--bidirectional` to test both directions for every pair:

```bash
python3 validate_clive.py --tests pairwise --positions 0,20,40,100 --bidirectional
```

### `concurrent`
Creates multiple CHs and makes `CH-1` send to all other CHs simultaneously.

This checks that:
- multiple unicast flows can coexist;
- reachable receivers receive traffic;
- unreachable receivers receive none; and
- the aggregate sender transmission rate does not exceed the configured sender-wide maximum rate.

Example:

```bash
python3 validate_clive.py \
  --tests concurrent \
  --positions 0,20,40,100 \
  --max-rate 20000000 \
  --offered-rate 10000000
```

`--offered-rate` is the rate offered **per flow**.

This test does **not** claim to model full IEEE 802.11 contention or interference
between independent transmitters. It validates NodeSocket's current sender-wide rate
ceiling and simultaneous traffic behavior.

### `broadcast`
Creates one broadcasting CH and multiple receiving CHs.

Checks that:
- one broadcast counts as one logical sender transmission;
- each receiver is evaluated independently using its own distance/RSSI conditions;
- packet loss differs per receiver according to the pairwise link; and
- delivery-attempt accounting is correct.

Example:

```bash
python3 validate_clive.py --tests broadcast --positions 0,20,40,100
```

If 100 broadcasts are sent to 3 receivers, the expected accounting is:

```text
logical transmissions = 100
delivery attempts      = 300
```

## Running Multiple Tests

Run selected modes:

```bash
python3 validate_clive.py --tests single,pairwise,broadcast \
  --distances 1,20,40,100 \
  --positions 0,20,40,100
```

Run everything:

```bash
python3 validate_clive.py --tests all \
  --distances 1,20,40,100 \
  --positions 0,20,40,100
```

List available test modes:

```bash
python3 validate_clive.py --list-tests
```

## Command-Line Arguments

| Argument | Meaning | Default |
|---|---|---:|
| `--tests` | `single`, `pairwise`, `concurrent`, `broadcast`, or `all` | `single` |
| `--distances` | Distances used by `single` | `1..100` |
| `--positions` | Fixed x-axis CH positions used by multi-node tests | `0,20,40,100` |
| `--packets` | Packets per distance in `single` | `1000` |
| `--multi-packets` | Packets per pair/broadcast in multi-node tests | `300` |
| `--interval` | Packet interval for echo/pairwise/broadcast tests | `0.005 s` |
| `--iperf` | Enable real `iperf3` throughput measurement in `single` | off |
| `--iperf-payload` | UDP payload used by `iperf3` | `1200 B` |
| `--seconds` | Duration for iperf and concurrent tests | `3 s` |
| `--max-rate` | Configured NodeSocket maximum data rate | `1e9 bps` |
| `--offered-rate` | Concurrent traffic offered per flow | `2e6 bps` |
| `--payload-bytes` | Concurrent-test UDP payload size | `1200 B` |
| `--bidirectional` | Test both directions in pairwise mode | off |
| `--seed` | Random seed | `810` |
| `--output` | Result directory | `validation-results` |

## How the Single-Link Test Works

For each requested distance, the validator creates exactly two CHs:

```text
CH-1 at (0, 0)
CH-2 at (distance, 0)
```

The nodes are static for the test. The validator sends UDP echo traffic through their
`NodeSocket`s and compares observed packet loss against the expected value from the
link model.

Round-trip echo loss is not the same as one-way PLR. If one-way PLR is `p`, the
expected echo loss is:

```text
1 - (1 - p)^2
```

because the packet must survive both `CH-1 -> CH-2` and `CH-2 -> CH-1`.

## `iperf3` and `iperf_bridge.py`

`iperf3` is used only when `--iperf` is supplied.

`iperf3` cannot directly call Python's `NodeSocket.sendto()` and `NodeSocket.recvfrom()`.
`iperf_bridge.py` therefore acts as an adapter:

```text
iperf3 client
    -> normal localhost UDP socket
    -> iperf_bridge.py
    -> NodeSocket A
    -> CLIVE emulated link
    -> NodeSocket B
    -> iperf_bridge.py
    -> normal localhost UDP socket
    -> iperf3 server
```

`iperf3` generates traffic and measures throughput. `iperf_bridge.py` moves that
traffic into and out of NodeSocket. `NodeSocket` applies the actual CLIVE link model.

The bridge itself does not determine RSSI, PLR, IPDV, or the Table 3-3 bandwidth band.

### Host-Limited Throughput

A throughput result below the configured rate does not automatically prove the
NodeSocket limiter is wrong. The Python bridge, packet logging, operating-system
sockets, CPU, and packet size can become the bottleneck first.

For example, a 1200-byte UDP payload at 1 Gbps requires roughly 104,000 datagrams per
second. That is a much harder host-side workload than the same aggregate bit rate with
large datagrams.

For a diagnostic rate-shaping test, it is valid to use a lower `--max-rate` and verify
that the 100%, 44%, and 20% bands scale correctly. Do not describe the lowered rate as
the thesis maximum; it is a host-safe diagnostic configuration.

## Output

Each run creates a timestamp/randomized directory under `validation-results/` (or the
path passed with `--output`). It contains JSON summaries and NodeSocket metrics/logs
for later inspection.

The command exits non-zero if a selected test fails.

## Recommended Demo Commands

Fast single-link demonstration:

```bash
python3 validate_clive.py --tests single --distances 1,20,40,100
```

Multiple CH pairwise demonstration:

```bash
python3 validate_clive.py --tests pairwise --positions 0,20,40,100
```

Broadcast demonstration:

```bash
python3 validate_clive.py --tests broadcast --positions 0,20,40,100
```

Host-safe throughput demonstration:

```bash
python3 validate_clive.py \
  --tests single \
  --iperf \
  --distances 1,20,40,100 \
  --max-rate 20000000
```

## What This Validator Proves

It provides integration-level evidence that NodeSocket applies the configured link
model to real UDP traffic under controlled topologies.

It does not prove that the complete FLAME protocol is correct, and it does not model
multi-hop routing or full wireless-medium contention.
