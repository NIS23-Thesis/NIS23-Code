# CLIVE v2 + FLAME GUI and Membership Fix — Test Report

## Scope

A local browser-based controller was added without changing CLIVE's emulated radio/NodeSocket or the original FLAME retry/window algorithm. Only the membership callback ordering was changed in `flame/communication/communication_module.py`.

## Results, October 31, 2026

| Test | Outcome |
|---|---|
| Existing FLAME tests (`python -m pytest -q tests`) | **183/183 passed** |
| Existing CLIVE non-stress suite (`python clive/validation/run_all.py`) | **Passed** |
| New GUI and membership suite (`python -m unittest clive.validation.validate_control_center -v`) | **5/5 passed** |
| All bundled scenario JSONs tested after merging the GUI's default values | **28/28 valid** |
| HTTP GET for GUI HTML and JavaScript | **Passed** |
| HTTP presets listing, scenario read, scenario validation, save/load | **Passed** |
| HTTP experiment start, double-start rejection, result read, stop | **Passed** |
| Membership promotion after multi-fragment reassembly, before application callback | **Passed** |
| Partial sensor fragment does not promote membership | **Passed** |
| Unknown sensor does not become associated | **Passed** |
| Real CLIVE one-sample, one-CH/one-SN test | **1 observation accepted, 0 rejected** |
| Real CLIVE 2-CH/2-SN MNIST/PyTorch scenario (`flame_mnist_small.json`) | **Passed, complete run** |

### Real MNIST run

A fresh real-MNIST scenario used two Cluster Heads and two Sensor Nodes. Each CH recorded 16 accepted observations, zero unassociated-sensor rejections, 15 training events, 15 received peer models, 15 model aggregations, and zero transfer failures. The run's `metrics.json` recorded success and zero emulated delivery drops. This specific successful run does not establish loss robustness.

### Unchanged components

Comparison with the previously integrated ZIP confirmed that all original files were byte-identical **except**:

- `README.md` — new GUI entry-point documentation;
- `clive/validation/run_all.py` — adds the controller regression suite;
- `flame/communication/communication_module.py` — moves sensor membership promotion before application delivery.

The original CLIVE `nodesocket.py`, emulator, orchestrator and FLAME reliability code were not modified.

### Known limits

- Headless browser navigation was restricted by the test environment; automated visual layout/screenshot verification was **not** completed. HTTP-serving tests and JavaScript syntax validation passed. Validate the browser layout interactively on the target workstation.
- CLIVE is still localhost-only; multi-host operation was not added.
- The earlier AIMD/ACK window-desynchronization defects remain **unfixed**, as requested by the limited scope of this release. Avoid treating failure rates under packet loss as final protocol conclusions until that reliability logic is repaired and independently retested.
- The GUI progress bar is a wall-clock estimate, not a measure of network throughput or FL accuracy.
