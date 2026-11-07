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