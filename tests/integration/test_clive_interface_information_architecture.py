"""CLIVE UI regression tests for context clarity and shared navigation."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
STUDIO = ROOT / "clive" / "control_ui"
VIZ = ROOT / "clive" / "visualizer" / "static"


def test_all_main_screens_share_navigation():
    for path in (STUDIO / "index.html", VIZ / "index.html", VIZ / "learning.html", VIZ / "events.html"):
        html = path.read_text()
        assert 'class="clive-site-header"' in html or 'class="bar clive-site-header"' in html
        for label in ("Setup", "Topology", "Learning", "Events", "History"):
            assert label in html, (path.name, label)
        assert html.count('class="clive-site-header"') + html.count('class="bar clive-site-header"') == 1


def test_setup_uses_two_distinct_experiment_contexts():
    html = (STUDIO / "index.html").read_text()
    assert html.index('id="runStatusStrip"') < html.index('id="designView"')
    assert 'id="runStatusName"' in html and 'id="editingName"' in html
    assert 'SELECTED SCENARIO · EDITING ONLY' in html
    assert 'id="workloadNotice"' not in html
    assert 'id="topologyNavLink"' in html


def test_progress_comes_from_started_run_not_selected_scenario():
    source = (STUDIO / "app.js").read_text()
    assert 'Number(s.duration_s)' in source
    assert 'Number(s.startup_delay_s)' in source
    assert 'Number(config.runtime?.duration_s)' not in source
    assert 'Number.isSafeInteger(next)' in source


def test_only_run_local_navigation_carries_selected_run():
    for filename in ("learning.js", "events.js"):
        script = (VIZ / filename).read_text()
        assert '.clive-site-nav a[data-run-link]' in script
    assert (VIZ / "ui-shell.css").read_bytes() == (STUDIO / "ui-shell.css").read_bytes()
