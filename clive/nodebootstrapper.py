"""Process-isolated CLIVE node runtime.

The node process is intentionally event-driven with respect to the protocol. Mobility ticks are
used only inside NodeSocket to look up the precomputed topology. The protocol is never
advanced by ``tick``/``step`` calls.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
import traceback
from collections import deque

from nodesocket import NodeSocket
from orchestrator import EmulationPlan, atomic_json
from protocol_interface import NullProtocol, ProtocolInterface


class EventLog:
    """List-like append interface that streams all events and retains a small tail."""

    def __init__(self, path=None, tail_size=256):
        self.tail = deque(maxlen=tail_size)
        self.stream = open(path, "x", encoding="utf-8", buffering=1) if path else None

    def append(self, event):
        if self.stream:
            self.stream.write(json.dumps(event, allow_nan=False) + "\n")
        self.tail.append(event)

    def __iter__(self):
        return iter(self.tail)

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None


class TimingOverrunError(RuntimeError):
    pass


class Node:
    """Runtime context exposed to a ProtocolInterface implementation.

    Protocol implementations may call ``sendto()``, ``elapsed()`` and ``is_active()`` and may
    create their own timer/worker threads. CLIVE only dispatches received datagrams through
    ``on_datagram``; it does not own protocol timers.
    """

    def __init__(self, spec, plan, node_socket, protocol_class=NullProtocol, *, event_path=None, stop_event=None):
        self.spec = spec
        self.plan = plan
        self.socket = node_socket
        self.stop_event = stop_event
        self.events = EventLog(event_path, plan.config.log_tail_records)
        self._closing = threading.Event()
        self.protocol = None
        try:
            if not isinstance(protocol_class, type) or not issubclass(protocol_class, ProtocolInterface):
                raise TypeError("protocol class must subclass ProtocolInterface")
            self.protocol = protocol_class(self)
        except BaseException:
            self.events.close()
            raise

    def elapsed(self):
        return self.socket.elapsed()

    def sendto(self, data, destination):
        return self.socket.sendto(data, destination)

    def is_active(self):
        return (
            not self._closing.is_set()
            and not (self.stop_event is not None and self.stop_event.is_set())
            and self.elapsed() < self.plan.config.duration_s
        )

    def _receive_available(self):
        deadline = time.monotonic() + self.plan.config.receive_budget_s
        count = 0
        for _ in range(self.plan.config.receive_batch_size):
            if not self.is_active() or time.monotonic() >= deadline:
                break
            result = self.socket.recvfrom()
            if result is None:
                break
            self.protocol.on_datagram(*result)
            count += 1
        return count

    def _wait_for_shared_epoch(self):
        elapsed = self.elapsed()
        if elapsed > self.plan.config.start_tolerance_s:
            raise TimingOverrunError(f"worker started {elapsed:.6f}s after shared epoch")
        while elapsed < 0:
            if self.stop_event is not None and self.stop_event.is_set():
                raise RuntimeError("experiment canceled before start")
            time.sleep(min(-elapsed, self.plan.config.io_poll_interval_s))
            elapsed = self.elapsed()
        if elapsed > self.plan.config.start_tolerance_s:
            raise TimingOverrunError(f"worker awoke {elapsed:.6f}s after shared epoch")

    def run(self):
        try:
            self._wait_for_shared_epoch()
            self.events.append({"type": "started", "time_s": self.elapsed(), "wall_time": time.time()})
            self.protocol.start()

            while self.is_active():
                self.socket.check_health()
                self._receive_available()
                if not self.is_active():
                    break
                wait = min(
                    self.plan.config.io_poll_interval_s,
                    max(0.0, self.plan.config.duration_s - self.elapsed()),
                )
                if wait > 0:
                    self.socket.wait_readable(wait)

            if self.stop_event is not None and self.stop_event.is_set() and self.elapsed() < self.plan.config.duration_s:
                raise RuntimeError("experiment canceled by supervisor")
            if self.elapsed() > self.plan.config.duration_s + self.plan.config.start_tolerance_s:
                raise TimingOverrunError("node runtime exceeded the experiment deadline")
        finally:
            self._closing.set()
            try:
                if self.protocol is not None:
                    self.protocol.close()
            finally:
                try:
                    self.socket.close()
                finally:
                    self.events.close()
        return self.socket.metrics.snapshot(), list(self.events)


def _load_protocol_class(path):
    if not path:
        return NullProtocol
    if ":" not in path:
        raise ValueError("protocol_class_path must be module:ClassName")
    module, name = path.split(":", 1)
    value = getattr(importlib.import_module(module), name)
    if not isinstance(value, type) or not issubclass(value, ProtocolInterface):
        raise TypeError("protocol must be a ProtocolInterface subclass")
    return value


def run_node(node_name, plan_path, results_dir, protocol_class_path=None, control=None, stop_event=None):
    node_socket = None
    error = None
    spec = None
    try:
        plan = EmulationPlan.load(plan_path)
        spec = plan.node_by_name(node_name)
        node_socket = NodeSocket(
            spec,
            plan,
            plan.config.seed,
            log_path=os.path.join(results_dir, f"{node_name}.packets.jsonl"),
        )
        node = Node(
            spec,
            plan,
            node_socket,
            _load_protocol_class(protocol_class_path),
            event_path=os.path.join(results_dir, f"{node_name}.events.jsonl"),
            stop_event=stop_event,
        )
        if control is not None:
            control.send({"status": "ready"})
            if not control.poll(max(60.0, plan.config.readiness_timeout_s * 2)):
                raise TimeoutError("supervisor did not arm the ready worker")
            message = control.recv()
            if message.get("action") != "start":
                raise RuntimeError("worker start canceled")
            node_socket.set_epoch(message["epoch_start"])
        node.run()
    except BaseException:
        error = traceback.format_exc()
        if control is not None:
            try:
                control.send({"status": "failed", "error": error})
            except (OSError, EOFError):
                pass
        raise
    finally:
        if node_socket is not None:
            try:
                node_socket.close()
            except BaseException:
                if error is None:
                    error = traceback.format_exc()
            snapshot = node_socket.metrics.snapshot()
            snapshot["success"] = error is None
            atomic_json(os.path.join(results_dir, f"{node_name}.metrics.json"), snapshot)
        if error and spec is not None:
            atomic_json(os.path.join(results_dir, f"{node_name}.error.json"), {"error": error})
        if control is not None:
            control.close()
