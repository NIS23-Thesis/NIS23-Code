"""UI regression tests: visualization-only controls; never modifies FLAME."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "clive" / "visualizer" / "static"
sys.path.insert(0, str(ROOT / "clive"))
from visualizer import RunData  # noqa: E402


def test_highlights_preserve_origin_and_hide_bookkeeping():
    def event(name, stream='protocol'):
        return {'event': name, 'stream': stream}
    assert RunData._panel_highlight(event('flame_joined'))
    assert RunData._panel_highlight(event('flame_trained'))
    assert RunData._panel_highlight(event('flame_transfer_failed'))
    assert not RunData._panel_highlight(event('started'))
    assert not RunData._panel_highlight(event('flame_started'))
    assert not RunData._panel_highlight(event('flame_sample_buffered'))
    assert not RunData._panel_highlight(event('flame_sensor_queued'))
    assert not RunData._panel_highlight(event('delivery_drop', 'packet'))


def test_server_highlight_category_is_consistent_across_log_readers():
    # Shared filtering predicate used by archive, preview and indexed window.
    assert RunData._matches_event_filters({'event':'flame_joined','stream':'protocol'},'highlights')
    assert RunData._matches_event_filters({'event':'flame_trained','stream':'protocol'},'highlights')
    assert not RunData._matches_event_filters({'event':'started','stream':'protocol'},'highlights')
    assert not RunData._matches_event_filters({'event':'transmit','stream':'packet',
                                               'packet_type':0},'highlights')
    source = (ROOT / 'clive' / 'visualizer.py').read_text()
    assert source.count('self._matches_event_filters(') >= 3


def test_four_distinct_views_are_available_not_hidden_by_legacy_css():
    html = (STATIC / 'index.html').read_text()
    css = (STATIC / 'styles.css').read_text()
    js = (STATIC / 'app.js').read_text()
    for mode in ('protocol', 'radio', 'traffic', 'none'):
        assert f'data-mode="{mode}"' in html
    assert '.mode-button[data-mode="traffic"], .mode-button[data-mode="none"] {display:inline-flex!important' in css
    assert 'syncLayerUI()' in js
    assert 'data-layer-for="radio"' in html
    assert 'data-layer-for="protocol"' in html
    assert 'modeExplanation' in html


def test_node_identity_modes_and_exact_time_seek_are_present():
    html = (STATIC / 'index.html').read_text()
    js = (STATIC / 'app.js').read_text()
    theme = (STATIC / 'theme.css').read_text()
    for mode in ('name', 'overlay', 'underlay', 'both', 'none'):
        assert f'value="{mode}"' in html
    assert 'spec?.overlay_ip' in js and 'spec?.underlay_ip' in js
    assert html.count('id="timeReadout"') == 1
    assert 'id="exactTime"' not in html
    assert 'id="seekButton"' not in html
    assert '<form id="seekForm" class="playback-clock playback-time-editor"' in html
    assert 'seekToEnteredTime()' in js
    assert 'html[data-theme="light"] .playback-time-editor #timeReadout' in theme


def test_event_coalescing_is_lossless_in_inspect_view():
    source = (STATIC / 'event_stream.js').read_text()
    assert '_repeat' in source
    assert 'underlying records' in source
    assert 'rec.time_s.toFixed(6)' in source
    assert 'flame_hello_sent' in source


def test_javascript_syntax_for_layer_and_event_views():
    for name in ('app.js', 'event_stream.js'):
        completed = subprocess.run(['node', '--check', str(STATIC/name)], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
