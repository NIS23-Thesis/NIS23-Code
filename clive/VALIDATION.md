# CLIVE localhost validation report

Validation performed on 2026-10-07 after removing multi-host/LAN support.

## 1. NodeSocket behavioral acceptance

Command:

```bash
python3 evaluation/validate_nodesocket_acceptance.py
```

Result: **7/7 checks passed**.

Validated:
- Table 3-3 RSSI -> bandwidth / PLR / IPDV mapping
- Equations 3-13 and 3-14 energy calculations
- common FLAME header parsing for metrics/logging
- direct localhost UDP delivery
- out-of-range drop before kernel UDP send
- token-bucket transmission delay approximately equal to `L/R`
- pairwise broadcast receiver behavior

## 2. End-to-end CLIVE validation

Command:

```bash
python3 evaluation/validate_clive.py --stress-200
```

Result: **PASS**.

Validated:
- 4 CH + 20 SN thesis low-density plan generation
- unique `127/8` loopback endpoint per node
- complete precomputed mobility trace
- 200-node plan generation
- protocol interface contains no `step`, `tick`, `on_tick`, `next_wakeup`, or `on_timer` callback
- end-to-end Emulator -> NodeBootstrapper -> NodeSocket process run
- validation protocol sends repeatedly every 70 ms while the mobility quantum is 500 ms, proving protocol execution is not driven by mobility ticks
- protocol datagrams reach peer callbacks
- **200 independent localhost node processes** start, run, stop, and produce 200 metrics files

## 3. Standalone 200-node runner

Command used for a short smoke run:

```bash
python3 run_200_nodes.py --duration 0.25 --startup-delay 0.2 --results /tmp/clive-results-200-script-test
```

Result: **PASS** (`success=true`, 200 metrics files).

## 4. Normal launcher

Command:

```bash
bash deploy.sh
```

Result: **PASS** using `localhost.json` with 4 CH + 20 SN and `NullProtocol`.

## Not validated here

- FLAME itself is not included in this CLIVE package, so FLAME protocol correctness was not tested.
- `iperf3` was not available in the validation environment, so the optional real-iperf bridge was not executed in this pass.
- A 200-node run with a real FLAME implementation will have substantially higher CPU, memory, and traffic load than the 200-node `NullProtocol` process stress test.
