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
from urllib.parse import unquote, urlparse

from orchestrator import (EmulationConfig, LinkConfig, MobilityConfig,
                          LoggingConfig, atomic_json, build_nodes)
from flame_protocol import DEFAULTS as FLAME_DEFAULTS, _configuration as validate_flame
from visualizer import RunCatalog, VisualizerServer

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
    for path in sorted(CONFIG_DIR.rglob('*.json')):
        if path.name == 'all_options.json' or path.is_symlink():
            continue
        rel = path.relative_to(CONFIG_DIR).as_posix()
        rows.append({'id': rel, 'name': path.stem.replace('_', ' ').replace('-', ' ').title(),
                     'category': path.parent.relative_to(CONFIG_DIR).as_posix(),
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
    legal = {'num_ch', 'num_sn', 'runtime', 'mobility', 'link', 'flame', 'logging'}
    unknown = set(data) - legal
    if unknown:
        raise ValueError(f'Unknown top-level settings: {sorted(unknown)}')
    for key in ('num_ch', 'num_sn'):
        if type(data.get(key)) is not int or not 0 <= data[key] <= 200:
            raise ValueError(f'{key} must be an integer between 0 and 200')
    if data['num_ch'] < 1 or data['num_ch'] + data['num_sn'] > 200:
        raise ValueError('Use at least one CH and at most 200 nodes total')
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
    if duration / tick * (data['num_ch'] + data['num_sn']) > 6_000_000:
        raise ValueError('Scenario is too large for the GUI safety limit (6 million node-ticks)')
    # Check manual coordinate coverage and node naming without precomputing a trace.
    mob = MobilityConfig.from_dict(mobility)
    if mob.initial_topology == 'manual':
        expected = {n.name for n in build_nodes(data['num_ch'], data['num_sn'])}
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
        self.last_status = 'idle'
        self.last_exit_code = None
        self.run_name = None
        self.existing_runs = set()
        self.selected_name = None

    def _refresh(self):
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                self.last_exit_code = code
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
                    'started_at': self.started_at, 'exit_code': self.last_exit_code,
                    'run': self.run_name, 'scenario_name': self.selected_name,
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
            self.last_status = 'running'
            self.last_exit_code = None
            self.run_name = None
            self.existing_runs = existing_runs
            self.selected_name = str(name or 'Unsaved experiment')[:80]
        return self.status()

    def stop(self):
        with self.lock:
            self._refresh()
            if self.process is None:
                raise ValueError('No experiment is currently running')
            process = self.process
            self.last_status = 'stopped'
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

    def history(self):
        dirs = sorted((p for p in self.results_dir.glob('run-*') if p.is_dir()),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        rows = []
        for p in dirs:
            manifest = read_json(p / 'run-manifest.json', {}) or {}
            metrics = read_json(p / 'metrics.json', {}) or {}
            rows.append({'name': p.name, 'status': manifest.get('status', 'starting'),
                         'success': manifest.get('success', False),
                         'started_at': manifest.get('started_at'),
                         'duration_s': (read_json(p / 'plan.json', {}) or {}).get('config', {}).get('duration_s'),
                         'metrics': {key: (metrics.get('totals', {}) or {}).get(key) for key in
                                     ['packets_sent', 'packets_received', 'delivery_drops']}})
        return rows


class Handler(SimpleHTTPRequestHandler):
    server_version = 'CLIVEControlCenter/1.0'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

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
                self.respond({'runs': self.server.controller.history()})
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
    def __init__(self, address, controller, visualizer_url=None, quiet=False):
        self.controller = controller
        self.visualizer_url = visualizer_url
        self.quiet = quiet
        super().__init__(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9767)
    parser.add_argument('--visualizer-port', type=int, default=9766)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--results', default=str(RESULTS_DIR))
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Control dashboard may only listen on loopback')
    if not 1 <= args.port <= 65535 or not 1 <= args.visualizer_port <= 65535 or args.port == args.visualizer_port:
        parser.error('Choose two distinct valid port numbers')
    controller = ExperimentController(args.results)
    viz = None
    visualizer_url = None
    try:
        viz = VisualizerServer(('127.0.0.1', args.visualizer_port), RunCatalog(controller.results_dir), quiet=args.quiet)
        threading.Thread(target=viz.serve_forever, daemon=True, name='topology-visualizer').start()
        visualizer_url = f'http://127.0.0.1:{args.visualizer_port}/'
    except OSError as exc:
        print(f'Visualizer port unavailable ({exc}); controller still works.', file=sys.stderr)
    server = Server((args.host, args.port), controller, visualizer_url, args.quiet)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'CLIVE Experiment Control Center: {url}', flush=True)
    print(f'Topology visualizer: {visualizer_url or "unavailable"}', flush=True)
    if args.open:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    def shutdown_signal(_signum, _frame):
        # Server.shutdown must execute outside the serve_forever thread.
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
        if viz is not None:
            viz.shutdown()
            viz.server_close()


if __name__ == '__main__':
    main()
