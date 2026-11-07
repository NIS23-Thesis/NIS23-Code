# CLIVE visualizer — second-pass corrections (2026-11-05)

**Base:** `NIS23-CLIVE-Visualizer-UI-Audit-Fixed-20261105.zip`.
**Scope:** visualizer frontend, read-only visualizer API and CLIVE validation only.
**FLAME:** all 25 files under `flame/` are preserved byte-for-byte from the base release.

## New corrections and rationale

| Area | Finding | Correction |
|---|---|---|
| Model Learning chart | Mouse movement still called `draw()` directly on every event despite the previous report's performance claim. `hoverFrame` existed but was unused. | Schedule a single redraw through `requestAnimationFrame`; cancel pending redraw when the pointer exits. |
| Model Learning chart | Invalid/missing accuracies and unsorted points could create broken line segments and wrong nearest-point tooltips. | Only draw finite, 0–100% accuracy measurements with nonnegative timestamps; sort measured points by timestamp. Original raw history remains unchanged. |
| Model Learning chart | Each hover reset the canvas dimensions and GPU rendering context. | Only resize the backing canvas when device-pixel size actually changes. |
| Model Learning responsive UI | All-Cluster-Head selector text was clipped on phone widths. | Shortened visible option to “All CHs”; the label remains “Cluster Head.” |
| Model Learning larger experiments | Six repeated plot colors for 7–10 CHs impaired identification. | Expanded to 12 palette entries; each CH retains its own legend marker. |
| Model Learning tooltips | Multiple CHs could produce an overlay taller than the chart container. | Bound tooltip height to the chart viewport. |
| Event History search | `MODEL_UPDATE` did not match datagrams stored only as packet_type=4; the archive searched numeric IDs, not human-readable packet names. | Include the canonical FLAME wire packet name in the searchable text for both the preview and archive APIs. A real run's `MODEL_UPDATE` packet search now returns matches. |
| Event History filters | “Major protocol actions” and “Drops and failures” classified events differently from the live Event Stream. | Reuse the same event-category and key-event classification rules on the server. |
| Event History speed | The archive kept scanning a per-node file after its timestamps passed the requested end time. | Stop reading that chronological stream once its timestamp is past the end of the interval. |
| Main topology | Paused playback still redrew both map and traffic canvases at every animation frame, including hidden traffic panels. | Limit paused map redraw to ~8fps and traffic to ~2fps; while playing, cap map at ~30fps and traffic at ~10fps; avoid drawing inactive traffic panel. |
| Main topology | Completed runs repeatedly reread the same full event window every 750 ms. | Reuse the last matching completed-run event query, refresh running-run queries at a bounded interval, and reject stale responses when controls change mid-request. |
| Main topology polling | Completed historical runs requested analytics and relationships every 1.5 seconds. | Use 6-second polling for inactive runs while preserving faster refresh during active emulations. |
| Event row safety | Record ID interpolated into an aria label without HTML escaping. | Escape that attribute before rendering. |

## Verified results

- Original FLAME Python unit tests: **183/183 passed**.
- Existing targeted visualizer, indexed history, node-connection and event-stream JavaScript tests: **passed**.
- Added `clive/validation/validate_visualizer_second_pass.py` to the CLIVE validation runner. It verifies numeric packet type/name search, aligned key/drop categories, and bounded time windows. **Passed**.
- Full CLIVE validation log reached **`ALL CLIVE VALIDATIONS: PASS`** (including the new test). Its memory-optimization group is resource-intensive and took longer than the initial execution window.
- Browser fixture using an **actual prior MNIST run's API data**, at **1440px, 820px and 390px**: 100-row event archive pagination, populated 5.4s event panel, and no page JavaScript errors or horizontal document overflow.
- The same browser fixture dispatched 60 mouse-move events in one browser turn and observed **one chart redraw**, after the fix.
- Paused map refreshed approximately six times in 700ms rather than rendering 42+ times; paused traffic around 1–2 times in the same window.
- A real recorded run's 0–5.4s `MODEL_UPDATE` packet search changed from **0 matches in ~1.4s** to **matching records in ~0.004s**, in a single local comparison. Timing is environment- and cache-dependent.

## Limitations

- This is a further targeted pass, **not proof that every possible visual defect has been removed**. Screenshots reflect a browser harness with real saved experiment data; exact OS/browser deployment should still be smoke-tested.
- Model Learning's full historical point array is still sent to the page; very long experiments with extremely many revisions could benefit from server-side pagination/decimation in a future optimization.
- A recording cannot display packet events that were not logged when it originally ran. Event History provides all *recorded* events in 100-row pages.
- FLAME ACK/reliability logic, protocol semantics and the 255-update counter were deliberately **not modified**.
- These changes do not assert that mobility/loss demos complete every model transfer or that MNIST has converged.

## Run instructions

From the extracted project root:

```bash
./start_gui.sh
```

From the `clive/` directory, the read-only visualizer can be opened separately:

```bash
python visualizer.py --results results --port 8765
```

To rerun the new regression from `clive/`:

```bash
python validation/validate_visualizer_second_pass.py
```
