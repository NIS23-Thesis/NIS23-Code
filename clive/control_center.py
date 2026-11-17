#!/usr/bin/env python3
"""Local CLIVE experiment dashboard: scenario editor and experiment lifecycle.

The controller runs only on loopback, never imports/executes user-supplied code,
never changes protocol logic, and supervises CLIVE as a separate process group.
Open from the project root with: python clive/control_center.py --open
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import fields
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from orchestrator import (EmulationConfig, LinkConfig, MobilityConfig,
                          LoggingConfig, NodeRole, NodeSpec, atomic_json, build_nodes)
from flame_protocol import DEFAULTS as FLAME_DEFAULTS, _configuration as validate_flame
from visualizer import RunCatalog, VisualizerHandler, STATIC_DIR as VISUALIZER_STATIC_DIR

CLIVE_DIR = Path(__file__).resolve().parent
STATIC_DIR = CLIVE_DIR / 'control_ui'
CONFIG_DIR = CLIVE_DIR / 'configs'
GUI_DIR = CONFIG_DIR / 'gui'
RESULTS_DIR = CLIVE_DIR / 'results'
OPTIONS = json.loads((CONFIG_DIR / 'all_options.json').read_text(encoding='utf8'))
# The complete form schema comes from the existing dataclasses and FLAME options.
DEFAULT_SCENARIO = {
    'num_ch': 2, 'num_sn': 4,
    'runtime': {f.name: getattr(EmulationConfig(), f.name) for f in fields(EmulationConfig)},
    'mobility': {f.name: getattr(MobilityConfig(), f.name) for f in fields(MobilityConfig)},
    'link': {f.name: getattr(LinkConfig(), f.name) for f in fields(LinkConfig)},
    'flame': dict(FLAME_DEFAULTS),
    'logging': {f.name: getattr(LoggingConfig(), f.name) for f in fields(LoggingConfig)},
}
# Supply nonempty demonstrations for optional structured fields; the frontend
# supports enabling/disabling optional fields and editing dictionaries/arrays.
OPTION_EXAMPLES = {
    'mobility': OPTIONS.get('mobility', {}),
    'link': OPTIONS.get('link', {}),
}
SCALAR_LIMIT = 300_000


def read_json(path, fallback=None):
    try:
        with path.open(encoding='utf8') as stream:
            return json.load(stream)
    except (ValueError, OSError):
        return fallback


def presets():
    rows = []
    for path in sorted(list((CONFIG_DIR / 'demos').glob('panel-*.json')) + list(GUI_DIR.glob('*.json'))):
        if path.name in {'all_options.json', 'manifest.json'} or path.is_symlink():
            continue
        rel = path.relative_to(CONFIG_DIR).as_posix()
        name = path.stem.replace('_', ' ').replace('-', ' ').title()
        panel_names = {
            'panel-01': '01 · Static no-loss · 2 CH / 2 SN',
            'panel-02': '02 · HELLO + JOIN · 2 CH / 4 SN',
            'panel-03': '03 · Model exchange · 2 CH / 4 SN',
            'panel-04': '04 · Gauss–Markov · 2 CH / 4 SN',
            'panel-05': '05 · RSSI loss · 2 CH / 4 SN',
        }
        name = next((label for prefix, label in panel_names.items() if path.stem.startswith(prefix)), name)
        category = path.parent.relative_to(CONFIG_DIR).as_posix()
        if category == 'evaluations/thesis_matrix':
            # Present scenario dimensions, not cryptic m-..._d-... codes.
            match = re.fullmatch(r'm-(none|low|medium|high)_d-(low|medium|high)_f-(low|medium|high)_a-(small|medium|large)', path.stem)
            if match:
                mobility, density, frequency, area = match.groups()
                ch, sn = {'low': (4, 20), 'medium': (7, 50), 'high': (10, 100)}[density]
                interval = {'low': 60, 'medium': 30, 'high': 15}[frequency]
                length = {'small': 200, 'medium': 350, 'large': 500}[area]
                name = f'Mobility {mobility.title()} | {ch} CH + {sn} SN | {interval}s samples | {length}x{length}m'
            category = 'THESIS EVALUATION (300-second matrix)'
        elif category == 'demos':
            category = 'PANEL DEMOS — ALL MNIST / PYTORCH'
        rows.append({'id': rel, 'name': name, 'category': category,
                     'custom': path.parent == GUI_DIR})
    return rows


def preset_path(name):
    if not isinstance(name, str) or not name or name not in {r['id'] for r in presets()}:
        raise ValueError('Unknown scenario preset')
    return CONFIG_DIR / name


def validated_scenario(data):
    """Reject unknown/invalid fields before writing or launching a scenario.

    This is cheap metadata validation; CLI still performs the authoritative full
    topology, trace and protocol validation in its separate worker process.
    """
    if not isinstance(data, dict):
        raise ValueError('Scenario must be an object')
    legal = {'num_ch', 'num_sn', 'nodes', 'addressing', 'runtime', 'mobility', 'link', 'flame', 'logging'}
    unknown = set(data) - legal
    if unknown:
        raise ValueError(f'Unknown top-level settings: {sorted(unknown)}')
    if 'nodes' in data:
        if 'addressing' in data:
            raise ValueError('Use nodes or addressing, not both')
        if not isinstance(data['nodes'], list) or not data['nodes']:
            raise ValueError('nodes must be a nonempty list')
        specs = [NodeSpec.from_dict(value) for value in data['nodes']]
        actual_ch = sum(node.role == NodeRole.CLUSTER_HEAD for node in specs)
        actual_sn = len(specs) - actual_ch
        for key, actual in (('num_ch', actual_ch), ('num_sn', actual_sn)):
            if key in data and data[key] != actual:
                raise ValueError(f'{key} does not match nodes')
        names = [node.name for node in specs]
        overlays = [node.address for node in specs]
        underlays = [node.underlay_address for node in specs]
        if len(set(names)) != len(names) or len(set(overlays)) != len(overlays) or len(set(ip for ip, _ in underlays)) != len(underlays):
            raise ValueError('Node names, overlay endpoints and underlay source IPs must be unique')
        n_ch, n_sn = actual_ch, actual_sn
    else:
        for key in ('num_ch', 'num_sn'):
            if type(data.get(key)) is not int or not 0 <= data[key] <= 200:
                raise ValueError(f'{key} must be an integer between 0 and 200')
        n_ch, n_sn = data['num_ch'], data['num_sn']
        specs = build_nodes(n_ch, n_sn, addressing=data.get('addressing'))
    if n_ch < 1 or n_ch > 200 or n_sn > 200 or n_ch + n_sn > 220:
        raise ValueError('Use at least one CH, at most 200 per role, and at most 220 nodes total')
    result = dict(data)
    for group, cls in [('runtime', EmulationConfig), ('link', LinkConfig),
                       ('mobility', MobilityConfig), ('logging', LoggingConfig)]:
        block = result.get(group, {})
        if not isinstance(block, dict):
            raise ValueError(f'{group} must be an object')
        # Always validate a copy: the constructors normalize a few values.
        instance = cls.from_dict(block) if hasattr(cls, 'from_dict') else cls(**block)
        instance.validate()
    flame = result.get('flame', {})
    if not isinstance(flame, dict) or set(flame) - set(FLAME_DEFAULTS):
        raise ValueError('Invalid FLAME settings')
    class PlanStub:
        flame_settings = flame
    validate_flame(PlanStub())
    runtime = result.get('runtime', {})
    mobility = result.get('mobility', {})
    duration = float(runtime.get('duration_s', 300))
    tick = float(mobility.get('tick_interval', 0.1))
    if duration / tick * (n_ch + n_sn) > 6_000_000:
        raise ValueError('Scenario is too large for the GUI safety limit (6 million node-ticks)')
    # Check manual coordinate coverage and node naming without precomputing a trace.
    mob = MobilityConfig.from_dict(mobility)
    if mob.initial_topology == 'manual':
        expected = {n.name for n in specs}
        actual = set(mob.manual_positions)
        if expected != actual:
            raise ValueError('Manual positions must contain precisely these nodes: ' + ', '.join(sorted(expected)))
    return result


class ExperimentController:
    def __init__(self, results_dir):
        self.results_dir = Path(results_dir).resolve()
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots = self.results_dir / '_control'
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.log_path = None
        self.log_handle = None
        self.started_at = None
        self.finished_at = None
        self.last_status = 'idle'
        self.last_exit_code = None
        self.run_name = None
        self.existing_runs = set()
        self.selected_name = None
        self.duration_s = None
        self.startup_delay_s = None

    def _refresh(self):
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                self.last_exit_code = code
                self.finished_at = time.time()
                if self.last_status != 'stopped':
                    self.last_status = 'complete' if code == 0 else 'failed'
                self.process = None
                if self.log_handle:
                    self.log_handle.close()
                    self.log_handle = None
        # Use run directories, not stdout parsing, to identify the output folder.
        if self.started_at and self.run_name is None:
            recent = [p for p in self.results_dir.glob('run-*') if p.is_dir()
                      and p.name not in self.existing_runs]
            if recent:
                self.run_name = max(recent, key=lambda p: p.stat().st_mtime).name

    def status(self):
        with self.lock:
            self._refresh()
            return {'status': self.last_status, 'running': self.process is not None,
                    'pid': self.process.pid if self.process else None,
                    'started_at': self.started_at, 'finished_at': self.finished_at, 'exit_code': self.last_exit_code,
                    'run': self.run_name, 'scenario_name': self.selected_name,
                    'duration_s': self.duration_s, 'startup_delay_s': self.startup_delay_s,
                    'log': self.log_tail()}

    def log_tail(self):
        if not self.log_path or not self.log_path.exists():
            return ''
        with self.log_path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 24_000))
            return stream.read().decode('utf8', errors='replace')

    def start(self, scenario, name):
        validated_scenario(scenario)
        with self.lock:
            self._refresh()
            if self.process is not None:
                raise RuntimeError('An experiment is already running. Stop it before starting another.')
            job = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:7]
            config = self.snapshots / (job + '.json')
            log_path = self.snapshots / (job + '.log')
            atomic_json(str(config), scenario)
            log_handle = log_path.open('w', encoding='utf8', buffering=1)
            command = [sys.executable, '-u', str(CLIVE_DIR / 'clive.py'), '--scenario',
                       str(config), '--results', str(self.results_dir)]
            existing_runs = {p.name for p in self.results_dir.glob('run-*') if p.is_dir()}
            try:
                process = subprocess.Popen(command, cwd=CLIVE_DIR, stdin=subprocess.DEVNULL,
                                           stdout=log_handle, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            except BaseException:
                log_handle.close()
                raise
            self.process = process
            self.log_handle = log_handle
            self.log_path = log_path
            self.started_at = time.time()
            self.finished_at = None
            self.last_status = 'running'
            self.last_exit_code = None
            self.run_name = None
            self.existing_runs = existing_runs
            self.selected_name = str(name or 'Unsaved experiment')[:80]
            self.duration_s = float(scenario.get('runtime', {}).get('duration_s', 0))
            self.startup_delay_s = float(scenario.get('runtime', {}).get('startup_delay_s', 0))
        return self.status()

    def stop(self):
        with self.lock:
            self._refresh()
            if self.process is None:
                raise ValueError('No experiment is currently running')
            process = self.process
            self.last_status = 'stopped'
            self.finished_at = time.time()
            # CLIVE spawns worker and radio processes; target the entire session,
            # not only the top-level Python launcher.
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            self._refresh()
            if self.run_name:
                manifest_path = self.results_dir / self.run_name / 'run-manifest.json'
                manifest = read_json(manifest_path, {}) or {}
                if manifest.get('status') not in ('complete', 'failed'):
                    manifest.update(status='stopped', success=False, stopped_at=time.time())
                    atomic_json(str(manifest_path), manifest)
        return self.status()

    def history(self, offset=0, limit=30, query='', include_total=False):
        """Return a stable, searchable run page without silently hiding older runs.

        None means all runs (used by the shared header selector). For History,
        paginate *before* reading per-run metrics to avoid expensive full scans.
        """
        if not isinstance(offset, int) or offset < 0:
            raise ValueError('Run-history offset must be non-negative')
        if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 200):
            raise ValueError('Run-history page size must be between 1 and 200')
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError('Run-history search is too long')
        needle = query.strip().casefold()
        dirs = sorted((p for p in self.results_dir.glob('run-*') if p.is_dir()),
                      key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        if needle:
            dirs = [p for p in dirs if needle in p.name.casefold() or
                    needle in str((read_json(p / 'run-manifest.json', {}) or {}).get('status', '')).casefold()]
        total = len(dirs)
        selected = dirs[offset:] if limit is None else dirs[offset:offset + limit]
        rows = []
        for p in selected:
            manifest = read_json(p / 'run-manifest.json', {}) or {}
            metrics = read_json(p / 'metrics.json', {}) or {}
            rows.append({'name': p.name, 'status': manifest.get('status', 'starting'),
                         'success': manifest.get('success', False),
                         'started_at': manifest.get('started_at'),
                         'duration_s': (read_json(p / 'plan.json', {}) or {}).get('config', {}).get('duration_s'),
                         'metrics': {key: (metrics.get('totals', {}) or {}).get(key) for key in
                                     ['packets_sent', 'packets_received', 'delivery_drops']}})
        if include_total:
            return {'runs': rows, 'total': total, 'offset': offset,
                    'limit': limit, 'has_more': offset + len(rows) < total}
        return rows


class Handler(VisualizerHandler):
    server_version = 'CLIVEControlCenter/1.0'
    def __init__(self, *args, **kwargs):
        # Normal web pages are Studio assets; the /topology/ route switches
        # to the read-only Visualizer assets without opening a second port.
        SimpleHTTPRequestHandler.__init__(self, *args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        if not self.server.quiet:
            super().log_message(format, *args)

    def respond(self, data, status=200):
        body = json.dumps(data, allow_nan=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        if self.headers.get('Content-Type', '').split(';')[0].lower().strip() != 'application/json':
            raise ValueError('Content-Type must be application/json')
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://{host}', f'https://{host}'):
            raise ValueError('Cross-origin control requests are blocked')
        length = int(self.headers.get('Content-Length', '0'))
        if length < 2 or length > SCALAR_LIMIT:
            raise ValueError('Request body too large or empty')
        return json.loads(self.rfile.read(length).decode('utf8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        # Serve all browser pages and both API families from a single origin.
        # The Visualizer handler is reused rather than reimplementing its APIs.
        if parsed.path.startswith('/api/viz/'):
            self.path = '/api/' + parsed.path[len('/api/viz/'):] + (
                '?' + parsed.query if parsed.query else '')
            return VisualizerHandler.do_GET(self)
        if parsed.path == '/topology':
            self.send_response(HTTPStatus.PERMANENT_REDIRECT)
            self.send_header('Location', '/topology/' + ('?' + parsed.query if parsed.query else ''))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if parsed.path.startswith('/topology/'):
            suffix = parsed.path[len('/topology/'):] or 'index.html'
            # Keep all visualizer assets within its dedicated static directory.
            self.path = '/' + suffix + ('?' + parsed.query if parsed.query else '')
            self.directory = str(VISUALIZER_STATIC_DIR)
            return VisualizerHandler.do_GET(self)
        try:
            if parsed.path == '/api/bootstrap':
                self.respond({'presets': presets(), 'defaults': DEFAULT_SCENARIO,
                              'examples': OPTION_EXAMPLES,
                              'visualizer_url': self.server.visualizer_url,
                              'status': self.server.controller.status(),
                              'runs': self.server.controller.history()})
            elif parsed.path.startswith('/api/scenario/'):
                name = unquote(parsed.path[len('/api/scenario/'):])
                self.respond({'id': name, 'scenario': read_json(preset_path(name))})
            elif parsed.path == '/api/status':
                self.respond(self.server.controller.status())
            elif parsed.path == '/api/history':
                parameters = parse_qs(parsed.query, keep_blank_values=True)
                raw_offset = parameters.get('offset', ['0'])[0]
                raw_limit = parameters.get('limit', ['30'])[0]
                try:
                    offset = int(raw_offset)
                    limit = None if raw_limit == 'all' else int(raw_limit)
                except ValueError as exc:
                    raise ValueError('Invalid run-history pagination') from exc
                query = parameters.get('search', [''])[0]
                self.respond(self.server.controller.history(
                    offset=offset, limit=limit, query=query, include_total=True))
            elif parsed.path.startswith('/api/run/'):
                name = unquote(parsed.path[len('/api/run/'):])
                path = self.server.controller.results_dir / name
                if not re.fullmatch(r'run-[A-Za-z0-9_-]+', name) or not path.is_dir():
                    raise ValueError('Unknown run')
                self.respond({'manifest': read_json(path / 'run-manifest.json', {}) or {},
                              'metrics': read_json(path / 'metrics.json', {}) or {},
                              'flame': {f.stem: read_json(f, {}) for f in sorted((path / 'flame').glob('*.json'))}})
            elif parsed.path.startswith('/api/'):
                self.respond({'error': 'Unknown endpoint'}, HTTPStatus.NOT_FOUND)
            else:
                self.path = '/index.html' if parsed.path == '/' else parsed.path
                return super().do_GET()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.respond({'error': str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        try:
            body = self.read_body()
            path = urlparse(self.path).path
            if path == '/api/save':
                scenario = validated_scenario(body.get('scenario'))
                name = str(body.get('name', '')).strip()
                slug = re.sub(r'[^a-z0-9_-]+', '-', name.lower()).strip('-')[:64]
                if not slug:
                    raise ValueError('Please enter a preset name')
                GUI_DIR.mkdir(parents=True, exist_ok=True)
                target = GUI_DIR / (slug + '.json')
                atomic_json(str(target), scenario)
                self.respond({'id': 'gui/' + target.name, 'name': name, 'presets': presets()})
            elif path == '/api/validate':
                validated_scenario(body.get('scenario'))
                self.respond({'valid': True})
            elif path == '/api/start':
                self.respond(self.server.controller.start(body.get('scenario'), body.get('name')))
            elif path == '/api/stop':
                self.respond(self.server.controller.stop())
            else:
                self.respond({'error': 'Unknown endpoint'}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
            self.respond({'error': str(exc)}, HTTPStatus.BAD_REQUEST)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller, visualizer_url='/topology/', quiet=False):
        self.controller = controller
        self.catalog = RunCatalog(controller.results_dir)
        self.capacity_editor_url = '/?section=capacity'
        self.visualizer_url = visualizer_url
        self.quiet = quiet
        super().__init__(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9767,
                        help='Single CLIVE web port for Setup, Topology, Learning, Events, and History')
    parser.add_argument('--visualizer-port', type=int, default=None,
                        help=argparse.SUPPRESS)  # Old launcher compatibility; no second server.
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--results', default=str(RESULTS_DIR))
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Control dashboard may only listen on loopback')
    if not 1 <= args.port <= 65535:
        parser.error('Choose a valid port number')
    controller = ExperimentController(args.results)
    server = Server((args.host, args.port), controller, '/topology/', args.quiet)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'CLIVE unified workspace: {url}', flush=True)
    print(f'Topology: {url}topology/', flush=True)
    print('All views and APIs share one origin; no separate Visualizer port.', flush=True)
    if args.open:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    def shutdown_signal(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    if os.name == 'posix':
        signal.signal(signal.SIGTERM, shutdown_signal)
    try:
        server.serve_forever(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if controller.status()['running']:
            controller.stop()
        server.server_close()


if __name__ == '__main__':
    main()
