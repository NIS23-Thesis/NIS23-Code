"""Guard the Learning page against CSS and DOM regressions without running FLAME."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / 'clive' / 'visualizer' / 'static'


def test_learning_js_does_not_reference_absent_elements():
    html = (ROOT / 'learning.html').read_text()
    script = (ROOT / 'learning.js').read_text()
    available = set(re.findall(r'\bid="([^"]+)"', html))
    references = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", script))
    assert references <= available, sorted(references - available)
    assert 'runStatus' not in references  # removed from shared header


def test_learning_uses_compact_responsive_grid():
    html = (ROOT / 'learning.html').read_text()
    css = (ROOT / 'learning-dashboard.css').read_text()
    for area in ('chart', 'quality', 'distribution'):
        assert f'learning-{area}-panel' in html
        assert f'.learning-{area}-panel' in css
    for area in ('history', 'digits', 'prediction'):
        assert f'learning-{area}-panel' in html
        assert f'id="detail-tab-{area}"' in html
        assert f'id="detail-{area}"' in html
    assert 'role="tablist"' in html
    assert '.learning-detail-pane[hidden]' in css
    assert 'grid-template-columns:repeat(12,minmax(0,1fr))' in css
    assert '@media(max-width:840px)' in css
    assert 'learning-dashboard.css' in html
    assert 'id="learningNotice"' in html


def test_learning_and_events_preserve_run_across_navigation():
    for name in ('learning.js', 'events.js'):
        script = (ROOT / name).read_text()
        assert '.clive-site-nav a[data-run-link]' in script
        assert '.bar nav a[data-run-link]' not in script
        assert 'window.CLIVERunSelection?.write(selectedRun)' in script


def test_learning_failure_and_missing_data_have_visible_states():
    script = (ROOT / 'learning.js').read_text()
    assert 'clearLearning()' in script
    assert 'showNotice(' in script
    assert "Array.isArray(data.nodes)" in script
    assert 'Invalid model prediction data.' in script


def test_received_digit_inspection_is_ch_scoped_and_sortable():
    script=(ROOT / 'learning.js').read_text()
    css=(ROOT / 'learning-dashboard.css').read_text()
    html=(ROOT / 'learning.html').read_text()
    for control in ('nodeSelect','chartCHSelect','distributionCHSelect','qualityCHSelect',
                    'detailsCHSelect','distributionSort','qualitySort','historySort','perDigitSort'):
        assert f'id="{control}"' in html
    assert 'function distributionRecord(node)' in script
    assert 'function renderReceivedDigits(node)' in script
    assert 'function panelNodesFor(id)' in script
    assert 'Follow master' in html
    for sort in ('most','least','digit'):
        assert f'value="{sort}"' in html
    assert 'single-digit-bars' in css
    assert 'single-digit-row' in css
    assert 'Not recorded for this run' in script
    assert 'rankTile(' not in script
    assert 'distributionPageSize' not in html
    assert 'fullDigitBreakdown(' not in script


def test_master_filter_and_each_panel_are_independent():
    script=(ROOT / 'learning.js').read_text()
    assert "const target=selection==='follow'?master:selection" in script
    assert "renderReceivedDigits(specificNodeFor('distributionCHSelect'))" in script
    assert "draw(panelNodesFor('chartCHSelect'))" in script
    assert "renderQuality(panelNodesFor('qualityCHSelect'))" in script
    assert "renderHistory(panelNodesFor('detailsCHSelect'))" in script
    assert "$('chartCHSelect').value" in script
    assert "$('nodeSelect').value=select.value" not in script


def test_model_quality_curve_is_always_primary_even_without_measurements():
    """The received-digit summary may not displace the learning curve."""
    html=(ROOT/'learning.html').read_text()
    css=(ROOT/'learning-dashboard.css').read_text()
    script=(ROOT/'learning.js').read_text()
    assert 'id="chart"' in html and 'id="chartEmpty"' in html
    assert '.learning-chart-panel{grid-column:1/-1;grid-row:1;' in css
    assert '.learning-distribution-panel{grid-column:1/span 7;grid-row:2;' in css
    assert '.learning-grid.no-quality-metrics .learning-chart-panel{display:none}' not in css
    assert "classList.toggle('no-quality-metrics'" in script
    assert "$('chartEmpty').hidden=Boolean(ready.length);" in script
    assert 'No accuracy/F1 measurements were recorded for this run' in html
