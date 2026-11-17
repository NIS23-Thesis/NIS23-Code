"""Single-origin CLIVE controller + visualizer routing and consistent web shell."""
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
CLIVE = ROOT / 'clive'
sys.path.insert(0, str(CLIVE))
from control_center import ExperimentController, Server


def test_combined_server_serves_every_page_and_api_from_one_port(tmp_path):
    server = Server(('127.0.0.1', 0), ExperimentController(tmp_path), '/topology/', True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        for path, expected in (
            ('/', b'Experiment <span>setup'),
            ('/topology/', b'Network topology'),
            ('/topology/learning.html', b'Model learning'),
            ('/topology/events.html', b'Recorded event history'),
            ('/topology/ui-shell.css', b'clive-site-header'),
            ('/topology/app.js', b'visualizerApiPath'),
            ('/ui-shell.css', b'clive-site-header'),
        ):
            with urlopen(base + path, timeout=4) as response:
                assert response.status == 200, path
                assert expected in response.read(), path
        with urlopen(base + '/api/bootstrap', timeout=4) as response:
            data = json.load(response)
        assert data['visualizer_url'] == '/topology/'
        with urlopen(base + '/api/viz/runs', timeout=4) as response:
            assert json.load(response) == {'runs': []}
        with urlopen(base + '/api/viz/experiment-studio', timeout=4) as response:
            assert json.load(response)['capacity_editor_url'] == '/?section=capacity'
        with urlopen(base + '/topology', timeout=4) as response:
            assert response.url == base + '/topology/'
        try:
            urlopen(base + '/api/viz/meta?run=unknown', timeout=4)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('Unknown run must not succeed')
    finally:
        server.shutdown()
        thread.join(timeout=4)
        server.server_close()


def test_all_pages_use_the_same_navigation_and_canonical_shell():
    studio = CLIVE / 'control_ui'
    viewer = CLIVE / 'visualizer/static'
    shell = (CLIVE / 'ui_shared/shell.css').read_bytes()
    assert (studio / 'ui-shell.css').read_bytes() == shell
    assert (viewer / 'ui-shell.css').read_bytes() == shell
    for page in (studio / 'index.html', viewer / 'index.html',
                 viewer / 'learning.html', viewer / 'events.html'):
        text = page.read_text()
        assert 'class="clive-site-nav"' in text
        assert '<svg viewBox="0 0 32 32"' in text
        assert 'CLIVE<small>NETWORK EMULATOR</small>' in text
    assert 'grid-template-columns:minmax(195px,1fr) auto minmax(220px,1fr)' in shell.decode()
    assert 'new URL(visualizer,location.href)' in (studio / 'app.js').read_text()
    assert 'path.replace(/^\\/api(?=\\/)/, "/api/viz")' in (viewer / 'app.js').read_text()
