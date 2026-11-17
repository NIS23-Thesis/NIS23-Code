# CLIVE Experiment Studio — GUI Quickstart

## Starting the dashboard

From the extracted project folder on Linux/Fedora:

```bash
./start_gui.sh
```

Or (cross-platform):

```bash
python clive/control_center.py --open
```

The unified CLIVE application opens at **http://127.0.0.1:9767/**. Experiment Setup and Run History use `/`, the topology is at `/topology/`, Model Learning is at `/topology/learning.html`, and Events are at `/topology/events.html` on **the same port**. You do **not** need to open VS Code, edit JSON, or switch between two servers.

The GUI and emulator run on your own computer. Keep the controller terminal open while using the dashboard. Press Ctrl+C to close the server (running experiments and their child processes are stopped on shutdown).

For real MNIST/PyTorch training, install `numpy` and `torch` in the Python environment used to start the dashboard. For fast transport validation, choose the **FLAME smoke** preset, which uses synthetic sensor observations and the `mean_pixel` trainer. The supplied `mnist/mnist.npz` dataset is included.

## Controls

1. **Choose a scenario** from the left panel (demos, evaluation presets, or your own saved presets). The controls load automatically.
2. **Edit** any setup field under General, Mobility & topology, Radio & network, FLAME, or Diagnostics. These controls cover the complete scenario schema. For **manual topology**, select Manual and use **Auto-populate all node coordinates** or edit the individual X/Y entries.
3. **Save preset** to create your own reusable configuration in `clive/configs/gui/`. Bundled scenarios are never overwritten; editing them and saving creates a custom copy.
4. Click **Run experiment**. The dashboard generates a frozen JSON snapshot under `clive/results/_control/`, then executes `clive.py` in a separate process using the selected settings. You can run unsaved configurations too.
5. The **run status strip** in Experiment setup displays progress, execution output, **Stop run**, and a **View topology** link. Topology playback and network activity are only in the Visualizer, not a duplicate monitor page.
6. The **Run history** lists previous runs. **View topology** opens the selected run in the actual Visualizer; **Metrics** expands a summary in Studio. Detailed outputs remain under `clive/results/run-*/`.

**Stop** sends termination to the entire launched process group so CLIVE's node and radio children do not continue running unnoticed. Only one experiment can be active in a controller instance at a time.

The controller only listens on `127.0.0.1` by default; it is not intended for public/shared hosting. Ports can be customized with `--port` and `--visualizer-port`.

## First SENSOR_DATA observation fix

The original integration ran the application callback *before* `NeighborModule.process_sensor_data()` promoted a newly joined Sensor Node from `JOIN_PENDING` to `ASSOCIATED`. That caused the first observation to be discarded despite a successful network transfer.

The fix moves promotion **inside the fully reassembled SENSOR_DATA path before `_deliver()`** in `flame/communication/communication_module.py`. A partial fragment cannot promote membership. Unknown sensor nodes remain unassociated. The remaining FLAME reliability implementation (including previously identified AIMD/ACK issues) is unchanged.

## Testing

```bash
python -m pytest -q tests
python clive/validation/run_all.py
python -m unittest clive.validation.validate_control_center -v
```

The controller regression exercises configuration validation, scenario save/load, the first sensor observation via real CLIVE processes, full completion, concurrent-start rejection, and stopping a long-running process group.

## Limitations

- CLIVE v2 remains **localhost-only**. The GUI does not add LAN/multi-host emulation.
- The experiment progress bar estimates elapsed vs configured duration; it is not a percentage of FL model convergence.
- Model-transfer reliability issues identified in the independent audit remain and were intentionally **not changed** in this GUI + membership-fix release.
- Browser control requires the local server to be running. Opening `index.html` as a file does not provide execution controls.
