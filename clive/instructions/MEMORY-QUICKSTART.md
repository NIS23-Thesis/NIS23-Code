# Memory-optimized CLIVE + FLAME — quick start

1. Extract the archive, then run `./start_gui.sh` from the extracted folder.
2. In the GUI open **Experiment setup → General**. Set Cluster Heads, Sensor Nodes, and choose **Radio Execution: Thread** for compact-memory mode. **Process** remains the original, most isolated option.
3. For a verified 220-node sensor/ACK test, select `demos/capacity_20ch_200sn_memory.json` from the scenario library. This preset uses real MNIST observations but **mean_pixel** instead of PyTorch; it does not validate full-scale federated learning.
4. Save the scenario from the GUI, then press **Run experiment**. Stop is available from **Live monitor**. Do not change JSON manually.
5. For full FL, select **FLAME → Trainer: mnist**, then run a small scenario first. The 20CH/200SN PyTorch workload has **not** been established safe within 4 GiB RAM.

The first-sample association fix remains. Known FLAME ACK/sliding-window defects are unchanged. Read `MEMORY-OPTIMIZATION-REPORT.md` before using performance figures for the thesis.
