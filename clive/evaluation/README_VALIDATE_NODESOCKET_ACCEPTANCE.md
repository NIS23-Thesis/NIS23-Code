# NodeSocket Acceptance Validator (`validate_nodesocket_acceptance.py`)

## Purpose

`validate_nodesocket_acceptance.py` is a thesis-driven acceptance test suite for
CLIVE's `NodeSocket`.

Its main question is:

> Does the observable NodeSocket behavior satisfy the specific rules and equations
> expected by the thesis-derived design?

It is intentionally smaller and more controlled than `validate_clive.py`. Most tests
construct tiny static plans, usually two nodes, and test one requirement at a time.

## Run It

Place the validator beside `nodesocket.py` and `orchestrator.py` and run:

```bash
python3 validate_nodesocket_acceptance.py
```

Run the additional statistical packet-loss checks:

```bash
python3 validate_nodesocket_acceptance.py --statistical
```

## What It Tests

The default suite checks:

### Link-model mapping

Verifies representative distances against the expected RSSI-derived conditions:

```text
1 m   -> 100% bandwidth, 0% PLR, 1 ms IPDV band
20 m  -> 44% bandwidth, 2% PLR, 5 ms IPDV band
40 m  -> 20% bandwidth, 10% PLR, 25 ms IPDV band
100 m -> unreachable, 0 bandwidth, 100% PLR
```

### RSSI path-loss formula

Checks that RSSI is calculated using the configured reference RSSI, path-loss exponent,
and reference distance.

### Common-header parsing

Constructs a FLAME-style four-byte common header and verifies that NodeSocket extracts:
- protocol version;
- packet type;
- role;
- padding; and
- sequence number.

### Energy equations

Checks the configured TX and RX energy equations against independently calculated
expected values.

### Near unicast and metrics

Sends a packet between nearby nodes and verifies:
- packet delivery;
- returned overlay source address;
- packets/bits sent;
- packets/bits received; and
- per-packet-type metric accounting.

### LAN overlay/underlay translation

Uses different overlay and underlay addresses and verifies that NodeSocket translates
correctly while still returning the virtual/overlay peer address to the caller.

### Out-of-range behavior

Places two nodes beyond the reachable range and verifies that the packet is not
delivered and the delivery is recorded as a drop.

### Transmission-delay accounting

Uses a deliberately low data rate so `L/R` is measurable and checks that transmission
delay is applied once rather than accidentally counted twice.

### Token-bucket bandwidth ratios

Uses a host-safe 1 Mbps maximum and checks that the 100%, 44%, and 20% link bands are
actually paced at approximately those rates.

This is intentionally scaled down so host/Python capacity does not hide whether the
rate limiter itself works.

### Broadcast policy

Uses one sender, one near receiver, and one out-of-range receiver to verify that:
- one broadcast is one logical TX;
- there are separate delivery attempts for each receiver;
- the near receiver can receive it;
- the out-of-range receiver does not; and
- per-receiver bandwidth/reachability is preserved.

## Optional `--statistical`

The optional statistical mode sends 1000 packets at representative distances and
checks whether observed PLR is reasonably close to the expected probability.

Example:

```bash
python3 validate_nodesocket_acceptance.py --statistical
```

This is useful because PLR is probabilistic. A 2% PLR does not mean exactly two of
every 100 packets must be dropped in every short run.

## Output

Each test is reported as one of:

```text
PASS
FAIL
CHECK
```

`FAIL` means the observed behavior did not match the acceptance condition used by the
validator.

`CHECK` is reserved for design behavior that is not clearly specified enough to be
treated as unambiguously correct or incorrect.

The script exits with a non-zero code when one or more acceptance tests fail.

## What This Validator Does Not Do

It does not:
- run `iperf3`;
- attempt a literal 1 Gbps host throughput benchmark;
- test a large multi-node topology;
- validate the FLAME application protocol;
- test routing or packet forwarding; or
- simulate full IEEE 802.11 contention/interference.

Its purpose is requirement-by-requirement NodeSocket acceptance testing.

## When to Use It

Use this validator when someone asks:

> How do you know the implementation follows the NodeSocket design and thesis rules?

Use `validate_clive.py` when someone asks:

> What happens when you actually send traffic between nodes, vary distance, run multiple
> CHs, broadcast, or measure throughput?
