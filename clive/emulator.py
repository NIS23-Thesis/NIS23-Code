"""Localhost CLIVE process supervisor.

Each virtual node runs in its own ``multiprocessing.Process``. Each node process in turn owns
a separate radio/TX child process, so protocol/RX execution and CPU-heavy radio calculations
do not share a Python GIL. The supervisor waits for readiness and then arms all workers with
one shared experiment epoch.
"""
from __future__ import annotations

import multiprocessing as mp
import json
from pathlib import Path
from multiprocessing.connection import wait as wait_connections
import os
import tempfile
import re
import time
import traceback
from collections import defaultdict

from nodebootstrapper import run_node
from orchestrator import EmulationPlan, atomic_json
from nodesocket import MetricsCollector


def create_run_directory(results_dir):
    """Allocate run-1, run-2, ... without renaming legacy result folders.

    The counter is durable, and mkdir is exclusive. The advisory lock protects
    multiple concurrent CLIVE runners on POSIX hosts; mkdir still prevents
    collisions if the advisory-lock implementation is unavailable.
    """
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    control = root / "_control"
    control.mkdir(exist_ok=True)
    lock_path = control / "run-number.lock"
    counter_path = control / "run-counter.txt"
    with lock_path.open("a+b") as lock_file:
        if os.name == "posix":
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                previous = max(0, int(counter_path.read_text().strip()))
            except (ValueError, OSError):
                previous = 0
            for folder in root.iterdir():
                if folder.is_dir():
                    matched = re.fullmatch(r"run-(\d+)", folder.name)
                    if matched:
                        previous = max(previous, int(matched.group(1)))
            while True:
                previous += 1
                directory = root / f"run-{previous}"
                try:
                    directory.mkdir()  # Atomic exclusive create, even across processes.
                    break
                except FileExistsError:
                    continue
            temporary_counter = control / f"run-counter-{os.getpid()}.tmp"
            temporary_counter.write_text(str(previous) + "\n", encoding="utf-8")
            os.replace(temporary_counter, counter_path)
            return str(directory)
        finally:
            if os.name == "posix":
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class Emulator:
    def __init__(self, plan_path, results_dir, *, protocol_class_path="flame_protocol:FlameProtocol"):
        self.results_dir = os.path.abspath(results_dir)
        self.plan = EmulationPlan.load(os.path.abspath(plan_path))
        self.protocol_class_path = protocol_class_path
        self.run_dir = None

    @staticmethod
    def _aggregate_metrics(per_node):
        fields = MetricsCollector.FIELDS
        totals = {name: 0 for name in fields}
        by_type = defaultdict(lambda: {name: 0 for name in fields})
        nodes = {}
        for node_name, snapshot in sorted(per_node.items()):
            compact = {
                "success": bool(snapshot.get("success", False)),
                **{name: snapshot.get(name, 0) for name in fields},
                "by_packet_type": snapshot.get("by_packet_type", {}),
                "unknown_sources_rejected": snapshot.get("unknown_sources_rejected", 0),
            }
            nodes[node_name] = compact
            for name in fields:
                totals[name] += compact[name]
            for packet_type, values in compact["by_packet_type"].items():
                for name in fields:
                    by_type[str(packet_type)][name] += values.get(name, 0)
        # Radio emissions and recipient deliveries are different counting units:
        # one broadcast emission may produce many recipient successes. Never use
        # packets_received / packets_sent as PDR in a multi-node experiment.
        attempts = totals["delivery_attempts"]
        handoffs = totals["kernel_datagrams_sent"]
        drops = totals["delivery_drops"]
        delivery = {
            "recipient_delivery_attempts": attempts,
            "recipient_kernel_handoffs": handoffs,
            "recipient_delivery_drops": drops,
            "recipient_handoff_ratio": (handoffs / attempts if attempts else None),
            "recipient_loss_ratio": (drops / attempts if attempts else None),
            "unresolved_recipient_attempts": max(0, attempts - handoffs - drops),
            "note": "Recipient-scoped radio/kernel handoff, not end-to-end FLAME acknowledgment "
                    "or application-level receipt. Broadcast counts once per eligible recipient.",
        }
        return {
            "schema_version": 5,
            "success": all(row["success"] for row in nodes.values()),  # legacy: process completion only
            "execution_success": all(row["success"] for row in nodes.values()),
            "protocol_success": None,  # not proven by OS process exit status
            "totals": totals,
            "by_packet_type": {key: dict(value) for key, value in sorted(by_type.items())},
            "nodes": nodes,
            "recipient_delivery": delivery,
            "accounting": (
                "packets_admitted = accepted into software queue; packets_sent/bits_sent = "
                "actual modeled radio emissions; kernel_datagrams_sent = successful kernel "
                "sendto calls (not verified delivery); delivery_drops = per-recipient modeled loss"
            ),
        }

    def _flame_outcome(self):
        """Integration-only outcome summary from final adapter artifacts.

        The CLIVE `success` field means workers exited successfully. Do not use it
        as proof that model transfers or learning succeeded.
        """
        details = {}
        for file in sorted((Path(self.run_dir) / "flame").glob("*.json")):
            try:
                row = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            details[file.stem] = row
        heads = [row for row in details.values() if row.get("role") == "CH"]
        # A crashed CH may never write its final adapter JSON. Do not silently
        # aggregate only surviving CHs and suggest the evidence is complete.
        plan = getattr(self, "plan", None)
        expected_heads = sorted(spec.name for spec in getattr(plan, "nodes", ())
                                if int(spec.role) == 1) if plan is not None else None
        reported_heads = sorted(row.get("node", name) for name, row in details.items()
                                if row.get("role") == "CH")
        missing_heads = sorted(set(expected_heads) - set(reported_heads)) if expected_heads is not None else None
        head_reporting_complete = (len(missing_heads) == 0) if missing_heads is not None else None
        statuses = [row.get("model_transfer_summary", {}) for row in heads]
        count = lambda key: sum(int(s.get(key, 0)) for s in statuses)
        started = count("started_versions")
        completed = count("completed_versions")
        failed = count("failed_versions")
        unfinished = count("unfinished_versions")
        superseded = count("superseded_versions")
        # Configuration and execution are different claims. A 30 s run with
        # first sampling at 30 s legitimately executes no training even though
        # its trainer is MNIST. Use observed training events, not configured flags.
        trainer_configured = bool(heads) and all(
            row.get("workload", {}).get("trainer") == "mnist" or
            row.get("workload", {}).get("real_neural_network_trainer_configured") is True or
            row.get("workload", {}).get("real_neural_network_training") is True
            for row in heads
        )
        trained_heads = sorted(
            row.get("node", "unknown") for row in heads
            if int(row.get("counters", {}).get("trained", 0)) > 0
        )
        training_count = sum(int(row.get("counters", {}).get("trained", 0)) for row in heads)
        real_training = trainer_configured and training_count > 0
        evaluated = {
            row["node"]: row["model_evaluation"] for row in heads
            if row.get("model_evaluation", {}).get("status") == "ok"
            and "accuracy" in row["model_evaluation"]
        }
        accuracy_values = [value["accuracy"] for value in evaluated.values()]
        eval_errors = {
            row["node"]: row["model_evaluation"].get("reason")
            for row in heads if row.get("model_evaluation", {}).get("status") == "error"
        }
        application = {
            "unique_sensor_observations_received": sum(
                int(row.get("application_delivery", {}).get("unique_sensor_observations_received", 0)) for row in heads),
            "unique_model_updates_received": sum(
                int(row.get("application_delivery", {}).get("unique_model_updates_received", 0)) for row in heads),
            "model_updates_buffered_by_flame": sum(
                int(row.get("application_delivery", {}).get("model_updates_buffered_by_flame",
                    row.get("application_delivery", {}).get("unique_model_updates_received", 0))) for row in heads),
            "model_acceptance_stage": "FLAME aggregation buffer admission, not model compatibility or successful aggregation",
            "unique_sensor_payload_bytes_received": sum(
                int(row.get("application_delivery", {}).get("unique_sensor_payload_bytes_received", 0)) for row in heads),
            "unique_model_payload_bytes_received": sum(
                int(row.get("application_delivery", {}).get("unique_model_payload_bytes_received", 0)) for row in heads),
        }
        application["unique_useful_payload_bytes_received"] = (
            application["unique_sensor_payload_bytes_received"] + application["unique_model_payload_bytes_received"]
        )
        sensors = [row for row in details.values() if row.get("role") == "SN"]
        return {
            "available": bool(heads), "trainer": (heads[0].get("workload", {}).get("trainer") if heads else None),
            "real_neural_network_trainer_configured": trainer_configured,
            "real_neural_network_training_executed": real_training,
            "training_executed": training_count > 0,
            "cluster_heads_with_local_training": trained_heads,
            "cluster_heads_without_local_training": sorted(
                row.get("node", "unknown") for row in heads
                if int(row.get("counters", {}).get("trained", 0)) == 0
            ),
            # Backward-compatible field now means actual observed training.
            "real_neural_network_training": real_training,
            "final_mnist_accuracy_mean": (sum(accuracy_values) / len(accuracy_values)) if accuracy_values else None,
            "final_mnist_accuracy_by_ch": {node: v["accuracy"] for node, v in evaluated.items()},
            "final_mnist_accuracy_status": ("measured" if accuracy_values else "not_measured"),
            "model_evaluation_errors": eval_errors,
            "expected_cluster_heads": expected_heads,
            "reported_cluster_heads": reported_heads,
            "missing_cluster_head_results": missing_heads,
            "cluster_head_reporting_complete": head_reporting_complete,
            "flame_processing_errors_by_ch": {
                row.get("node", f"CH-{index}"): int(row.get("counters", {}).get("fl_error", 0))
                for index, row in enumerate(heads, start=1)
                if int(row.get("counters", {}).get("fl_error", 0)) > 0
            },
            "local_training_events": training_count,
            "model_updates_aggregated": sum(int(row.get("counters", {}).get("aggregated", 0)) for row in heads),
            "model_transfers_started": started, "model_transfers_completed": completed,
            "model_transfers_failed": failed, "model_transfers_unfinished": unfinished,
            "model_transfers_superseded": superseded,
            "model_transfers_censored_at_cutoff": unfinished,
            "model_transfer_status_counts_reconcile": (started == completed + failed + unfinished + superseded),
            "model_transfer_resolution": {
                "attempted_versions": started,
                "resolved_versions": completed + failed,
                "confirmed_completion_fraction_of_resolved": (
                    completed / (completed + failed) if completed + failed else None
                ),
                "unfinished_versions_excluded_from_resolved_rate": unfinished,
                "superseded_versions_excluded_from_resolved_rate": superseded,
            },
            "application_delivery": application,
            "sensor_observation_accounting": {
                "generated": sum(int(row.get("samples_generated", 0)) for row in sensors),
                "offline_buffer_dropped": sum(int(row.get("offline_buffer_drops", 0)) for row in sensors),
                "sensing_buffer_dropped": sum(int(row.get("sensing_buffer_drops", 0)) for row in sensors),
                "accepted_unique_at_cluster_heads": application["unique_sensor_observations_received"],
                "retained_at_cluster_heads": sum(int(row.get("observations_retained", 0)) for row in heads),
                "evicted_at_least": sum(int(row.get("observations_evicted_at_least", 0)) for row in heads),
            },
            "model_delivery_success": (
                None if head_reporting_complete is False else
                ((failed == 0 and unfinished == 0 and completed == started) if started else None)
            ),
            "model_delivery_success_stage": "sender ACK completion only; not receiver model validity or aggregate quality",
            "note": "Model transfer status is sender-confirmed ACK completion, not receiver model validation, accuracy, or convergence. Missing CH result files make the integration outcome incomplete.",
        }

    def run(self):
        self.plan.validate()
        nodes = list(self.plan.nodes)
        # Warm the shared sample cache once in the supervisor. A cold cache must
        # never cause 200 sensors to decompress the full MNIST dataset at once.
        if (self.protocol_class_path == "flame_protocol:FlameProtocol" and
                self.plan.flame_settings.get("sample_source", "mnist") == "mnist" and
                any(int(n.role) == 0 for n in nodes)):
            from pathlib import Path
            from mnist_cache import prepare_mnist_cache
            prepare_mnist_cache(Path(__file__).resolve().parent.parent / "mnist" / "mnist.npz")
        os.makedirs(self.results_dir, exist_ok=True)
        self.run_dir = create_run_directory(self.results_dir)
        local_plan_path = os.path.join(self.run_dir, "plan.json")
        self.plan.save(local_plan_path)
        if self.plan.logging_config.mobility_tsv:
            self.plan.save_trace(os.path.join(self.run_dir, "mobility.tsv"))

        manifest_path = os.path.join(self.run_dir, "run-manifest.json")
        manifest = {
            "success": False,
            "status": "starting",
            "execution": "localhost",
            "process_architecture": (
                "one node process + one radio thread per virtual node" if self.plan.config.radio_execution == "thread"
                else "one node process + one radio child process per virtual node"
            ),
            "emulation_worker_threads": len(nodes) if self.plan.config.radio_execution == "thread" else 0,
            "gil_isolation": (
                "node and radio share a GIL; CPU saturation may affect timing" if self.plan.config.radio_execution == "thread"
                else "node/protocol and radio/TX execute in separate OS processes"
            ),
            "nodes": [n.name for n in nodes],
            "started_at": time.time(),
            "plan_path": local_plan_path,
            "mobility_path": os.path.join(self.run_dir, "mobility.bin"),
            "mobility_storage": dict(self.plan.trace_storage or {}),
            "protocol": self.protocol_class_path or "protocol_interface:NullProtocol",
            "failures": [],
        }
        atomic_json(manifest_path, manifest)

        # Linux/Fedora: fork is materially lighter for large localhost process counts (e.g.
        # 200 nodes). Fall back to spawn on platforms where fork is unavailable.
        method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        context = mp.get_context(method)
        stop = context.Event()
        workers = []
        metrics_by_node = {}

        def handle_worker_message(node, message):
            if not message:
                return
            status = message.get("status")
            if status == "failed":
                raise RuntimeError(f"worker {node.name} failed:\n{message.get('error', '')}")
            if status == "complete":
                metrics = message.get("metrics")
                if not isinstance(metrics, dict):
                    raise RuntimeError(f"worker {node.name} returned invalid metrics")
                metrics_by_node[node.name] = metrics

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
            connection_to_worker = {connection: (node, process) for node, process, connection in workers}
            pending_connections = set(connection_to_worker)
            while pending_connections:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"workers did not become ready in time ({len(ready)}/{len(workers)})")
                readable = wait_connections(pending_connections, timeout=min(0.25, remaining))
                for connection in readable:
                    node, process = connection_to_worker[connection]
                    try:
                        message = connection.recv()
                    except EOFError:
                        raise RuntimeError(f"{node.name} closed its control pipe before readiness")
                    if message.get("status") != "ready":
                        raise RuntimeError(f"{node.name} initialization failed: {message}")
                    ready.add(node.name)
                    pending_connections.discard(connection)
                for connection in tuple(pending_connections):
                    node, process = connection_to_worker[connection]
                    if process.exitcode is not None:
                        raise RuntimeError(f"{node.name} exited before readiness ({process.exitcode})")

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
            live_connections = {connection: (node, process) for node, process, connection in workers}
            while any(process.is_alive() for _, process, _ in workers):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("worker exceeded experiment deadline and shutdown grace")
                readable = wait_connections(list(live_connections), timeout=min(0.05, remaining)) if live_connections else []
                for connection in readable:
                    node, process = live_connections[connection]
                    try:
                        message = connection.recv()
                    except EOFError:
                        live_connections.pop(connection, None)
                        message = None
                    handle_worker_message(node, message)
                for node, process, connection in workers:
                    if process.exitcode not in (None, 0):
                        raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                    if process.exitcode is not None:
                        live_connections.pop(connection, None)

            for node, process, connection in workers:
                process.join()
                if process.exitcode != 0:
                    raise RuntimeError(f"worker {node.name} failed with exit code {process.exitcode}")
                # A worker can exit between the final poll and the while-condition check.
                # Drain any completion message that is still buffered in its control pipe.
                while connection.poll():
                    try:
                        message = connection.recv()
                    except EOFError:
                        break
                    handle_worker_message(node, message)

            missing = [node.name for node in nodes if node.name not in metrics_by_node]
            if missing:
                raise RuntimeError(f"missing final metrics from workers: {missing}")
            summary = self._aggregate_metrics(metrics_by_node)
            outcome = self._flame_outcome() if self.protocol_class_path == "flame_protocol:FlameProtocol" else None
            summary["flame_outcome"] = outcome
            duration = self.plan.config.duration_s
            useful_bytes = (outcome or {}).get("application_delivery", {}).get("unique_useful_payload_bytes_received")
            summary["measurement_window"] = {
                "duration_s": duration,
                "emitted_wire_throughput_bps": summary["totals"]["bits_sent"] / duration,
                "unique_application_delivery_bps": (
                    8 * useful_bytes / duration if useful_bytes is not None else None
                ),
                "definition": "Entire simulation duration, aggregated across all nodes. NOT a local-training "
                              "round or the thesis's asynchronous per-round throughput/goodput.",
                "caveat": "Broadcast emission and recipient reception have different fanout units; "
                          "do not subtract these rates to estimate overhead.",
            }
            link = self.plan.link_config
            summary["energy_model"] = {
                "basis": "analytical estimate, not measured device energy",
                "formula": "E_tx = bits*(E_elec + E_amp*distance_m**n); E_rx = bits*E_elec",
                "pathloss_exponent": link.pathloss_exponent,
                "amplifier_coefficient_j_per_bit_mn": link.e_amp_j_per_bit_mn,
                "amplifier_coefficient_pj_per_bit_mn": link.e_amp_j_per_bit_mn / 1e-12,
                "amplifier_units": "J/(bit*m**n), n=pathloss_exponent",
                "amplifier_units_consistent_with_exponent": True,
                "coefficient_is_calibrated": False,
                "warning": "Dimensionally consistent generalized d**n model, using a nominal "
                           "100 pJ/(bit*m**n) coefficient; NOT calibrated physical radio energy. "
                           "A published 100 pJ/(bit*m**2) coefficient for n=2 does not "
                           "independently validate the same numerical value for n=3.",
            }
            summary["protocol_success"] = (outcome or {}).get("model_delivery_success")
            summary["status_explanation"] = (
                "success/execution_success: worker processes exited without error. "
                "protocol_success: only sender-confirmed MODEL_UPDATE completion if transfers occurred. "
                "Neither field proves ML convergence."
            )
            atomic_json(os.path.join(self.run_dir, "metrics.json"), summary)

            manifest.update(success=True, status="complete", completed_at=time.time(),
                            execution_success=True, protocol_success=summary["protocol_success"])
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
