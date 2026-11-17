# Cluster Head received-digit distribution (CLIVE-side evidence)

**Scope:** `clive/flame_protocol.py` counts the last label byte of each unique accepted, complete 785-byte `SENSOR_DATA` MNIST-compatible observation, after membership and payload validation and after FLAME handling succeeds. Duplicates do not increment the count. Malformed observations are not admitted. A non-MNIST-compatible payload is recorded as `unclassified` when CLIVE runs another workload.

**Semantics:** The counts are **per CH and cumulative for the run**. They are not sensor-generated sample totals, data still retained in the bounded CH FIFO dataset, unique training examples, model predictions, test-set classification scores, or evidence that every generated sample arrived. As a Sensor Node changes association, separate CHs independently accumulate the samples each actually receives. The distribution is serialized in `flame/CH-X.json` as `received_digit_distribution`, separately from FLAME internals. The new Model Learning panel reads that field via `/api/learning`.

**Display behavior:** Selecting All CHs shows a separate 0–9 histogram row for every Cluster Head; selecting one CH filters the view. Colors correspond to each CH. Counts become available when the node exits and writes its final result JSON. Historical runs without the field show `Not recorded for this run`, not invented zero values. Synthetic data sources are labeled. All explanatory content is accessible from the (?) button.

**Regression proof:** `tests/integration/test_clive_digit_distribution.py` covers exact label counts, duplicate suppression, dataset eviction independence, invalid input exclusion, API propagation, and UI structure. Existing integrated tests and the complete Python suite also run. A fresh 2-CH/2-SN real-MNIST run produced CH-1 digit 2 count 7 and CH-2 digit 1 count 7, matching each CH’s unique received-observation counter.

**Source boundary:** No changes under `flame/`. No changes to retransmissions, membership logic, dataset capacity, learning rules, or radio modeling.
