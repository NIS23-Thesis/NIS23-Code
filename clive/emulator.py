"""Supervised process launcher. Each run has a fresh directory; old runs survive."""
from __future__ import annotations
import multiprocessing as mp
import os
import tempfile
import time
import traceback

from nodebootstrapper import run_node
from orchestrator import EmulationPlan, atomic_json


class Emulator:
    def __init__(self, plan_path, results_dir, *, local_underlay_ip=None, protocol_class_path=None):
        self.results_dir = os.path.abspath(results_dir)
        self.plan = EmulationPlan.load(os.path.abspath(plan_path))
        self.local_underlay_ip = local_underlay_ip
        self.protocol_class_path = protocol_class_path
        self.run_dir = None

    def _assigned_nodes(self):
        if self.plan.config.mode == "localhost":
            return list(self.plan.nodes)
        address = self.local_underlay_ip or os.getenv("CLIVE_UNDERLAY_IP")
        nodes = [node for node in self.plan.nodes if node.underlay_ip == address]
        if not nodes:
            raise ValueError("LAN mode requires the assigned local_underlay_ip or CLIVE_UNDERLAY_IP")
        self.local_underlay_ip = address
        return nodes

    def run(self, *, epoch_callback=None):
        self.plan.validate()
        nodes = self._assigned_nodes()
        if epoch_callback is None and time.time() >= self.plan.epoch_start:
            raise RuntimeError("epoch_start has passed; prepare a fresh plan")
        os.makedirs(self.results_dir, exist_ok=True)
        self.run_dir = tempfile.mkdtemp(prefix="run-", dir=self.results_dir)
        local_plan_path = os.path.join(self.run_dir, "plan.json")
        self.plan.save(local_plan_path)
        self.plan.save_trace(os.path.join(self.run_dir, "mobility.tsv"))
        manifest_path = os.path.join(self.run_dir, "run-manifest.json")
        manifest = dict(success=False, status="starting", mode=self.plan.config.mode,
                        local_underlay_ip=self.local_underlay_ip, nodes=[n.name for n in nodes],
                        started_at=time.time(), plan_path=local_plan_path, failures=[])
        atomic_json(manifest_path, manifest)
        # spawn avoids inheriting other hosts' client sockets and parent threads.
        context = mp.get_context("spawn")
        stop = context.Event()
        workers = []
        try:
            for node in nodes:
                parent, child = context.Pipe()
                process = context.Process(name=f"CLIVE-{node.name}", target=run_node,
                    args=(node.name, local_plan_path, self.run_dir, self.protocol_class_path, child, stop))
                try:
                    process.start()
                except BaseException:
                    parent.close()
                    child.close()
                    raise
                child.close()
                workers.append((node, process, parent))
            deadline = time.monotonic() + self.plan.config.readiness_timeout_s
            ready = set()
            while len(ready) < len(workers):
                for node, process, connection in workers:
                    if node.name in ready:
                        continue
                    if connection.poll():
                        message = connection.recv()
                        if message.get("status") != "ready":
                            raise RuntimeError(f"{node.name} initialization failed: {message}")
                        ready.add(node.name)
                    elif process.exitcode is not None:
                        raise RuntimeError(f"{node.name} exited before readiness ({process.exitcode})")
                if time.monotonic() >= deadline:
                    raise TimeoutError("workers did not become ready in time")
                time.sleep(.005)
            epoch = epoch_callback(self.plan) if epoch_callback else self.plan.epoch_start
            if epoch <= time.time():
                raise RuntimeError("workers became ready after epoch_start; increase startup_delay_s")
            self.plan.epoch_start = epoch
            self.plan.validate()
            self.plan.save(local_plan_path)
            manifest.update(status="running", epoch_start=epoch)
            atomic_json(manifest_path, manifest)
            for _, _, connection in workers:
                connection.send(dict(action="start", epoch_start=epoch))
            deadline = time.monotonic() + (epoch-time.time()) + self.plan.config.duration_s + self.plan.config.worker_grace_s
            while any(process.is_alive() for _, process, _ in workers):
                for node, process, _ in workers:
                    if process.exitcode not in (None, 0):
                        raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("worker exceeded experiment deadline and shutdown grace")
                time.sleep(.01)
            for node, process, _ in workers:
                process.join()
                if process.exitcode != 0:
                    raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                for suffix in ("metrics.json", "events.jsonl", "packets.jsonl"):
                    if not os.path.isfile(os.path.join(self.run_dir, f"{node.name}.{suffix}")):
                        raise RuntimeError(f"missing output for {node.name}: {suffix}")
            manifest.update(success=True, status="complete")
        except BaseException:
            manifest.update(status="failed", error=traceback.format_exc())
            raise
        finally:
            stop.set()
            # One shared cleanup budget, not a full timeout for every worker.
            cleanup_deadline = time.monotonic() + 1
            for _, process, _ in workers:
                process.join(timeout=max(0, cleanup_deadline-time.monotonic()))
            for _, process, _ in workers:
                if process.is_alive():
                    process.terminate()
            cleanup_deadline = time.monotonic() + 1
            for node, process, connection in workers:
                process.join(timeout=max(0, cleanup_deadline-time.monotonic()))
                if process.is_alive():
                    process.kill()
                    process.join(timeout=.5)
                connection.close()
                if process.exitcode != 0:
                    manifest["failures"].append(dict(node=node.name, exitcode=process.exitcode))
            manifest["finished_at"] = time.time()
            atomic_json(manifest_path, manifest)
        return self.run_dir
