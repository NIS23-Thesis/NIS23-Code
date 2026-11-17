# CLIVE — Light Mode presentation polish (17 November 2026)

This change affects **only the CLIVE front end**. It does not alter FLAME, packet semantics, network emulation, saved experiments, model training, or simulation settings.

## Visual changes

- The Scenario Library's active preset uses a light teal selected state with readable dark text, a strong left selection marker, and a separate hover treatment.
- Primary actions use solid teal (white text), active destructive actions use solid red (white text), and disabled controls use neutral gray rather than low-contrast washed-out danger colors.
- Setup, Topology, Learning and Events share a cleaner hierarchy: white content surfaces, restrained borders and shadows, consistent navigation hover, focus rings and tab selection.
- Topology playback controls, layer menus, node tooltips, chart tooltips, badges, event rows, Cluster Head selectors and model-revision controls all receive explicit light-mode interaction treatments.
- On narrow screens, the event archive's cards use light surfaces instead of a dark background carried over from the original theme.

## Use

Open CLIVE through `python clive/control_center.py --port 9766` (from the repository root), then use the **Light mode / Dark mode** button in the top-right. The choice persists across page navigation and reloads.

## Verification performed

- `pytest -q --disable-warnings`: **310 passed, 4 warnings** on the packaging environment.
- Independent Python color-pair checks cover primary, danger, selected, neutral text, and disabled control contrast (>= 4.5:1 for the specifically checked pairs).
- CSS parsed without syntax errors and the local server returned successful HTTP responses for Setup, Topology, Learning, Events and both theme asset endpoints.
- Compared to the immediately preceding light-mode ZIP, only CLIVE theme CSS, new UI regression tests, and this document changed; FLAME source is unchanged.

**Limitation:** A fresh browser-rendered visual regression pass was not possible because the current Chromium environment blocks all page navigation, including local URLs. The visual changes are therefore source-reviewed, syntax-checked, unit-tested, and HTTP-served, but should be visually inspected in the actual CLIVE browser before the thesis defense.
