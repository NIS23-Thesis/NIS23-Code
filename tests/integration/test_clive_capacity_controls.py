"""Role capacities in CLIVE; no FLAME protocol code changes."""
from types import SimpleNamespace
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'clive'))
from capacity_settings import OVERRIDES, effective, check_overrides
from control_center import validated_scenario, DEFAULT_SCENARIO
from flame_protocol import _configuration, DEFAULTS


def settings(**overrides):
    return _configuration(SimpleNamespace(flame_settings=overrides))


def test_defaults_preserve_existing_capacities():
    s=settings()
    assert effective(s, 0, 'send', s['send_buffer_capacity']) == 128
    assert effective(s, 1, 'datagram', s['receive_buffer_capacity']) == 128
    assert effective(s, 0, 'sample', 100) == 100
    assert effective(s, 1, 'dataset', s['dataset_capacity']) == 512


def test_role_override_isolated_and_legacy_fallback():
    s=settings(send_buffer_capacity=23, sn_send_buffer_capacity=8,
               ch_datagram_buffer_capacity=17, sn_sample_buffer_capacity=3,
               sn_offline_buffer_capacity=5, ch_dataset_capacity=25)
    assert effective(s, 0, 'send', s['send_buffer_capacity']) == 8
    assert effective(s, 1, 'send', s['send_buffer_capacity']) == 23
    assert effective(s, 0, 'datagram', s['receive_buffer_capacity']) == 128
    assert effective(s, 1, 'datagram', s['receive_buffer_capacity']) == 17
    assert effective(s, 0, 'sample', 100) == 3
    assert effective(s, 0, 'offline', s['max_offline_samples']) == 5
    assert effective(s, 1, 'dataset', s['dataset_capacity']) == 25


def test_radio_queue_overrides_are_per_role_and_support_bytes():
    s=settings(sn_radio_queue_packets=1, ch_radio_queue_packets=7,
               sn_radio_queue_bytes=4096, ch_radio_queue_bytes=8192)
    assert effective(s, 0, 'radio_packets', 4096) == 1
    assert effective(s, 1, 'radio_packets', 4096) == 7
    assert effective(s, 0, 'radio_bytes', 8 * 1024 * 1024) == 4096
    assert effective(s, 1, 'radio_bytes', 8 * 1024 * 1024) == 8192


@pytest.mark.parametrize('bad', [0, -1, 1.5, False, True, '10', [], 1_000_001])
def test_invalid_limit_rejected(bad):
    with pytest.raises(ValueError):
        settings(sn_sample_buffer_capacity=bad)


def test_invalid_queue_byte_limit_rejected():
    with pytest.raises(ValueError):
        settings(ch_radio_queue_bytes=(1 << 30) + 1)


def test_ch_dataset_capacity_must_hold_training_batch():
    with pytest.raises(ValueError, match='ch_dataset_capacity'):
        settings(expected_observations=10, training_threshold=.8,
                 dataset_capacity=512, ch_dataset_capacity=7)
    assert settings(expected_observations=10, training_threshold=.8,
                    ch_dataset_capacity=8)['ch_dataset_capacity'] == 8


def test_internal_flame_histories_cannot_be_overridden():
    with pytest.raises(ValueError, match="Unknown flame configuration keys"):
        settings(sn_receive_history_capacity=10)


def test_clive_does_not_rebind_flame_receive_buffers():
    adapter_source = (Path(__file__).resolve().parents[2] / 'clive' / 'flame_protocol.py').read_text()
    assert 'BoundedReceiveHistory' not in adapter_source
    assert 'setattr(self.comms' not in adapter_source


def test_scenario_json_accepts_role_capacities_and_defaults():
    import copy
    scenario=copy.deepcopy(DEFAULT_SCENARIO)
    scenario['flame']['sn_sample_buffer_capacity']=19
    scenario['flame']['ch_dataset_capacity']=256
    scenario['flame']['sn_radio_queue_bytes']=1024 * 1024
    validated_scenario(scenario)
    assert scenario['flame']['sn_sample_buffer_capacity']==19
    # Optional role overrides preserve legacy .flame config compatibility.
    scenario['flame'].pop('ch_dataset_capacity')
    validated_scenario(scenario)


def test_visualizer_capacity_editor_link_targets_configured_control_port(tmp_path):
    """The visualizer remains read-only; it supplies a loopback editor link."""
    import json
    from threading import Thread
    from urllib.request import urlopen
    from visualizer import VisualizerServer, RunCatalog

    expected = 'http://127.0.0.1:19443/?section=capacity'
    server = VisualizerServer(('127.0.0.1', 0), RunCatalog(tmp_path),
                              quiet=True, capacity_editor_url=expected)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/experiment-studio', timeout=3) as response:
            result = json.load(response)
        assert result['capacity_editor_url'] == expected
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_capacity_tab_has_safe_gui_and_no_internal_flame_history_controls():
    clive = Path(__file__).resolve().parents[2] / 'clive'
    control = (clive / 'control_ui' / 'app.js').read_text()
    visualizer_html = (clive / 'visualizer' / 'static' / 'index.html').read_text()
    assert 'function renderCapacities(target)' in control
    assert 'new URLSearchParams(window.location.search).get("section")==="capacity"' in control
    assert 'sn_receive_history_capacity' not in control
    assert 'ch_receive_history_capacity' not in control
    # Capacities belong in Experiment Setup, not a duplicate Visualizer dialog.
    assert 'capacityDialog' not in visualizer_html
    assert 'capacityEditorLink' not in visualizer_html
