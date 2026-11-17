"""Regression checks for the CLIVE run-history archive (not FLAME)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'clive'))
sys.path.insert(0, str(ROOT))

from control_center import ExperimentController, Server


def build_history(root: Path, count: int = 113):
    for i in range(1, count + 1):
        folder = root / f'run-{i}'
        folder.mkdir()
        (folder / 'run-manifest.json').write_text(json.dumps({
            'status': 'complete' if i % 2 == 0 else 'failed',
            'success': i % 2 == 0, 'started_at': 1700000000 + i,
        }))
        (folder / 'metrics.json').write_text(json.dumps({'totals': {'packets_sent': i}}))
        (folder / 'plan.json').write_text(json.dumps({'config': {'duration_s': 10}}))


def test_paged_history_can_reach_all_formal_runs():
    with tempfile.TemporaryDirectory(prefix='clive-run-history-') as directory:
        root = Path(directory)
        build_history(root)
        controller = ExperimentController(root)
        pages = [controller.history(offset=n, limit=30, include_total=True) for n in (0, 30, 60, 90)]
        assert all(page['total'] == 113 for page in pages)
        assert [len(page['runs']) for page in pages] == [30, 30, 30, 23]
        assert [page['has_more'] for page in pages] == [True, True, True, False]
        names = [item['name'] for page in pages for item in page['runs']]
        assert len(names) == 113 and len(set(names)) == 113
        assert {f'run-{i}' for i in range(1,114)} == set(names)
        assert len(controller.history(limit=None)) == 113
        assert controller.history(offset=0,limit=30,query='run-102',include_total=True)['total'] == 1
        assert controller.history(offset=0,limit=30,query='complete',include_total=True)['total'] == 56


def test_history_http_pagination_search_and_validation():
    with tempfile.TemporaryDirectory(prefix='clive-run-history-http-') as directory:
        root = Path(directory)
        build_history(root, 110)
        server = Server(('127.0.0.1', 0), ExperimentController(root), quiet=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def json_at(url):
            with urlopen(base+url, timeout=5) as response:
                return json.load(response)
        try:
            result = json_at('/api/history?offset=90&limit=30')
            assert result['total'] == 110
            assert len(result['runs']) == 20
            assert not result['has_more']
            assert len(json_at('/api/history?limit=all')['runs']) == 110
            assert json_at('/api/history?search=run-101')['total'] == 1
            assert json_at('/api/history?search=FAILED')['total'] == 55
            for suffix in ('?offset=-1', '?limit=201', '?offset=oops'):
                try:
                    json_at('/api/history'+suffix)
                except HTTPError as e:
                    assert e.code == 400
                else:
                    raise AssertionError('Bad pagination unexpectedly accepted: '+suffix)
            # The unified pages are still served at one origin.
            for path in ('/','/topology/','/topology/learning.html','/topology/events.html'):
                with urlopen(base+path, timeout=5) as response:
                    assert response.status == 200
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
