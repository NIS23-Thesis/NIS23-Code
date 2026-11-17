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
import bisect
import re
import glob
import heapq
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
        # Sparse on-disk time indexes are independent of the bounded live packet cache.
        # Historical playback must never go blank when a long run exceeds max_events.
        self._window_index = {}
        self._window_index_lock = threading.RLock()
        # Per-bucket, append-only packet counters. The old deque contains only the
        # latest 200k records and is not valid for whole-run traffic statistics.
        self._timeline_caches = {}
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
                "flame": dict(plan.flame_settings),  # scenario workload/config, not live ML accuracy
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

    def _normalize_event(self, raw, filename, stream_type, *, assign_seq=True):
        event_type = raw.get("event") or raw.get("type") or "unknown"
        node_name = raw.get("node_name") or filename.split(".", 1)[0]
        time_s = _safe_float(raw.get("time_s"), -1.0)
        source_node = None
        target_node = None

        if event_type in {"receive", "protocol_receive", "flame_sensor_received", "flame_model_received",
                          "flame_message_reassembled", "flame_unassociated_sensor_rejected",
                          "flame_invalid_model_peer", "flame_member_expired",
                          "flame_leave_observed", "flame_ch_discovered",
                          "flame_join_pending_expired"}:
            source_node = self._node_from_address(raw.get("source"))
            target_node = node_name
        else:
            source_node = node_name
            target_node = self._node_from_address(raw.get("target"))
            if target_node is None:
                destination = raw.get("destination")
                if isinstance(destination, (list, tuple)) and destination and destination[0] != "255.255.255.255":
                    target_node = self._node_from_address(destination)

        if event_type == "flame_aggregated" and raw.get("trigger_peer"):
            source_node = self._node_from_address(raw.get("trigger_peer")) or node_name
            target_node = node_name if source_node != node_name else None

        header = raw.get("common_header") or {}
        counters = raw.get("counters") if isinstance(raw.get("counters"), dict) else {}
        # The event's ordinal `seq` is NOT the protocol packet sequence number.
        # Packet events carry the latter in common_header; protocol events may
        # put it at the top level (or have no packet cause at all).
        packet_sequence = header.get("sequence_number", raw.get("sequence_number"))
        packet_type = header.get("packet_type", raw.get("packet_type"))
        detail_keys = (
            "message_id", "data_id", "update_id", "join_id", "fragment_count",
            "block_number", "last_block_number", "window_start", "window_end",
            "retry_count", "trigger_sequence_number", "attempt", "bytes",
            "accepted_bytes", "reason", "error", "role", "model_id",
            "send_buffer_size", "packet_id", "source", "destination", "target", "trigger_peer",
            "component", "stage", "exception_type", "acceptance_stage", "rejection_kind",
            "is_broadcast", "time_s", "packet_type", "sequence_number",
        )
        details = {key: raw[key] for key in detail_keys if key in raw}
        event = {
            "seq": self._next_seq if assign_seq else None,
            "time_s": time_s,
            "event": event_type,
            "stream": stream_type,
            "node": node_name,
            "source": source_node,
            "target": target_node,
            "packet_type": packet_type,
            "packet_id": raw.get("packet_id"),
            "message_id": raw.get("message_id"),
            "update_id": raw.get("update_id"),
            "fragment_count": raw.get("fragment_count"),
            "details": details,
            "is_broadcast": bool(raw.get("is_broadcast", False)),
            "sequence_number": packet_sequence,
            "trigger_sequence_number": raw.get("trigger_sequence_number"),
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
        if assign_seq:
            self._next_seq += 1
        return event

    def _refresh_events(self):
        patterns = (
            ("debug/packets/*.jsonl", "packet"),
            ("debug/events/*.jsonl", "protocol"),
            ("debug/connections/*.jsonl", "connection"),
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
                            # Compact connection logs mirror some detailed FLAME events.
                            # Use the detailed version when both exist to avoid replaying
                            # JOIN/LEAVE twice on the map and in the node inspector.
                            mirrored_connection = (stream_type == "connection" and
                                (self.run_dir / "debug" / "events" / path.name).is_file())
                            if not mirrored_connection and stream_type in ("protocol", "connection") and event["event"] in {
                                "flame_hello_sent", "flame_ch_discovered", "flame_join_sent", "flame_joined",
                                "flame_leave_sent", "flame_connection_lost", "flame_member_expired",
                                "flame_join_pending_expired",
                                "flame_leave_observed", "flame_sensor_received",
                                "flame_model_sent", "flame_model_received",
                                "flame_trained", "flame_aggregated",
                                "flame_transfer_complete", "flame_transfer_failed",
                                "flame_model_retry_scheduled", "flame_model_retry_exhausted",
                            } and event["source"] and (event["target"] or event["event"] in {"flame_hello_sent", "flame_trained", "flame_aggregated"}):
                                self._relationships.append({
                                    "seq": event["seq"], "time_s": event["time_s"],
                                    "action": event["event"], "node": event["node"],
                                    "source": event["source"], "target": event["target"],
                                    "reason": event["reason"], "packet_type": event["packet_type"],
                                    "sequence_number": event["sequence_number"],
                                    "message_id": event["message_id"], "update_id": event["update_id"],
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
            logging_enabled = logging_enabled or any(
                (self.run_dir / "debug" / "connections").glob("*.jsonl")
            ) or any((self.run_dir / "debug" / "events").glob("*.jsonl"))
            return {
                "available": logging_enabled,
                "events": sorted(self._relationships, key=lambda e: (e["time_s"], e["seq"])),
                "truncated": len(self._relationships) == self._relationships.maxlen,
                "source": "protocol or compact connection log" if logging_enabled else "none",
            }

    # One checkpoint per 256 lines per log: bounded memory and fast seek by time.
    _LOG_TIME = re.compile(rb'"time_s"\s*:\s*(-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)')

    def _event_log_sources(self):
        patterns = (("debug/packets/*.jsonl", "packet"),
                    ("debug/events/*.jsonl", "protocol"),
                    ("debug/connections/*.jsonl", "connection"),
                    ("*.packets.jsonl", "packet"),
                    ("*.events.jsonl", "protocol"))
        sources = sorted(((path, kind)
            for pattern, kind in patterns for path in self.run_dir.glob(pattern)),
            key=lambda item: str(item[0]))
        # Connection logger duplicates specific protocol records if full logging is enabled.
        return [(path, kind) for path, kind in sources if not (
            kind == "connection" and (self.run_dir / "debug/events" / path.name).is_file())]

    def _update_window_index(self, path):
        """Index appended, newline-terminated records without JSON-parsing every packet."""
        key = str(path)
        entry = self._window_index.setdefault(key, {"offset": 0, "line_count": 0, "marks": []})
        try:
            size = path.stat().st_size
            if size < entry["offset"]:
                entry.update(offset=0, line_count=0, marks=[])
            if size == entry["offset"]:
                return entry
            with path.open('rb') as stream:
                stream.seek(entry["offset"])
                while True:
                    start = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        break
                    entry["line_count"] += 1
                    if (entry["line_count"] - 1) % 256 == 0:
                        match = self._LOG_TIME.search(line)
                        if match:
                            try:
                                timestamp = float(match.group(1))
                            except ValueError:
                                timestamp = -1
                            entry["marks"].append((timestamp, start))
                entry["offset"] = stream.tell()
        except OSError:
            pass
        return entry

    # The stream name describes the JSONL file, not the subsystem that emitted
    # the event. `flame_started` and adapter retries are CLIVE-owned events.
    _CLIVE_ADAPTER_EVENTS = frozenset({
        'flame_sample_collected', 'flame_sample_buffered', 'flame_offline_sample_dropped',
        'flame_sample_source_exhausted', 'flame_model_retry_scheduled',
        'flame_model_retry_superseded', 'flame_model_retry_exhausted',
        'flame_radio_backpressure_drop', 'flame_protocol_callback_slow',
        'flame_model_revision_limit_reached', 'flame_learning_evaluation_error',
        'flame_invalid_mnist_sample_rejected', 'flame_model_rejected_revision_limit',
        'flame_fl_error',
    })
    _FLAME_ACTION_EVENTS = frozenset({
        'flame_hello_sent', 'flame_join_sent', 'flame_joined', 'flame_ch_discovered',
        'flame_leave_observed', 'flame_leave_sent', 'flame_sensor_queued',
        'flame_sensor_received', 'flame_model_sent', 'flame_model_received',
        'flame_transfer_complete', 'flame_transfer_failed',
        'flame_unassociated_sensor_rejected', 'flame_invalid_model_peer',
        'flame_datagram_error', 'flame_error', 'flame_hello_queue_dropped',
        'flame_join_queue_dropped', 'flame_leave_queue_dropped',
    })
    _FLAME_INTERNAL_EVENTS = frozenset({
        'flame_connection_lost', 'flame_member_expired', 'flame_join_pending_expired',
        'flame_message_reassembled', 'flame_trained', 'flame_aggregated',
        'flame_sensor_enqueue_blocked', 'flame_model_rejected_or_duplicate',
    })
    _FLAME_BEHAVIOR_EVENTS = _FLAME_ACTION_EVENTS | _FLAME_INTERNAL_EVENTS
    _FAILURE_WORDS = ('failed', 'error', 'rejected', 'dropped', 'retry_exhausted',
                      'queue_dropped', 'enqueue_blocked')

    @staticmethod
    def _is_failure_event(event):
        name = str(event.get('event') or '')
        if name == 'flame_model_rejected_or_duplicate' and \
           ((event.get('details') or {}).get('rejection_kind') or event.get('rejection_kind')) == 'duplicate':
            return False
        return (name in ('delivery_drop', 'local_drop', 'socket_error',
                         'flame_radio_backpressure_drop') or
                any(word in name for word in RunData._FAILURE_WORDS))

    _PACKET_NAMES = ('HELLO', 'LEAVE', 'SENSOR_DATA', 'SENSOR_DATA_ACK',
                     'MODEL_UPDATE', 'MODEL_UPDATE_ACK', 'JOIN', 'JOIN_ACCEPT')
    _RELATED_PACKET = {
        'flame_hello_sent': 0, 'flame_ch_discovered': 0,
        'flame_join_sent': 6, 'flame_joined': 7,
        'flame_leave_sent': 1, 'flame_leave_observed': 1,
        'flame_sensor_queued': 2, 'flame_sensor_received': 2,
        'flame_model_sent': 4, 'flame_model_received': 4,
        'flame_invalid_model_peer': 4, 'flame_unassociated_sensor_rejected': 2,
    }

    @staticmethod
    def _preview_group(event):
        """What this record proves, NOT merely its original JSONL filename.

        FLAME Packet is reserved for a decoded real TX/RX from NodeSocket.
        A `flame_hello_sent` adapter observation is FLAME Action: it only
        establishes that the HELLO was submitted to the communication path.
        """
        name = str(event.get('event') or '')
        if event.get('stream') == 'packet':
            packet_id = event.get('packet_type')
            if (name in ('transmit', 'receive') and type(packet_id) is int
                    and 0 <= packet_id < len(RunData._PACKET_NAMES)):
                return 'flame-packet'
            return 'network'
        if name in ('started', 'flame_started') or name in RunData._CLIVE_ADAPTER_EVENTS:
            return 'clive'
        if event.get('stream') in ('protocol', 'connection'):
            if name in RunData._FLAME_ACTION_EVENTS:
                return 'flame-action'
            if name in RunData._FLAME_INTERNAL_EVENTS:
                return 'flame-internal'
        return 'clive'

    @staticmethod
    def _is_flame_group(group):
        return group in ('flame-packet', 'flame-action', 'flame-internal')

    @staticmethod
    def _preview_key(event):
        return (RunData._preview_group(event) in ('flame-action', 'flame-internal') and
                event['event'] not in ('flame_hello_sent', 'flame_message_reassembled',
                                       'flame_sensor_queued'))

    @staticmethod
    def _matches_event_filters(event, category='all', search='', node='', packet='', outcome='all'):
        """Shared predicate for indexed timeline, memory preview and archive.

        `packet` selects a decoded wire type or a clearly related adapter action.
        It never turns a submitted action into an observed packet emission.
        """
        name = str(event.get('event') or '')
        group = RunData._preview_group(event)
        if category == 'highlights':
            if not RunData._panel_highlight(event):
                return False
        elif category == 'flame':
            if not RunData._is_flame_group(group):
                return False
        elif category == 'protocol':  # legacy alias: logged FLAME actions/internal, not NodeSocket TX/RX
            if group not in ('flame-action', 'flame-internal'):
                return False
        elif category in ('flame-packet', 'flame-action', 'flame-internal', 'clive'):
            if group != category:
                return False
        elif category == 'network':
            if event.get('stream') != 'packet' or name not in (
                    'transmit', 'receive', 'delivery_drop', 'local_drop', 'socket_error'):
                return False
        elif category in ('drop', 'failures'):
            if not RunData._is_failure_event(event):
                return False
        elif category == 'activity':
            if not RunData._panel_activity(event):
                return False
        elif category == 'key':
            if not RunData._preview_key(event):
                return False
        elif category == 'packets':
            if not RunData._panel_packet(event):
                return False
        elif category in ('transmit', 'receive'):
            if event.get('stream') != 'packet' or name != category:
                return False
        elif category == 'traffic':
            if name not in ('receive', 'delivery_drop') or not event.get('source') or not event.get('target'):
                return False
        elif category == 'internal':
            if group not in ('clive', 'network'):
                return False
        elif category not in ('all', 'packet'):
            return False
        if category == 'packet' and event.get('stream') != 'packet':
            return False

        if node and node not in (str(event.get('node') or ''), str(event.get('source') or ''), str(event.get('target') or '')):
            return False
        if packet and packet != 'all':
            recorded = event.get('packet_type')
            effective = (recorded if recorded is not None else
                         RunData._RELATED_PACKET.get(name))
            if effective is None or not (0 <= effective < len(RunData._PACKET_NAMES)):
                return False
            if RunData._PACKET_NAMES[effective] != packet:
                return False
        if outcome == 'failure' and not RunData._is_failure_event(event):
            return False
        if outcome == 'nonfailure' and RunData._is_failure_event(event):
            return False
        if search and search not in RunData._search_text(event):
            return False
        return True

    @staticmethod
    def _search_text(event):
        """Searchable record with wire packet names, not just numeric type IDs."""
        packet_names = ('HELLO', 'LEAVE', 'SENSOR_DATA', 'SENSOR_DATA_ACK',
                        'MODEL_UPDATE', 'MODEL_UPDATE_ACK', 'JOIN', 'JOIN_ACCEPT')
        packet_type = event.get('packet_type')
        label = (packet_names[packet_type]
                 if type(packet_type) is int and 0 <= packet_type < len(packet_names)
                 else '')
        # A recorded but invalid type cannot be overwritten by an inferred one.
        if packet_type is None:
            related = RunData._RELATED_PACKET.get(str(event.get('event') or ''))
            label = (packet_names[related] if related is not None else '')
        return (json.dumps(event, ensure_ascii=False, default=str) + ' ' + label).lower()

    @staticmethod
    def _panel_activity(event):
        """FLAME actions and internal state, not NodeSocket packet TX/RX.

        FLAME Packet is a separate evidence category. Including it here makes
        the legacy activity filter disagree with the JavaScript classifier and
        doubles up real packet traffic in a protocol-action view.
        """
        if RunData._preview_group(event) not in ('flame-action', 'flame-internal'):
            return False
        return event['event'] not in ('flame_message_reassembled', 'flame_sensor_queued')

    _HIGHLIGHT_EVENTS = frozenset({
        'flame_hello_sent', 'flame_ch_discovered', 'flame_join_sent',
        'flame_joined', 'flame_sensor_received', 'flame_trained',
        'flame_model_sent', 'flame_model_received', 'flame_aggregated',
        'flame_transfer_complete', 'flame_transfer_failed',
        'flame_connection_lost', 'flame_member_expired',
        'flame_leave_observed', 'flame_leave_sent',
    })

    @staticmethod
    def _panel_highlight(event):
        """Panel timeline: meaningful FLAME state changes, not routine diagnostics.

        Packet loss and socket diagnostics are retained under Network/Failures,
        not duplicated thousands of times in the default panel timeline.
        """
        return (RunData._preview_group(event) in ('flame-action', 'flame-internal') and
                (event.get('event') in RunData._HIGHLIGHT_EVENTS or
                 RunData._is_failure_event(event)))

    @staticmethod
    def _panel_packet(event):
        """Actual wire packet stages only; excludes queued/scheduled operations."""
        return event['stream'] == 'packet' and event['event'] in (
            'transmit', 'local_drop')

    @staticmethod
    def _panel_network(event):
        """Physical/transport outcome view, including logged socket failures."""
        return event['stream'] == 'packet' and event['event'] in (
            'transmit', 'receive', 'delivery_drop', 'local_drop', 'socket_error')

    def _window_events(self, from_s, to_s, limit, category='all', search='',
                       order='latest', node='', packet='', outcome='all'):
        """Select the earliest or latest K matching on-disk events globally.

        Filtering occurs before limit. Tie-break by file and byte offset, not
        by an unstable worker/ingestion order. Does not mutate stored records.
        """
        if order not in ('asc', 'desc', 'latest'):
            raise ValueError('Unknown event order')
        start = max(0.0, float(from_s or 0))
        end = float('inf') if to_s is None else max(start, float(to_s))
        limit = max(1, min(int(limit), 2000))
        query = str(search or '').strip().lower()[:120]
        node = str(node or '').strip()[:80]
        packet = str(packet or 'all').strip().upper()
        if packet == 'ALL':
            packet = 'all'
        outcome = str(outcome or 'all')
        if packet and packet not in ('all', *self._PACKET_NAMES):
            raise ValueError('Unknown packet family')
        if outcome not in ('all', 'failure', 'nonfailure'):
            raise ValueError('Unknown event outcome')
        kept = []
        matching = 0
        source_count = 0
        with self._window_index_lock:
            for file_index, (path, kind) in enumerate(self._event_log_sources()):
                index = self._update_window_index(path)
                marks = index['marks']
                timestamps = [pair[0] for pair in marks]
                index_pos = bisect.bisect_right(timestamps, start) - 1
                offset = marks[index_pos][1] if index_pos >= 0 else 0
                source_count += 1
                try:
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        while True:
                            byte_position = stream.tell()
                            line = stream.readline()
                            if not line or not line.endswith(b'\n'):
                                break
                            time_match = self._LOG_TIME.search(line)
                            if not time_match:
                                continue
                            timestamp = float(time_match.group(1))
                            if timestamp < start:
                                continue
                            if timestamp > end:
                                break
                            try:
                                raw = json.loads(line)
                            except (ValueError, UnicodeDecodeError):
                                continue
                            event = self._normalize_event(raw, path.name, kind, assign_seq=False)
                            event['seq'] = f'{path.name}@{byte_position}'
                            if not self._matches_event_filters(event, category, query, node, packet, outcome):
                                continue
                            matching += 1
                            key = (timestamp, file_index, byte_position)
                            # For descending keep the largest K. For ascending
                            # keep smallest K with a negated max-key heap.
                            heap_key = key if order in ('desc', 'latest') else tuple(-v for v in key)
                            item = (heap_key, event)
                            if len(kept) < limit:
                                heapq.heappush(kept, item)
                            elif heap_key > kept[0][0]:
                                heapq.heapreplace(kept, item)
                except OSError:
                    continue
        selected = [event for _, event in sorted(kept, key=lambda row:row[0], reverse=(order != 'latest'))]
        return {'events':selected, 'matching_count':matching,
                'hidden_count':max(0, matching-len(selected)),
                'retained_events':None, 'truncated':matching > len(selected),
                'source_count':source_count, 'source':'indexed recorded JSONL',
                'complete_recorded_history':'/api/events/archive'}

    def events(self, from_s=None, to_s=None, after_seq=None, limit=2000,
               category="all", search="", order="latest", node="", packet="", outcome="all"):
        """Latest matching events, returned in chronological order.

        Node processes write their own JSONL files, so append/record order is NOT
        chronological order. Select the latest K with a bounded heap instead of
        sorting all retained events (up to 200,000) for each browser refresh.
        """
        if (from_s is not None or to_s is not None) and after_seq is None \
                and hasattr(self, '_window_index_lock'):
            return self._window_events(from_s, to_s, limit, category, search, order, node, packet, outcome)
        with self._lock:
            self._refresh_events()
            limit = max(1, min(int(limit), 10_000))

            def matching_events():
                for event in self._events:
                    if after_seq is not None and event["seq"] <= after_seq:
                        continue
                    if from_s is not None and event["time_s"] < from_s:
                        continue
                    if to_s is not None and event["time_s"] > to_s:
                        continue
                    if not self._matches_event_filters(event, category,
                            str(search or '').strip().lower(), str(node or '').strip(),
                            ('all' if str(packet or '').lower() == 'all' else str(packet or '').upper()), outcome):
                        continue
                    yield event

            by_time = lambda event: (event["time_s"], event["seq"])
            recent = (heapq.nsmallest(limit, matching_events(), key=by_time)
                      if order == 'asc' else heapq.nlargest(limit, matching_events(), key=by_time))
            recent.sort(key=by_time, reverse=(order == 'desc'))
            return {
                "events": recent,
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
                "flame_outcome": aggregate.get("flame_outcome") if isinstance(aggregate, dict) else None,
                "protocol_success": aggregate.get("protocol_success") if isinstance(aggregate, dict) else None,
                "execution_success": aggregate.get("execution_success") if isinstance(aggregate, dict) else None,
                "recipient_delivery": aggregate.get("recipient_delivery") if isinstance(aggregate, dict) else None,
                "measurement_window": aggregate.get("measurement_window") if isinstance(aggregate, dict) else None,
                "energy_model": aggregate.get("energy_model") if isinstance(aggregate, dict) else None,
                "complete": aggregate_complete,
                "source": "aggregate" if aggregate_complete else ("events" if self._events else "none"),
                "totals": totals,
                "nodes": sorted(per_node.values(), key=lambda row: row["node_name"]),
            }

    def event_archive(self, *, offset=0, limit=100, from_s=0, to_s=None,
                      category="all", search="", order="asc", node="", packet="", outcome="all"):
        """Paginate recorded on-disk events, not the capped live preview cache.

        This page is diagnostics-only; disk scans are intentionally on demand.
        It never claims events that were not logged in the first place.
        """
        from itertools import count
        offset = max(0, min(int(offset), 1_000_000_000))
        limit = max(1, min(int(limit), 500))
        lower = max(0.0, _safe_float(from_s))
        upper = float('inf') if to_s is None else max(lower, _safe_float(to_s))
        search = str(search or "").lower().strip()[:120]
        if category not in {'all', 'protocol', 'flame', 'flame-packet', 'flame-action',
                            'flame-internal', 'clive', 'packet', 'drop', 'key',
                            'activity', 'packets', 'network', 'highlights'}:
            raise ValueError('Invalid archive category')
        if order not in ('asc', 'desc'):
            raise ValueError('Invalid archive sort order')
        node = str(node or '').strip()[:80]
        packet = str(packet or 'all').strip().upper()
        outcome = str(outcome or 'all')
        if packet == 'ALL':
            packet = 'all'
        if packet not in ('all', *self._PACKET_NAMES):
            raise ValueError('Invalid packet filter')
        if outcome not in ('all', 'failure', 'nonfailure'):
            raise ValueError('Invalid outcome filter')
        patterns = (("debug/packets/*.jsonl", "packet"),
                    ("debug/events/*.jsonl", "protocol"),
                    ("debug/connections/*.jsonl", "connection"),
                    ("*.packets.jsonl", "packet"),
                    ("*.events.jsonl", "protocol"))
        paths = sorted({str(path): (path, kind)
                        for pattern,kind in patterns
                        for path in self.run_dir.glob(pattern)}.values(),
                       key=lambda row: str(row[0]))
        # Compact connection logs duplicate selected protocol records when verbose
        # protocol logging is also enabled; prefer the richer original event log.
        paths = [(path, kind) for path, kind in paths if not (
            kind == 'connection' and
            (self.run_dir / 'debug' / 'events' / path.name).exists())]
        streams, heap = [], []

        def forward_lines(path):
            with path.open(encoding='utf8') as handle:
                yield from enumerate(handle, 1)

        def reverse_lines(path):
            """Iterate JSONL backwards in bounded blocks, no full-log in-memory sort.

            Only complete records are emitted; skip the optional final newline.
            The reverse ordinal is stable for pagination within one run.
            """
            with path.open('rb') as handle:
                handle.seek(0, 2)
                remaining = handle.tell()
                carry = b''
                ordinal = 0
                while remaining:
                    chunk_size = min(65536, remaining)
                    remaining -= chunk_size
                    handle.seek(remaining)
                    blocks = (handle.read(chunk_size) + carry).split(b'\n')
                    carry = blocks[0]
                    for raw_line in reversed(blocks[1:]):
                        if raw_line:
                            ordinal += 1
                            yield ordinal, raw_line.decode('utf-8', errors='replace')
                if carry:
                    ordinal += 1
                    yield ordinal, carry.decode('utf-8', errors='replace')

        def next_item(i):
            stream, path, kind = streams[i]
            for num, line in stream:
                try:
                    raw = json.loads(line)
                except (ValueError, TypeError):
                    continue
                ts = _safe_float(raw.get('time_s'), -1)
                if order == 'asc':
                    if ts < lower:
                        continue
                    if ts > upper:
                        return None
                else:
                    if ts > upper:
                        continue
                    if ts < lower:
                        return None
                event = self._normalize_event(raw, path.name, kind, assign_seq=False)
                event['archive_id'] = f'{path.name}:{num}:{order}'
                # Reverse the file-order tie-break as well as time, otherwise
                # equal-timestamp events can jump between archive pages.
                key = (ts, i) if order == 'asc' else (-ts, -i)
                return key, num, i, event
            return None

        try:
            for path, kind in paths:
                try:
                    stream = forward_lines(path) if order == 'asc' else reverse_lines(path)
                    streams.append((stream, path, kind))
                except OSError:
                    continue
            for i in range(len(streams)):
                item = next_item(i)
                if item:
                    heapq.heappush(heap, item)
            found = 0
            rows = []
            more = False
            while heap:
                _, _, i, ev = heapq.heappop(heap)
                new = next_item(i)
                if new:
                    heapq.heappush(heap, new)
                if not self._matches_event_filters(ev, category, search, node, packet, outcome):
                    continue
                if found >= offset + limit:
                    more = True
                    break
                if found >= offset:
                    rows.append(ev)
                found += 1
            return {'run': self.name, 'events': rows, 'offset': offset, 'limit': limit,
                    'order': order, 'has_more': more, 'next_offset': offset + len(rows),
                    'available': bool(paths),
                    'source': 'Recorded JSONL only; events not originally logged cannot be reconstructed.',
                    'logging': self._plan.logging_config.to_dict() if self._plan else {}}
        finally:
            for stream, _, _ in streams:
                try:
                    stream.close()
                except (AttributeError, OSError):
                    pass

    def learning(self):
        """Return observed per-CH quality; never invent intermediate accuracy."""
        with self._lock:
            self._refresh_plan()
            self._refresh_manifest()
            nodes = []
            for spec in (self._plan.nodes if self._plan is not None else []):
                if int(spec.role) != 1:
                    continue
                points = []
                log_path = self.run_dir / "flame" / f"{spec.name}-learning.jsonl"
                if log_path.is_file():
                    try:
                        with log_path.open(encoding="utf8") as stream:
                            for line in stream:
                                try:
                                    entry = json.loads(line)
                                except (json.JSONDecodeError, ValueError):
                                    continue
                                if isinstance(entry, dict) and entry.get("measurement") in ("sampled_test_accuracy", "revision_event"):
                                    points.append(entry)
                    except OSError:
                        pass
                final = _load_json(self.run_dir / "flame" / f"{spec.name}.json", {}) or {}
                nodes.append({"name": spec.name, "points": points,
                              "final": final.get("model_evaluation"),
                              "counters": final.get("counters", {}),
                              "model_revision": final.get("model_update_id"),
                              "received_digit_distribution": final.get("received_digit_distribution"),
                              "history_enabled": bool(final.get("learning_history", {}).get("enabled"))})
            return {"run": self.name, "status": (self._manifest or {}).get("status", "waiting"),
                    "duration_s": self._plan.config.duration_s if self._plan else 0,
                    "nodes": nodes,
                    "note": "Each point is held-out MNIST classification quality for one model revision, not training accuracy or convergence proof. Softmax example confidence is not calibrated probability of correctness."}

    def timeline(self, bucket_s=1.0):
        """Exact logged packet counts by time bucket, including evicted events.

        The live event cache is intentionally bounded, so graphing its counts
        silently loses early transmissions in high-volume runs. Track read offsets
        for each JSONL file and incrementally count appended packet records instead.
        """
        with self._lock:
            bucket_s = max(0.1, min(_safe_float(bucket_s, 1.0), 60.0))
            cache = self._timeline_caches.get(bucket_s)
            paths = [path for path, kind in self._event_log_sources() if kind == "packet"]
            # A run can be restarted in the same folder or its logs rotated.
            # In that case rebuild counters rather than counting stale history.
            if cache is not None:
                current_paths = {str(path) for path in paths}
                if set(cache["offsets"]) - current_paths:
                    cache = None
                else:
                    for path in paths:
                        try:
                            if path.stat().st_size < cache["offsets"].get(str(path), 0):
                                cache = None
                                break
                        except OSError:
                            cache = None
                            break
            if cache is None:
                cache = {"offsets": {}, "buckets": defaultdict(
                    lambda: {"sent": 0, "received": 0, "dropped": 0}),
                         "totals": {"sent": 0, "received": 0, "dropped": 0},
                         "max_time": 0.0}
                self._timeline_caches[bucket_s] = cache

            for path in paths:
                key = str(path)
                try:
                    with path.open("rb") as stream:
                        stream.seek(cache["offsets"].get(key, 0))
                        while True:
                            start = stream.tell()
                            line = stream.readline()
                            if not line or not line.endswith(b"\n"):
                                stream.seek(start)
                                break
                            # Avoid JSON-decoding internal queue steps. A fast
                            # substring check keeps the high-volume graphs cheap.
                            if not any(name in line for name in
                                       (b'"transmit"', b'"receive"',
                                        b'"delivery_drop"', b'"local_drop"')):
                                continue
                            try:
                                event = json.loads(line)
                            except (ValueError, UnicodeDecodeError):
                                continue
                            name = event.get("event")
                            field = ({"transmit": "sent", "receive": "received",
                                      "delivery_drop": "dropped",
                                      "local_drop": "dropped"}).get(name)
                            time_s = _safe_float(event.get("time_s"), -1.0)
                            if field is None or time_s < 0:
                                continue
                            index = int(time_s / bucket_s)
                            cache["buckets"][index][field] += 1
                            cache["totals"][field] += 1
                            cache["max_time"] = max(cache["max_time"], time_s)
                        cache["offsets"][key] = stream.tell()
                except OSError:
                    continue

            duration_s = self._plan.config.duration_s if self._plan else 0.0
            # A duration of 6.0 seconds covers [0, 6), not a phantom
            # [6, 7) bucket. Logged packets at/after the nominal endpoint
            # must still extend the timeline rather than disappearing.
            nominal_last = math.ceil(duration_s / bucket_s) - 1
            max_index = max(0, nominal_last, max(cache["buckets"], default=-1))
            rows = [{"time_s": i * bucket_s,
                     **cache["buckets"].get(i, {"sent": 0, "received": 0, "dropped": 0})}
                    for i in range(max_index + 1)]
            totals = dict(cache["totals"])
            return {"bucket_s": bucket_s, "rows": rows, "totals": totals,
                    "has_packet_traffic": any(totals.values()),
                    "source": "all recorded packet logs (incremental)"}



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
            if parsed.path == "/api/experiment-studio":
                self._json({"capacity_editor_url": self.server.capacity_editor_url})
                return

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
                        category=query.get("category", ["all"])[0],
                        search=query.get("search", [""])[0],
                        order=query.get("order", ["latest"])[0],
                        node=query.get("node", [""])[0],
                        packet=query.get("packet", ["all"])[0],
                        outcome=query.get("outcome", ["all"])[0],
                    ))
                elif parsed.path == "/api/metrics":
                    self._json(run.metrics())
                elif parsed.path == "/api/timeline":
                    self._json(run.timeline(query.get("bucket_s", [1.0])[0]))
                elif parsed.path == "/api/relationships":
                    self._json(run.relationships())
                elif parsed.path == "/api/learning":
                    self._json(run.learning())
                elif parsed.path == "/api/events/archive":
                    self._json(run.event_archive(
                        offset=query.get("offset", [0])[0], limit=query.get("limit", [100])[0],
                        from_s=query.get("from_s", [0])[0],
                        to_s=query.get("to_s", [None])[0],
                        category=query.get("category", ["all"])[0],
                        search=query.get("search", [""])[0],
                        order=query.get("order", ["asc"])[0],
                        node=query.get("node", [""])[0],
                        packet=query.get("packet", ["all"])[0],
                        outcome=query.get("outcome", ["all"])[0]))
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

    def __init__(self, address, catalog, quiet=False, capacity_editor_url=None):
        self.catalog = catalog
        self.capacity_editor_url = capacity_editor_url
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
