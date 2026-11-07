# CLIVE Visualizer — UI regression audit

**Scope:** CLIVE visualizer presentation, event archive, model learning UI. No edits to `flame/` or FLAME protocol logic.

## Confirmed user-facing defects and fixes

| Issue | Cause | Change |
| --- | --- | --- |
| Earlier selected run replaced when a new run appeared | `/api/runs` polling automatically switched to newest | Preserve selection unless the selected run is missing; first load still selects newest |
| Events tab missing although recorded logs existed | Visibility was gated on logging switches | Keep Traffic, Events, and Nodes tabs accessible and show an honest empty state |
| Event archive contained unreadable internal names | Raw `event.replaceAll('_', ' ')` displayed without context | Reuse the shared `CliveEventStream` event dictionary for human-readable actions and explanations |
| Event archive could show stale results after fast run/filter changes | Previous requests were not versioned | Ignore outdated asynchronous responses and retain accurate pagination status |
| Archive links switched to another experiment | Navigation links did not carry selected `run` | Keep the selected run in Network / Learning / History navigation |
| Model chart hover triggered expensive full redraw on every mouse event | Direct `draw()` per pointermove | Throttle hover work to a browser animation frame; retain exact measured point values |
| Small-screen Play button text escaped its button | 45-pixel button forced wrapping | Sized Play/Pause control for visible text on mobile and desktop |
| Six-column event archive unreadable on phones | Dense table was squeezed into narrow viewport | Responsive event cards with labels, readable packet metadata and no body-wide horizontal overflow |
| Model table could hide node identity during horizontal scrolling | Narrow layout required horizontal table navigation | Sticky node/first column on narrow screens |
| Learning history toggle implied unlimited history | Expanded view rendered at most 2,000 rows | Explicitly label the 2,000-row cap (no claim that it is all history) |
| UI configuration text interpolated without escaping | Dynamic values inserted into `innerHTML` | Escape keys and values before rendering |
| Current viewing status was mislabeled ACTIVE EXPERIMENT | Completed historical runs may be viewed | Relabeled SELECTED EXPERIMENT |

## Already present and verified, not new changes

- Playback time freezes at the configured experiment duration when playback ends.
- `/api/events` reads selected time intervals from indexed recorded JSONL instead of an evicting live cache; at 5.4 seconds of a real 20-second run, it returned 109 key events.
- Full recorded event history is paginated from disk rather than limited to a live preview cache. Events never logged cannot be reconstructed.
- The Model Learning page shows real MNIST accuracy measurements and distinguishes sampled accuracy from full-test accuracy.

## Testing and evidence

- FLAME: 183/183 existing Python tests pass (run only; source unchanged).
- Full CLIVE validation suite: `ALL CLIVE VALIDATIONS: PASS`.
- Focused CLIVE visualizer, event history and connection tests: PASS.
- Browser fixture with real recorded run data: desktop 1440px, tablet 820px, and phone 390px; map, learning and events pages rendered without page-level JavaScript errors or document-level horizontal overflow.
- Browser interaction regressions: selected-run stability, Events tab availability when packet logging is disabled, frozen completed playback, measured accuracy tooltip, 100-record event archive paging, and phone-sized event cards all passed.
- Chromium direct navigation to loopback HTTP is restricted in this environment. Browser tests instead loaded the actual HTML/CSS/JS with API responses from the existing CLIVE run artifacts. HTTP endpoint responses were independently checked with local requests. This does not substitute for checking every platform/browser combination.

## Remaining limits (not changed)

- Not every possible UI state or operating system was exercised; no audit can guarantee zero visual bugs.
- Previously recorded events are available only if their logger was enabled when the run occurred. Connection-only logs retain fewer details than full packet logging.
- The event archive loads 100 records per page rather than putting hundreds of thousands of records into one DOM table.
- The learning history table displays up to 2,000 most recent updates when expanded; full historical accuracy series remain in the on-disk CLIVE learning history output.
- FLAME reliability / ACK and protocol issues from previous audits remain unchanged.

## Running

From the project root:

```bash
./start_gui.sh
```

From `clive/`, a read-only visualizer may also be launched using:

```bash
python visualizer.py --results results --port 8765
```

Navigation: **Network map → Model learning → Event history**. Select the run in the dropdown; the selection is preserved while navigating and during polling. The Events tab provides a bounded playback preview, whereas Event history offers all **recorded** events page-by-page.
