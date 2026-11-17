# CLIVE capacity controls (per node)

Open **Experiment setup → Buffers & storage** at `http://127.0.0.1:9767/?section=capacity` (while `start_gui.sh` is running). Enter a positive integer beside any SN/CH limit, or use **Inherit default** for the preexisting value. Save a preset to retain it. These fields are stored in the scenario under `flame` for compatibility with existing saved scenarios and plans; they are CLIVE-side controls, not FLAME protocol changes.

| Role | Setting | Units | Inherited value |
|---|---|---|---|
| Both | send buffer | FLAME pending messages | 128 |
| Both | datagram buffer | datagrams (oldest discarded if full) | 128 |
| SN | sensing sample buffer | observations | 100 |
| SN | offline sample buffer | observations | 64 |
| CH | local dataset | observations | 512 |
| Both | radio TX queue | packets / bytes | 4096 / 8388608 |

`receive_buffer_capacity` is a legacy name and actually configures the **datagram buffer**, not the completed-message receive history. Role-specific overrides can be left null to retain it. The CH dataset must still be large enough to satisfy the selected training threshold. CLIVE does not replace or resize FLAME's completed-message receive histories. Those histories remain internally managed by FLAME.

**Important limitations:** Buffer capacities are entry/byte limits, **not hard per-node RAM allocations**. Python/PyTorch memory, the OS socket receive buffer, FLAME's reliability/reassembly maps, and the aggregation buffer are not bounded by these settings. The aggregation *threshold* is not a memory limit. Changing buffers may change queue-full and loss outcomes; hold them fixed and record the scenario configuration when comparing runs. FLAME source remains untouched.

Example scenario overrides:

```json
"flame": {
  "sn_send_buffer_capacity": 64,
  "sn_sample_buffer_capacity": 40,
  "sn_radio_queue_bytes": 1048576,
  "ch_send_buffer_capacity": 256,
  "ch_dataset_capacity": 1024
}
```

**Safety:** No runtime replacement of FLAME ReceiveBuffer or reliability objects is performed. Only constructor-supported FLAME capacities and CLIVE-owned queues are set. Existing saved presets containing unsupported `sn_receive_history_capacity` or `ch_receive_history_capacity` must remove those fields before running.
