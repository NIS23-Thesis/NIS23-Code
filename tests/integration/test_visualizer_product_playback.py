"""Regression guard for the panel-facing CLIVE topology playback controls.

Only presentation elements are tested here; FLAME training/protocol functionality is
covered by the separate trainer and integration suites.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "clive" / "visualizer" / "static"


def test_playback_uses_one_product_toolbar_and_advanced_options():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "playButton", "timeReadout", "timeSlider", "speedSelect",
        "playbackOptions", "seekForm", "guidedPlayback", "nextAction",
    ):
        assert html.count(f'id="{element_id}"') == 1, element_id
    assert '<details class="playback-options"' in html
    assert '<div class="playback-timeline">' in html
    assert 'id="exactTime"' not in html
    assert 'id="seekButton"' not in html
    assert 'type="number"' in html
    assert 'press enter to seek' in html.lower() or 'press enter to jump' in html.lower()
    assert 'playback-duration-labels' not in html
    assert 'playbackEndTime' not in html
    assert 'playbackEndTime' not in (STATIC / "app.js").read_text(encoding="utf-8")
    # The scrubber remains accessible and the current time is still displayed.
    assert 'aria-label="Experiment playback time"' in html


def test_redundant_links_removed_but_top_navigation_preserved():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    for old_id in ("learningInlineLink", "eventArchiveLink", "eventProblemsLink"):
        assert old_id not in html
        assert old_id not in javascript
    for page in ("Setup", "Topology", "Learning", "Events", "Run History"):
        assert f">{page}</a>" in html


def test_extra_metrics_toggle_is_contextual_and_functional():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    assert html.count('id="toggleDiagnostics"') == 1
    assert html.index('id="toggleDiagnostics"') > html.index('rail-card metrics-card')
    assert 'body.show-diagnostics .metrics-card .advanced-metric { display:block!important; }' in css
    assert '"Show more metrics"' in javascript
    assert '"Show fewer metrics"' in javascript
