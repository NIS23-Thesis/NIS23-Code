## CLIVE panel presentation (five real-MNIST demos)

Run `./start_gui.sh` from the project root (or `python clive/control_center.py --open`).
Choose one of **five panel demos**, all with `sample_source: mnist` and
`trainer: mnist` (real MNIST/PyTorch model training). Start the experiment,
then use **Open topology visualizer** for nodes/links or **Open model training
& accuracy** for per-CH model quality. The visualizer also links to **Event
history**, which paginates the on-disk events rather than clipping to the
playback-window preview.

The Experiment Studio timer freezes after the launched process finishes.
The 108 formal scenarios under `clive/configs/evaluations/thesis_matrix/`
are separate and **not** five-minute panel demos; run them with the CLI
when completing the thesis evaluation campaign.

**Important:** Per-update accuracy is computed from the same fixed held-out
MNIST test subset (512 examples in panel demos). Final accuracy uses all 10,000
MNIST test examples. Frequent test-set measurements are diagnostic and add
runtime overhead; do not use the short demos as convergence or throughput
benchmarks. All FLAME protocol implementation files are unchanged.

---

# NIS23-Code

Run ./start_gui.sh

### Unified CLIVE web application

Run `./start_gui.sh` and use **http://127.0.0.1:9767/**. All normal
views share this origin: Setup/History (`/`), Topology (`/topology/`),
Model Learning (`/topology/learning.html`) and Events
(`/topology/events.html`). Run selection carries through the results views;
no second web port is required. The standalone `clive/visualizer.py` launcher
remains available for developers.

Frontend organization: `clive/control_ui/` (experiment editing),
`clive/visualizer/static/` (recorded results), and `clive/ui_shared/`
(the canonical common header stylesheet). To update the shared header CSS,
edit `clive/ui_shared/shell.css` and run
`python clive/scripts/sync_ui_shell.py`. These are presentation assets only;
FLAME source and CLIVE network simulation logic are unchanged.
