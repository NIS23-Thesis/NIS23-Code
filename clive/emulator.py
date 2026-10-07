"""Localhost CLIVE process supervisor.

Each virtual node runs in its own ``multiprocessing.Process`` as required by the emulator
design. The supervisor starts every node on this machine, waits for readiness, then arms all
workers with one shared experiment epoch.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import tempfile
import time
import traceback

from nodebootstrapper import run_node
from orchestrator import EmulationPlan, atomic_json


class Emulator:
    def __init__(self, plan_path, results_dir, *, protocol_class_path=None):
        self.results_dir = os.path.abspath(results_dir)
        self.plan = EmulationPlan.load(os.path.abspath(plan_path))
        self.protocol_class_path = protocol_class_path
        self.run_dir = None

    def run(self):
        self.plan.validate()
        nodes = list(self.plan.nodes)
        os.makedirs(self.results_dir, exist_ok=True)
        self.run_dir = tempfile.mkdtemp(prefix="run-", dir=self.results_dir)
        local_plan_path = os.path.join(self.run_dir, "plan.json")
        self.plan.save(local_plan_path)
        self.plan.save_trace(os.path.join(self.run_dir, "mobility.tsv"))

        manifest_path = os.path.join(self.run_dir, "run-manifest.json")
        manifest = {
            "success": False,
            "status": "starting",
            "execution": "localhost",
            "nodes": [n.name for n in nodes],
            "started_at": time.time(),
            "plan_path": local_plan_path,
            "failures": [],
        }
        atomic_json(manifest_path, manifest)

        # Linux/Fedora: fork is materially lighter for large localhost process counts (e.g.
        # 200 nodes). Fall back to spawn on platforms where fork is unavailable.
        method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        context = mp.get_context(method)
        stop = context.Event()
        workers = []

        try:
            for node in nodes:
                parent, child = context.Pipe()
                process = context.Process(
                    name=f"CLIVE-{node.name}",
                    target=run_node,
                    args=(node.name, local_plan_path, self.run_dir, self.protocol_class_path, child, stop),
                )
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
                        try:
                            message = connection.recv()
                        except EOFError:
                            raise RuntimeError(f"{node.name} closed its control pipe before readiness")
                        if message.get("status") != "ready":
                            raise RuntimeError(f"{node.name} initialization failed: {message}")
                        ready.add(node.name)
                    elif process.exitcode is not None:
                        raise RuntimeError(f"{node.name} exited before readiness ({process.exitcode})")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"workers did not become ready in time ({len(ready)}/{len(workers)})")
                time.sleep(0.002)

            manifest.update(
                status="ready",
                multiprocessing_start_method=method,
                trace_sha256=self.plan.trace_digest(),
                ready_at=time.time(),
            )
            atomic_json(manifest_path, manifest)

            # Re-arm a fresh future epoch after every worker is ready. This keeps protocol
            # execution synchronized without coupling it to mobility-trace generation speed.
            epoch = time.time() + self.plan.config.startup_delay_s
            self.plan.epoch_start = epoch
            self.plan.validate()
            self.plan.save(local_plan_path)
            manifest.update(status="running", epoch_start=epoch)
            atomic_json(manifest_path, manifest)
            for _, _, connection in workers:
                connection.send({"action": "start", "epoch_start": epoch})

            deadline = (
                time.monotonic()
                + self.plan.config.startup_delay_s
                + self.plan.config.duration_s
                + self.plan.config.worker_grace_s
            )
            while any(process.is_alive() for _, process, _ in workers):
                for node, process, connection in workers:
                    if connection.poll():
                        try:
                            message = connection.recv()
                        except EOFError:
                            message = None
                        if message and message.get("status") == "failed":
                            raise RuntimeError(f"worker {node.name} failed:\n{message.get('error', '')}")
                    if process.exitcode not in (None, 0):
                        raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("worker exceeded experiment deadline and shutdown grace")
                time.sleep(0.01)

            for node, process, _ in workers:
                process.join()
                if process.exitcode != 0:
                    raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                for suffix in ("metrics.json", "events.jsonl", "packets.jsonl"):
                    if not os.path.isfile(os.path.join(self.run_dir, f"{node.name}.{suffix}")):
                        raise RuntimeError(f"missing output for {node.name}: {suffix}")

            manifest.update(success=True, status="complete", completed_at=time.time())
        except BaseException:
            manifest.update(status="failed", error=traceback.format_exc())
            raise
        finally:
            stop.set()
            cleanup_deadline = time.monotonic() + 2.0
            for _, process, _ in workers:
                process.join(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            for _, process, _ in workers:
                if process.is_alive():
                    process.terminate()
            cleanup_deadline = time.monotonic() + 2.0
            for node, process, connection in workers:
                process.join(timeout=max(0.0, cleanup_deadline - time.monotonic()))
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                connection.close()
                if process.exitcode != 0:
                    manifest["failures"].append({"node": node.name, "exitcode": process.exitcode})
            manifest["finished_at"] = time.time()
            atomic_json(manifest_path, manifest)

        return self.run_dir
