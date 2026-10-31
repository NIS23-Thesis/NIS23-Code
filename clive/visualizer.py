"""CLIVE localhost visualization server.

The visualizer is deliberately observational: it reads CLIVE's run artifacts. plan.json
provides topology/configuration metadata, mobility.bin provides the exact trace, and metrics.json
provides compact final counters.
and optional debug JSONL streams are used only when diagnostic logging is enabled. The
visualizer never calls protocol code or advances mobility/protocol timers.

It uses only Python's standard library. The browser frontend is plain JavaScript + Canvas.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import threading
import time
import webbrowser
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from orchestrator import EmulationPlan

STATIC_DIR = Path(__file__).resolve().parent / "visualizer" / "static"
NUMERIC_METRIC_FIELDS = {
    "packets_attempted",
    "bits_attempted",
    "packets_admitted",
    "bits_admitted",
    "packets_sent",
    "bits_sent",
    "energy_sent_j",
    "packets_received",
    "bits_received",
    "energy_received_j",
    "packets_dropped",
    "delivery_attempts",
    "delivery_drops",
    "kernel_datagrams_sent",
    "socket_errors",
}


def _load_json(path: Path, default=None):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _safe_float(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


class RunData:
    """Lazy, thread-safe view over one CLIVE run directory."""

    def __init__(self, run_dir: Path, max_events: int = 200_000):
        self.run_dir = run_dir.resolve()
        self._lock = threading.RLock()
        # Mobility/frame reads must not wait behind expensive packet/event analytics.
        self._plan_lock = threading.RLock()
        self._plan = None
        self._plan_mtime_ns = None
        self._manifest = None
        self._manifest_mtime_ns = None
        self._file_state = {}
        self._events = deque(maxlen=max_events)
        # Compact, time-indexed application relationships. Unlike radio links, these
        # are evidenced by FLAME protocol events and are never inferred from distance.
        self._relationships = deque(maxlen=50_000)
        self._next_seq = 1
        self._address_to_name = {}
        self._trace_payload_cache = None
        self._trace_payload_mtime_ns = None
        self._refresh_plan(force=True)
        self._refresh_manifest(force=True)

    @property
    def name(self):
        return self.run_dir.name

    def _refresh_plan(self, force=False):
        with self._plan_lock:
            path = self.run_dir / "plan.json"
            try:
                mtime = path.stat().st_mtime_ns
            except FileNotFoundError:
                return False
            if force or mtime != self._plan_mtime_ns:
                plan = EmulationPlan.load(str(path))
                self._plan = plan
                self._plan_mtime_ns = mtime
                self._address_to_name = {node.address: node.name for node in plan.nodes}
                self._trace_payload_cache = None
                self._trace_payload_mtime_ns = None
                return True
            return False

    def _refresh_manifest(self, force=False):
        path = self.run_dir / "run-manifest.json"
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            self._manifest = None
            return False
        if force or mtime != self._manifest_mtime_ns:
            self._manifest = _load_json(path, {}) or {}
            self._manifest_mtime_ns = mtime
            return True
        return False

    def refresh(self):
        with self._lock:
            self._refresh_plan()
            self._refresh_manifest()
            self._refresh_events()

    @property
    def plan(self):
        with self._lock:
            self._refresh_plan()
            if self._plan is None:
                raise FileNotFoundError(f"{self.run_dir}/plan.json is not ready")
            return self._plan

    @property
    def manifest(self):
        with self._lock:
            self._refresh_manifest()
            return dict(self._manifest or {})

    def meta(self):
        with self._lock:
            self._refresh_plan()
            self._refresh_manifest()
            if self._plan is None:
                return {"ready": False, "run": self.name}
            plan = self._plan
            manifest = dict(self._manifest or {})
            return {
                "ready": True,
                "run": self.name,
                "run_dir": str(self.run_dir),
                "manifest": manifest,
                "duration_s": plan.config.duration_s,
                "tick_interval_s": plan.mobility_config.tick_interval,
                "tick_count": plan.tick_count,
                "nodes": [
                    {
                        "name": node.name,
                        "role": int(node.role),
                        "role_name": "Cluster Head" if int(node.role) == 1 else "Sensor Node",
                        "ip": node.ip,
                        "port": node.port,
                        "max_tx_range_m": plan.link_config.tx_range_m(node),
                    }
                    for node in plan.nodes
                ],
                "mobility": plan.mobility_config.to_dict(),
                "link": plan.link_config.to_dict(),
                "runtime": plan.config.to_dict(),
                "logging": plan.logging_config.to_dict(),
                "protocol": manifest.get("protocol", "protocol_interface:NullProtocol"),
                "epoch_start": plan.epoch_start,
                "trace_sha256": plan.trace_digest(),
                "trace_storage": dict(plan.trace_storage or {}),
            }

    def mobility_trace(self):
        """Return the exact precomputed mobility trace in a compact browser-friendly form.

        This endpoint is intentionally loaded once per run. Playback then selects the exact
        stored tick locally in the browser; it does not interpolate and it does not issue a
        network request on every mobility tick.
        """
        with self._plan_lock:
            self._refresh_plan()
            if self._plan is None:
                return {"ready": False}
            plan = self._plan
            nodes = list(plan.nodes)
            trace_mtime = None
            if plan.trace_file_path:
                try:
                    trace_mtime = os.stat(plan.trace_file_path).st_mtime_ns
                except FileNotFoundError:
                    trace_mtime = None
            cache_stamp = (self._plan_mtime_ns, trace_mtime)
            if self._trace_payload_cache is not None and self._trace_payload_mtime_ns == cache_stamp:
                return self._trace_payload_cache

            frames = []
            with plan.open_trace() as trace:
                for tick in range(plan.tick_count):
                    frames.append(list(trace.frame_flat(tick)))

            payload = {
                "ready": True,
                "run": self.name,
                "tick_interval_s": plan.mobility_config.tick_interval,
                "duration_s": plan.config.duration_s,
                "node_names": [node.name for node in nodes],
                "node_roles": [int(node.role) for node in nodes],
                "frames": frames,
            }
            self._trace_payload_cache = payload
            self._trace_payload_mtime_ns = cache_stamp
            return payload

    def mobility_binary_path(self):
        """Return the validated canonical mobility binary for direct browser streaming."""
        with self._plan_lock:
            self._refresh_plan()
            if self._plan is None or not self._plan.trace_file_path:
                raise FileNotFoundError("mobility.bin is not ready")
            path = Path(self._plan.trace_file_path)
            if not path.is_file():
                raise FileNotFoundError("mobility.bin is not ready")
            return path

    def frame(self, time_s):
        """Return one exact trace tick. Kept for diagnostics; normal playback uses mobility.bin."""
        with self._plan_lock:
            self._refresh_plan()
            if self._plan is None:
                return {"ready": False}
            plan = self._plan
            requested = max(0.0, min(plan.config.duration_s, _safe_float(time_s)))
            interval = plan.mobility_config.tick_interval
            tick = min(int(requested / interval), plan.tick_count - 1)
            with plan.open_trace() as trace:
                flat = trace.frame_flat(tick)
                positions = []
                for i, node in enumerate(plan.nodes):
                    j = i * 2
                    positions.append({
                        "name": node.name,
                        "role": int(node.role),
                        "x": flat[j],
                        "y": flat[j + 1],
                    })
            return {
                "ready": True,
                "requested_time_s": requested,
                "tick": tick,
                "tick_time_s": plan.time_for_tick(tick),
                "positions": positions,
            }


    def _node_from_address(self, value):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        try:
            return self._address_to_name.get((str(value[0]), int(value[1])))
        except (TypeError, ValueError):
            return None

    def _normalize_event(self, raw, filename, stream_type):
        event_type = raw.get("event") or raw.get("type") or "unknown"
        node_name = raw.get("node_name") or filename.split(".", 1)[0]
        time_s = _safe_float(raw.get("time_s"), -1.0)
        source_node = None
        target_node = None

        if event_type in {"receive", "protocol_receive", "flame_sensor_received", "flame_model_received"}:
            source_node = self._node_from_address(raw.get("source"))
            target_node = node_name
        else:
            source_node = node_name
            target_node = self._node_from_address(raw.get("target"))
            if target_node is None:
                destination = raw.get("destination")
                if isinstance(destination, (list, tuple)) and destination and destination[0] != "255.255.255.255":
                    target_node = self._node_from_address(destination)

        header = raw.get("common_header") or {}
        counters = raw.get("counters") if isinstance(raw.get("counters"), dict) else {}
        event = {
            "seq": self._next_seq,
            "time_s": time_s,
            "event": event_type,
            "stream": stream_type,
            "node": node_name,
            "source": source_node,
            "target": target_node,
            "packet_type": header.get("packet_type"),
            "is_broadcast": bool(raw.get("is_broadcast", False)),
            "sequence_number": header.get("sequence_number"),
            "reason": raw.get("reason"),
            "distance_m": raw.get("distance_m"),
            "rssi_dbm": raw.get("rssi_dbm"),
            "bandwidth_bps": raw.get("bandwidth_bps"),
            "plr": raw.get("plr"),
            "mobility_tick": raw.get("mobility_tick", raw.get("mobility_mobility_tick")),
            "accepted_bytes": raw.get("accepted_bytes"),
            "bytes": raw.get("bytes"),
            "counters": {k: v for k, v in counters.items() if k in NUMERIC_METRIC_FIELDS},
        }
        self._next_seq += 1
        return event

    def _refresh_events(self):
        patterns = (
            ("debug/packets/*.jsonl", "packet"),
            ("debug/events/*.jsonl", "protocol"),
            # Legacy layouts remain readable.
            ("*.packets.jsonl", "packet"),
            ("*.events.jsonl", "protocol"),
        )
        for pattern, stream_type in patterns:
            for raw_path in glob.glob(str(self.run_dir / pattern)):
                path = Path(raw_path)
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                state = self._file_state.setdefault(str(path), {"offset": 0, "size": 0})
                if size < state["offset"]:
                    state["offset"] = 0
                if size == state["offset"]:
                    state["size"] = size
                    continue
                try:
                    with path.open("r", encoding="utf-8") as stream:
                        stream.seek(state["offset"])
                        while True:
                            line_start = stream.tell()
                            line = stream.readline()
                            if not line:
                                break
                            if not line.endswith("\n"):
                                stream.seek(line_start)
                                break
                            try:
                                raw = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            event = self._normalize_event(raw, path.name, stream_type)
                            self._events.append(event)
                            if stream_type == "protocol" and event["event"] in {
                                "flame_joined", "flame_leave_sent", "flame_sensor_received",
                                "flame_model_sent", "flame_model_received",
                            } and event["source"] and event["target"]:
                                self._relationships.append({
                                    "seq": event["seq"], "time_s": event["time_s"],
                                    "action": event["event"],
                                    "source": event["source"], "target": event["target"],
                                })
                        state["offset"] = stream.tell()
                        state["size"] = size
                except (OSError, UnicodeDecodeError):
                    continue

    def relationships(self):
        """Return observed FLAME relationships, not guessed membership or physical links.

        The client replays these events against the displayed time. Missing diagnostic
        logging means *unknown*, never 'not associated'. SN-side JOIN acceptance and
        CH-side sensor acceptance are reported separately so the UI does not present
        a provisional association as confirmed on both endpoints.
        """
        with self._lock:
            self._refresh_events()
            logging_enabled = bool(self._plan and self._plan.logging_config.protocol_events)
            return {
                "available": logging_enabled,
                "events": sorted(self._relationships, key=lambda e: (e["time_s"], e["seq"])),
                "truncated": len(self._relationships) == self._relationships.maxlen,
            }

    def events(self, from_s=None, to_s=None, after_seq=None, limit=2000):
        with self._lock:
            self._refresh_events()
            limit = max(1, min(int(limit), 10_000))
            result = []
            for event in self._events:
                if after_seq is not None and event["seq"] <= after_seq:
                    continue
                if from_s is not None and event["time_s"] < from_s:
                    continue
                if to_s is not None and event["time_s"] > to_s:
                    continue
                result.append(event)
            if len(result) > limit:
                result = result[-limit:]
            return {
                "events": result,
                "last_seq": self._events[-1]["seq"] if self._events else 0,
                "retained_events": len(self._events),
                "truncated": len(self._events) == self._events.maxlen,
            }

    def metrics(self):
        with self._lock:
            self._refresh_events()
            plan = self._plan
            role_by_node = {node.name: int(node.role) for node in plan.nodes} if plan else {}
            per_node = {}
            completed_metric_files = 0
            aggregate_complete = False

            # Current format: one compact metrics.json for the entire run.  One file contains
            # the final rows for every node, so completion is based on node coverage rather
            # than the number of metric files on disk.
            aggregate_path = self.run_dir / "metrics.json"
            aggregate = _load_json(aggregate_path, None) if aggregate_path.exists() else None
            if isinstance(aggregate, dict) and isinstance(aggregate.get("nodes"), dict):
                for node, value in aggregate["nodes"].items():
                    if not isinstance(value, dict):
                        continue
                    row = {key: _safe_float(value.get(key), 0.0) for key in NUMERIC_METRIC_FIELDS}
                    row.update(node_name=node, role=role_by_node.get(node), success=value.get("success"))
                    per_node[node] = row
                aggregate_complete = bool(plan) and len(per_node) >= len(plan.nodes)
                # Compatibility field for older frontends: count completed node rows, not the
                # single aggregate file itself.
                completed_metric_files = len(per_node)

            # Legacy format: one <node>.metrics.json per node.
            for path in self.run_dir.glob("*.metrics.json"):
                value = _load_json(path, None)
                if not isinstance(value, dict):
                    continue
                node = value.get("node_name") or path.name.split(".", 1)[0]
                row = {key: _safe_float(value.get(key), 0.0) for key in NUMERIC_METRIC_FIELDS}
                row.update(node_name=node, role=role_by_node.get(node), success=value.get("success"))
                per_node[node] = row
                completed_metric_files += 1

            # During an active run metrics.json files may not exist yet (or only some nodes may
            # have finished). Reconstruct missing nodes from JSONL counter deltas. Current
            # CLIVE logs the same counter deltas used by MetricsCollector.
            observed = defaultdict(lambda: defaultdict(float))
            for event in self._events:
                for key, value in event.get("counters", {}).items():
                    observed[event["node"]][key] += _safe_float(value)
            for node, values in observed.items():
                if node in per_node:
                    continue
                row = {key: values.get(key, 0.0) for key in NUMERIC_METRIC_FIELDS}
                row.update(node_name=node, role=role_by_node.get(node), success=None)
                per_node[node] = row

            totals = {key: 0.0 for key in NUMERIC_METRIC_FIELDS}
            for row in per_node.values():
                for key in NUMERIC_METRIC_FIELDS:
                    totals[key] += row.get(key, 0.0)

            attempts = totals["delivery_attempts"]
            drops = totals["delivery_drops"]
            totals["observed_delivery_drop_ratio"] = drops / attempts if attempts else 0.0
            totals["completed_metric_files"] = completed_metric_files
            totals["node_metric_rows"] = len(per_node)
            return {
                "complete": aggregate_complete,
                "source": "aggregate" if aggregate_complete else ("events" if self._events else "none"),
                "totals": totals,
                "nodes": sorted(per_node.values(), key=lambda row: row["node_name"]),
            }

    def timeline(self, bucket_s=1.0):
        with self._lock:
            self._refresh_events()
            bucket_s = max(0.1, min(_safe_float(bucket_s, 1.0), 60.0))
            duration_s = self._plan.config.duration_s if self._plan else 0.0
            buckets = defaultdict(lambda: {"sent": 0, "received": 0, "dropped": 0})
            totals = {"sent": 0, "received": 0, "dropped": 0}
            max_t = duration_s
            for event in self._events:
                t = event["time_s"]
                if t < 0:
                    continue
                index = int(t / bucket_s)
                max_t = max(max_t, t)
                if event["event"] == "transmit":
                    buckets[index]["sent"] += 1
                    totals["sent"] += 1
                elif event["event"] == "receive":
                    buckets[index]["received"] += 1
                    totals["received"] += 1
                elif event["event"] in {"delivery_drop", "local_drop"}:
                    buckets[index]["dropped"] += 1
                    totals["dropped"] += 1
            rows = []
            max_index = int(max_t / bucket_s) if max_t > 0 else 0
            for index in range(max_index + 1):
                row = buckets[index]
                rows.append({"time_s": index * bucket_s, **row})
            return {
                "bucket_s": bucket_s,
                "rows": rows,
                "totals": totals,
                "has_packet_traffic": any(totals.values()),
            }



class RunCatalog:
    def __init__(self, results_dir: Path, explicit_run: Path | None = None):
        self.results_dir = results_dir.resolve()
        self.explicit_run = explicit_run.resolve() if explicit_run else None
        self._cache = {}
        self._lock = threading.RLock()

    def _candidate_dirs(self):
        if self.explicit_run is not None:
            return [self.explicit_run] if self.explicit_run.is_dir() else []
        if not self.results_dir.is_dir():
            return []
        return [path for path in self.results_dir.iterdir() if path.is_dir() and path.name.startswith("run-")]

    def list_runs(self):
        rows = []
        for path in self._candidate_dirs():
            manifest = _load_json(path / "run-manifest.json", {}) or {}
            rows.append({
                "name": path.name,
                "status": manifest.get("status", "waiting"),
                "success": manifest.get("success"),
                "modified": path.stat().st_mtime,
            })
        rows.sort(key=lambda row: row["modified"], reverse=True)
        return rows

    def get(self, name=None):
        with self._lock:
            candidates = self._candidate_dirs()
            if not candidates:
                return None
            if name:
                found = next((path for path in candidates if path.name == name), None)
                if found is None:
                    return None
                path = found
            else:
                path = max(candidates, key=lambda p: p.stat().st_mtime)
            key = str(path.resolve())
            if key not in self._cache:
                self._cache[key] = RunData(path)
            return self._cache[key]


class VisualizerHandler(SimpleHTTPRequestHandler):
    server_version = "CLIVEVisualizer/1.0"

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    @property
    def catalog(self):
        return self.server.catalog

    def log_message(self, fmt, *args):
        if not self.server.quiet:
            super().log_message(fmt, *args)

    def _json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _binary_file(self, path: Path, content_type="application/octet-stream"):
        try:
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _run_from_query(self, query):
        name = query.get("run", [None])[0]
        return self.catalog.get(name)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/runs":
                self._json({"runs": self.catalog.list_runs()})
                return

            if parsed.path.startswith("/api/"):
                run = self._run_from_query(query)
                if run is None:
                    self._json({"ready": False, "error": "No CLIVE run directory is available yet."}, HTTPStatus.NOT_FOUND)
                    return

                if parsed.path == "/api/meta":
                    self._json(run.meta())
                elif parsed.path == "/api/mobility.bin":
                    self._binary_file(run.mobility_binary_path())
                elif parsed.path == "/api/trace":
                    # Legacy/diagnostic JSON endpoint. Normal playback streams mobility.bin
                    # directly so Python does not materialize the full trace as nested JSON.
                    self._json(run.mobility_trace())
                elif parsed.path == "/api/frame":
                    self._json(run.frame(query.get("time_s", [0])[0]))
                elif parsed.path == "/api/events":
                    from_s = query.get("from_s", [None])[0]
                    to_s = query.get("to_s", [None])[0]
                    after_seq = query.get("after_seq", [None])[0]
                    limit = query.get("limit", [2000])[0]
                    self._json(run.events(
                        from_s=None if from_s is None else float(from_s),
                        to_s=None if to_s is None else float(to_s),
                        after_seq=None if after_seq is None else int(after_seq),
                        limit=int(limit),
                    ))
                elif parsed.path == "/api/metrics":
                    self._json(run.metrics())
                elif parsed.path == "/api/timeline":
                    self._json(run.timeline(query.get("bucket_s", [1.0])[0]))
                elif parsed.path == "/api/relationships":
                    self._json(run.relationships())
                else:
                    self._json({"error": "unknown API endpoint"}, HTTPStatus.NOT_FOUND)
                return
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/":
            self.path = "/index.html"
        else:
            self.path = parsed.path
        super().do_GET()


class VisualizerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, catalog, quiet=False):
        self.catalog = catalog
        self.quiet = quiet
        super().__init__(address, VisualizerHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="results", help="CLIVE results root containing run-* directories")
    parser.add_argument("--run", help="visualize one explicit run directory instead of following the results root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    explicit = Path(args.run) if args.run else None
    if explicit is not None and not explicit.is_dir():
        parser.error(f"run directory does not exist: {explicit}")

    catalog = RunCatalog(Path(args.results), explicit)
    server = VisualizerServer((args.host, args.port), catalog, quiet=args.quiet)
    url = f"http://{args.host}:{args.port}/"
    print(f"CLIVE Visualizer: {url}")
    if explicit:
        print(f"Run: {explicit.resolve()}")
    else:
        print(f"Watching results root: {Path(args.results).resolve()}")
    print("The visualizer is read-only and does not drive protocol or mobility ticks.")

    if args.open:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
