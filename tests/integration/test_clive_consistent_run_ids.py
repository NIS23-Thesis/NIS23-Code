"""Persistence and UI contract for sequential CLIVE run numbers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
CLIVE=ROOT/'clive'
sys.path.insert(0,str(CLIVE))
from emulator import create_run_directory


def test_sequential_run_ids_survive_restart_and_deletion(tmp_path):
    legacy=tmp_path/'run-legacy8fg4';legacy.mkdir()
    assert Path(create_run_directory(tmp_path)).name == 'run-1'
    assert Path(create_run_directory(tmp_path)).name == 'run-2'
    (tmp_path/'run-2').rmdir()
    assert Path(create_run_directory(tmp_path)).name == 'run-3'
    assert legacy.exists()  # Never rename or delete older user results.
    assert (tmp_path/'_control'/'run-counter.txt').read_text().strip()=='3'


def test_allocator_uses_highest_existing_sequence(tmp_path):
    (tmp_path/'run-17').mkdir()
    (tmp_path/'run-2').mkdir()
    assert Path(create_run_directory(tmp_path)).name == 'run-18'


def test_parallel_allocator_has_no_duplicates(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        names=list(executor.map(lambda _: Path(create_run_directory(tmp_path)).name, range(24)))
    assert len(set(names))==24
    assert set(names)=={f'run-{i}' for i in range(1,25)}


def test_all_page_headers_have_one_run_picker_and_identical_labels():
    pages=(CLIVE/'control_ui/index.html',CLIVE/'visualizer/static/index.html',
           CLIVE/'visualizer/static/learning.html',CLIVE/'visualizer/static/events.html')
    navs=[]
    for page in pages:
        html=page.read_text()
        header=re.search(r'<header\b.*?</header>',html,re.S).group()
        labels=re.findall(r'>(Setup|Topology|Learning|Events|Run History)</(?:a|button)>',header)
        assert labels == ['Setup','Topology','Learning','Events','Run History']
        assert header.count('id="runSelect"')==1
        assert header.count('class="clive-site-nav"')==1
        assert 'VIEWING RUN' not in header
        navs.append(labels)
    assert navs[0]==navs[1]==navs[2]==navs[3]


def test_shared_styles_and_header_generator_are_repeatable():
    canonical=(CLIVE/'ui_shared/shell.css').read_bytes()
    for part in ('control_ui','visualizer/static'):
        assert (CLIVE/part/'ui-shell.css').read_bytes()==canonical
    assert 'def header(page):' in (CLIVE/'scripts/sync_ui_shell.py').read_text()


def test_selected_run_is_not_replaced_by_selected_preset():
    source=(CLIVE/'control_ui/app.js').read_text()
    assert "writeSelectedRun(select.value)" in source
    assert "runVisualizerUrl(run)" in source
    assert "Number(s.duration_s)" in source


def test_studio_highlight_changes_with_history_view():
    source=(CLIVE/'control_ui/app.js').read_text()
    assert "el.classList.toggle('active',active)" in source
    assert "el.removeAttribute('aria-current')" in source


def test_controller_and_visualizer_list_same_sequential_runs(tmp_path):
    import json
    from threading import Thread
    from urllib.request import urlopen
    from control_center import ExperimentController, Server
    for index in (1,2):
        run = Path(create_run_directory(tmp_path))
        assert run.name == f'run-{index}'
        (run/'run-manifest.json').write_text(json.dumps({'status':'complete','success':True,'started_at':100+index}))
        (run/'metrics.json').write_text(json.dumps({'totals':{'packets_sent':index}}))
        (run/'plan.json').write_text(json.dumps({'config':{'duration_s':10}}))
    server=Server(('127.0.0.1',0),ExperimentController(tmp_path),quiet=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/api/history',timeout=4) as response:
            history=json.load(response)
        with urlopen(base+'/api/viz/runs',timeout=4) as response:
            visualizer=json.load(response)
        assert {r['name'] for r in history['runs']}=={'run-1','run-2'}
        assert {r['name'] for r in visualizer['runs']}=={'run-1','run-2'}
        with urlopen(base+'/api/run/run-1',timeout=4) as response:
            assert json.load(response)['metrics']['totals']['packets_sent']==1
    finally:
        server.shutdown();thread.join(timeout=3);server.server_close()
