"""Navigation acceptance checks: Studio configures; Visualizer visualizes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'clive'


def test_no_duplicate_live_monitor_or_capacity_visualizer_dialog():
    studio = (ROOT / 'control_ui/index.html').read_text(encoding='utf-8')
    viewer = (ROOT / 'visualizer/static/index.html').read_text(encoding='utf-8')
    assert 'data-view="monitor"' not in studio
    assert 'id="monitorView"' not in studio
    assert 'data-view="design"' in studio
    assert 'data-view="history"' in studio
    assert 'id="runStatusStrip"' in studio
    assert 'id="stopButton"' in studio
    assert 'id="runTopologyLink"' in studio
    assert 'id="topologyNavLink"' in studio
    assert 'id="capacityEditorLink"' not in viewer
    assert 'id="capacityDialog"' not in viewer
    assert 'id="capacityEditorFrame"' not in viewer


def test_history_view_links_to_selected_run_in_visualizer():
    code = (ROOT / 'control_ui/app.js').read_text(encoding='utf-8')
    viewer = (ROOT / 'visualizer/static/app.js').read_text(encoding='utf-8')
    assert 'function runVisualizerUrl(runName)' in code
    assert "link.searchParams.set('run',runName)" in code
    assert "open.href=url" in code
    assert "open.textContent='View topology ↗'" in code
    assert "details.textContent='Metrics'" in code
    assert 'new URLSearchParams(window.location.search).get("run")' in viewer
    assert "selectView('monitor')" not in code


def test_run_controls_remain_available_after_monitor_removal():
    code = (ROOT / 'control_ui/app.js').read_text(encoding='utf-8')
    assert "$('stopButton').disabled=!s.running" in code
    assert "$('runStatusStrip').classList.toggle('hidden',!visible)" in code
    assert "$('console').textContent=s.log" in code


def test_advanced_settings_remain_editable_without_overcrowding_setup():
    code=(ROOT / 'control_ui/app.js').read_text(encoding='utf-8')
    assert 'const primaryFields =' in code
    assert "runtime:['duration_s','seed','radio_execution']" in code
    assert "const details=document.createElement('details');details.className='advanced-settings'" in code
    assert "advanced.forEach(key=>fieldFor(group,key,get(group,key),more))" in code
