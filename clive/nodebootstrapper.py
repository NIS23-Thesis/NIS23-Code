"""Process-isolated node runtime with bounded I/O and explicit timing failures."""
from __future__ import annotations
import importlib
import json
import os
import time
import traceback
from collections import deque

from nodesocket import BROADCAST_ADDRESS, NodeSocket
from orchestrator import EmulationPlan, atomic_json


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


class Protocol:
    def __init__(self, node):
        self.node = node
        self.events = node.events

    def step(self, tick):
        pass

    def receive(self, data, source):
        pass

    def close(self):
        pass


class SimpleProtocol(Protocol):
    """Dummy traffic only. Type 15 is not a FLAME packet assignment."""
    DUMMY_PACKET_TYPE = 15

    def __init__(self, node):
        super().__init__(node)
        self.sequence, self.next_send_s = 0, 0.0

    def step(self, tick):
        if tick * self.node.plan.mobility_config.tick_interval + 1e-9 < self.next_send_s:
            return
        data = bytes([self.DUMMY_PACKET_TYPE, int(self.node.spec.role) << 7])
        data += self.sequence.to_bytes(2, "big")
        data += f"DUMMY FROM {self.node.spec.name} AT TICK {tick}".encode()
        self.events.append(dict(type="send", tick=tick, time_s=self.node.socket.elapsed(),
                                accepted_bytes=self.node.sendto(data, BROADCAST_ADDRESS)))
        self.sequence = (self.sequence + 1) & 65535
        self.next_send_s += 1

    def receive(self, data, source):
        self.events.append(dict(type="receive", tick=self.node.socket.current_tick(),
                                time_s=self.node.socket.elapsed(), source=list(source),
                                data=data[4:].decode(errors="replace")))


class TimingOverrunError(RuntimeError):
    pass


class Node:
    def __init__(self, spec, plan, node_socket, protocol_class=SimpleProtocol, *, event_path=None, stop_event=None):
        self.spec, self.plan, self.socket = spec, plan, node_socket
        self.stop_event = stop_event
        self.events = EventLog(event_path, plan.config.log_tail_records)
        self.protocol = None
        try:
            self.protocol = protocol_class(self)
            for method in ("step", "receive", "close"):
                if not callable(getattr(self.protocol, method, None)):
                    raise TypeError(f"protocol lacks callable {method}")
        except BaseException:
            self.events.close()
            raise

    def sendto(self, data, destination):
        return self.socket.sendto(data, destination)

    def _stopped(self):
        return self.stop_event is not None and self.stop_event.is_set()

    def _receive_available(self):
        deadline = time.monotonic() + self.plan.config.receive_budget_s
        count = 0
        for _ in range(self.plan.config.receive_batch_size):
            if self._stopped() or self.socket.elapsed() >= self.plan.config.duration_s or time.monotonic() >= deadline:
                break
            result = self.socket.recvfrom()
            if result is None:
                break
            self.protocol.receive(*result)
            count += 1
        return count

    def _wait_for_shared_epoch(self):
        elapsed = self.socket.elapsed()
        if elapsed >= self.plan.mobility_config.tick_interval:
            raise TimingOverrunError("worker missed the shared starting quantum")
        while elapsed < 0:
            if self._stopped():
                raise RuntimeError("experiment canceled before start")
            time.sleep(min(-elapsed, .005))
            elapsed = self.socket.elapsed()

    def run(self):
        try:
            self._wait_for_shared_epoch()
            next_tick = 0
            quantum = self.plan.mobility_config.tick_interval
            while not self._stopped():
                self.socket.check_health()
                elapsed = self.socket.elapsed()
                if elapsed >= self.plan.config.duration_s:
                    break
                current = int(elapsed / quantum)
                if current > next_tick:
                    raise TimingOverrunError(f"missed protocol tick {next_tick}; current tick is {current}")
                if current == next_tick:
                    self.protocol.step(current)
                    next_tick += 1
                elapsed = self.socket.elapsed()
                if elapsed >= self.plan.config.duration_s:
                    break
                self._receive_available()
                elapsed = self.socket.elapsed()
                wait = min(self.plan.config.duration_s - elapsed, next_tick * quantum - elapsed,
                           self.plan.config.io_poll_interval_s)
                if wait > 0:
                    self.socket.wait_readable(wait)
            if self._stopped():
                raise RuntimeError("experiment canceled by supervisor")
            # Detect a blocking final callback instead of silently reporting success.
            if self.socket.elapsed() > self.plan.config.duration_s + quantum:
                raise TimingOverrunError("protocol callback exceeded the experiment deadline")
        finally:
            # End networking first: a protocol close callback cannot emit late packets.
            try:
                self.socket.close()
            finally:
                try:
                    self.protocol.close()
                finally:
                    self.events.close()
        return self.socket.metrics.snapshot(), list(self.events)


def _load_protocol_class(path):
    if not path:
        return SimpleProtocol
    if ":" not in path:
        raise ValueError("protocol_class_path must be module:ClassName")
    module, name = path.split(":", 1)
    value = getattr(importlib.import_module(module), name)
    if not isinstance(value, type):
        raise TypeError("protocol must be a class")
    return value


def run_node(node_name, plan_path, results_dir, protocol_class_path=None, control=None, stop_event=None):
    node_socket = None
    error = None
    spec = None
    try:
        plan = EmulationPlan.load(plan_path)
        spec = plan.node_by_name(node_name)
        node_socket = NodeSocket(spec, plan, plan.config.seed,
                                 log_path=os.path.join(results_dir, f"{node_name}.packets.jsonl"))
        node = Node(spec, plan, node_socket, _load_protocol_class(protocol_class_path),
                    event_path=os.path.join(results_dir, f"{node_name}.events.jsonl"), stop_event=stop_event)
        if control is not None:
            control.send({"status": "ready"})
            if not control.poll(plan.config.readiness_timeout_s):
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
