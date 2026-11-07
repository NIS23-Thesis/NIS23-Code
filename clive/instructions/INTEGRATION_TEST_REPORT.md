# Integration validation record

Tests were executed against the integrated code in this repository.

| Validation | Result | Scope |
| --- | --- | --- |
| Upstream FLAME tests: `python -m pytest -q tests` | **PASS: 183 tests** | Packet serialization/parsing, transport reliability, neighbors, federated-learning components, sensing, etc. |
| CLIVE v2 non-stress suite: `cd clive && python validation/run_all.py` | **PASS** | CLIVE mobility, radio/link conditions, sockets, visualizer, process architecture, and added FLAME acceptance test |
| Six-node end-to-end smoke (`flame-synthetic-transport-and-aggregation-smoke.json`) | **PASS** | 2 CH + 4 SN; real CLIVE NodeSocket and FLAME wire protocol; all eight packet types; sample receipt; CH training and aggregation; scalar test model and synthetic payload |
| PyTorch MNIST run (`flame-mnist-pytorch-training-2ch-2sn.json`) | **PASS** | 2 CH + 2 SN; real 785-byte MNIST payloads; WAFL-style MLP local training on CH; 399-fragment FLAME `MODEL_UPDATE` messages and ACK/reassembly; CH-to-CH buffered aggregation |

## Observations from the final real MNIST run

- Node/radio processes: all completed successfully.
- CH-1: 30 observations received; 29 local train/update events.
- CH-2: 29 complete models received and 29 aggregation events.
- SN-1 and SN-2: both joined a CH, sent data, and issued an explicit LEAVE before shutdown.
- FLAME `MODEL_UPDATE` packet emissions: 11,571 data fragments in total.
- No FLAME datagram errors, transfer failures, or FL training errors were recorded in the run counters.

**Scope:** These tests demonstrate successful integration, not the accuracy of the learned classifier, long-term FL convergence, scalability, or guaranteed communication performance under all mobility/loss configurations. The `mean_pixel` smoke training routine exists only as a fast integration test, while the regular MNIST mode invokes the supplied final FLAME implementation's PyTorch trainer.
