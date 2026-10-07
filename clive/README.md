# CLIVE - localhost-only emulator

This revision removes the LAN / multi-host deployment path. CLIVE now runs entirely on one physical machine using Linux loopback addresses (`127/8`). Each virtual node still runs as an independent Python process and communicates through real UDP sockets, while NodeSocket applies the thesis-defined mobility and wireless-link constraints.

## Architecture

The implementation follows the localhost branch of the NIS23 methodology:

1. **Orchestrator** precomputes the Gauss-Markov mobility trace before execution and creates one localhost record per node: `node_name`, `node_role`, `ip`, and `port`.
2. **Emulator** launches one `multiprocessing.Process` per virtual node.
3. **NodeBootstrapper** creates the node's `NodeSocket`, loads the selected protocol class, waits for the shared experiment epoch, and dispatches received datagrams.
4. **NodeSocket** binds directly to the node's loopback address. There is no overlay/underlay translation. It uses experiment time only to select the current precomputed mobility position and apply distance -> RSSI -> bandwidth/PLR/IPDV, token-bucket rate limiting, delay, and energy accounting.
5. **ProtocolInterface** is the boundary for FLAME or another protocol under test. CLIVE does **not** call `step(tick)`, `on_tick()`, or any protocol timer callback. Protocol implementations own their HELLO timers, sampling timers, retransmission timers, aggregation timers, and worker threads.

The mobility trace still has a 100-500 ms quantum because that is how the emulator samples topology. That quantum is **not** a protocol clock.

## Protocol integration

A protocol must subclass `ProtocolInterface`:

```python
from protocol_interface import ProtocolInterface

class MyProtocol(ProtocolInterface):
    def start(self):
        # Start protocol-owned workers/timers here.
        pass

    def on_datagram(self, data, source):
        # Parse/dispatch an incoming UDP datagram here.
        pass

    def close(self):
        # Stop protocol-owned workers/timers here.
        pass
```

The protocol receives the CLIVE node runtime as `self.node` and can use:

- `self.node.spec` - role/name/address information
- `self.node.plan` - experiment configuration
- `self.node.sendto(data, destination)` - emulated UDP transmission
- `self.node.elapsed()` - experiment-relative time
- `self.node.is_active()` - experiment/stop state

Run a protocol with:

```bash
python3 clive.py --scenario localhost.json --protocol your_module:YourProtocol
```

`example_protocol.py` contains `IndependentProbeProtocol`, which is validation-only and deliberately uses a 70 ms protocol-owned timer while the mobility quantum is 500 ms. This demonstrates that the protocol is not mobility-ticked.

## Normal localhost run

```bash
bash deploy.sh
```

or:

```bash
python3 clive.py --scenario localhost.json --results results
```

`localhost.json` uses the thesis' low topology-density level of 4 Cluster Heads + 20 Sensor Nodes. Its short duration is intended as a functional demo; use the thesis experiment duration and scenario parameters for actual measurements.

## 200-node script

`run_200_nodes.py` creates **200 independent virtual nodes** by default: 20 Cluster Heads + 180 Sensor Nodes.

Plan/configuration check only:

```bash
python3 run_200_nodes.py --plan-only
```

Launch all 200 node processes with no protocol traffic (`NullProtocol`):

```bash
python3 run_200_nodes.py
```

Launch 200 nodes using your protocol implementation:

```bash
python3 run_200_nodes.py --duration 30 --protocol your_module:YourProtocol
```

The 200-node composition is a **scalability stress scenario**, not a thesis Table 4-8 density level. Table 4-8 tops out at 10 CH + 100 SN.

## Validation

Run the NodeSocket behavioral checks:

```bash
python3 evaluation/validate_nodesocket_acceptance.py
```

Run end-to-end CLIVE validation, including the protocol-decoupling check and 200-node plan generation:

```bash
python3 evaluation/validate_clive.py
```

Also launch a short 200-process stress test:

```bash
python3 evaluation/validate_clive.py --stress-200
```

The validators check direct localhost UDP delivery, RSSI/link mapping, energy formulas, unreachable-link drops, bandwidth delay, broadcast pairwise behavior, process supervision, result files, and the protocol interface.

## Main files

- `orchestrator.py` - configuration validation and precomputed Gauss-Markov mobility
- `emulator.py` - localhost process supervisor
- `nodebootstrapper.py` - per-process node runtime and datagram dispatch
- `nodesocket.py` - single-hop UDP wireless-link emulation
- `protocol_interface.py` - protocol integration contract
- `example_protocol.py` - validation-only protocol example
- `clive.py` - normal command-line runner
- `run_200_nodes.py` - 200-node stress runner
- `evaluation/validate_nodesocket_acceptance.py` - NodeSocket behavioral validation
- `evaluation/validate_clive.py` - end-to-end and optional 200-process validation

## Scope boundary

CLIVE emulates the network conditions required by the thesis. It does not implement FLAME discovery, membership, fragmentation/reassembly, ACK/retransmission logic, sliding-window reliability, Federated Learning, or MANET routing. Those belong to the protocol/application layer above `NodeSocket`.
