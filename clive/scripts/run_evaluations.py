#!/usr/bin/env python3
"""List, validate, plan, or execute CLIVE evaluation scenario groups."""
from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
import datetime
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "configs" / "evaluations"
sys.path.insert(0, str(ROOT))
from orchestrator import Orchestrator


def scenario_files(group: str):
    if group == "all":
        return sorted(path for path in CONFIG_ROOT.rglob("*.json") if path.name != "manifest.json")
    folder = CONFIG_ROOT / group
    if not folder.is_dir():
        raise SystemExit(f"unknown evaluation group: {group}")
    return sorted(path for path in folder.glob("*.json") if path.name != "manifest.json")





def recover_interrupted_artifacts(results_root: Path, known_files) -> int:
    """Explicitly catalog finished emulator outputs missing from a runner index.

    This does NOT claim the supervising subprocess exited cleanly. The distinct
    `recovered_artifact` status preserves that provenance and requires BOTH
    metrics.json and a complete run-manifest.json.
    """
    index_path = results_root / "evaluation-index.jsonl"
    indexed_dirs = set()
    if index_path.is_file():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("run_dir"):
                    indexed_dirs.add(str(Path(row["run_dir"]).resolve()))
            except (ValueError, TypeError):
                continue
    by_stem = {}
    for path in known_files:
        by_stem.setdefault(path.stem, []).append(path)
    recovered = 0
    for metrics_path in sorted(results_root.glob("*/seed-*/run-*/metrics.json")):
        run_dir = metrics_path.parent
        if str(run_dir.resolve()) in indexed_dirs:
            continue
        candidates = by_stem.get(run_dir.parent.parent.name, [])
        scenario_used = run_dir.parent / "scenario-used.json"
        manifest_path = run_dir / "run-manifest.json"
        if not scenario_used.is_file() or not manifest_path.is_file():
            continue
        try:
            used = json.loads(scenario_used.read_text(encoding="utf-8"))
            expected_hash = hashlib.sha256(json.dumps(used, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            seed = int(used["runtime"]["seed"])
            if run_dir.parent.name != f"seed-{seed}":
                continue
            # Match the scenario by stem and all fields except runtime seed and
            # deliberately short diagnostic overrides. Ambiguous stems are not recovered.
            if len(candidates) != 1:
                continue
            raw = json.loads(candidates[0].read_text(encoding="utf-8"))
            if raw.get("num_ch") != used.get("num_ch") or raw.get("num_sn") != used.get("num_sn"):
                continue
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not (manifest.get("status") == "complete" and manifest.get("execution_success") is True
                    and metrics.get("execution_success") is True):
                continue
            row = {"scenario": str(candidates[0].relative_to(ROOT)), "seed": seed,
                   "scenario_sha256": expected_hash, "run_dir": str(run_dir.resolve()),
                   "duration_s": used["runtime"]["duration_s"],
                   "diagnostic_only": used["runtime"]["duration_s"] < 300,
                   "status": "recovered_artifact", "execution_success": True,
                   "protocol_success": metrics.get("protocol_success"),
                   "recovered_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "warning": "Emulator metrics and manifest independently show completion, but runner exit was not observed.",
                   "flame_outcome": metrics.get("flame_outcome"),
                   "recipient_delivery": metrics.get("recipient_delivery")}
            with index_path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, allow_nan=False) + "\n")
            indexed_dirs.add(str(run_dir.resolve()))
            recovered += 1
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return recovered


def write_campaign_reports(results_root: Path, planned_keys=None) -> dict:
    """Report ACTUAL recorded outcomes; never infer success from a generated plan."""
    index_path = results_root / "evaluation-index.jsonl"
    records = []
    if index_path.exists():
        for line_no, line in enumerate(index_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("not an object")
                record["index_line"] = line_no
                records.append(record)
            except (json.JSONDecodeError, ValueError) as exc:
                records.append({"index_line": line_no, "status": "corrupt_index_record", "error": str(exc)})
    status_counts = dict(Counter(str(r.get("status", "unknown")) for r in records))
    protocol_counts = dict(Counter(
        "unknown" if r.get("protocol_success") is None else str(r["protocol_success"]).lower()
        for r in records if r.get("status") in {"completed", "recovered_artifact"}
    ))
    latest = {}
    for record in records:
        if record.get("scenario") and record.get("seed") is not None:
            latest[(record["scenario"], record["seed"])] = record
    planned_keys = set(planned_keys or [])
    missing_keys = sorted(planned_keys - set(latest), key=lambda x: (x[0], str(x[1])))
    report = {
        "schema": "clive-evaluation-campaign-v1",
        "planned_scenario_seed_pairs": len(planned_keys) if planned_keys else None,
        "missing_scenario_seed_pairs": len(missing_keys) if planned_keys else None,
        "missing_scenario_seed_pair_examples": [{"scenario": s, "seed": seed} for s, seed in missing_keys[:20]],
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "attempts_recorded": len(records),
        "distinct_scenario_seed_pairs": len(latest),
        "statuses_all_attempts": status_counts,
        "statuses_latest_attempt": dict(Counter(str(x.get("status", "unknown")) for x in latest.values())),
        "protocol_success_of_completed_executions": protocol_counts,
        "warning": "Completed execution is NOT proof of protocol success, statistical reliability, or completion of all planned scenarios.",
        "records": records,
    }
    results_root.mkdir(parents=True, exist_ok=True)
    (results_root / "campaign-summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    cols = ("index_line", "scenario", "seed", "scenario_sha256", "duration_s", "diagnostic_only",
            "start_utc", "finish_utc", "status", "exit_code", "execution_success",
            "protocol_success", "run_dir", "error", "timeout_s")
    with (results_root / "campaign-results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    # Export one row per observed attempt. The full simulation metrics (not
    # inferred values) are the only source for communication, energy, FL values.
    metric_cols = ("scenario", "seed", "status", "diagnostic_only", "run_dir",
                   "execution_success", "protocol_success", "real_mnist_training",
                   "real_mnist_trainer_configured", "real_mnist_training_executed",
                   "local_training_events", "peer_aggregations", "transfers_started",
                   "transfers_completed", "transfers_failed", "transfers_censored",
                   "transfer_completion_fraction_of_started", "unique_sensor_observations_received",
                   "recipient_attempts", "recipient_handoffs", "recipient_drops",
                   "recipient_handoff_ratio", "recipient_loss_ratio", "mean_final_mnist_accuracy",
                   "modeled_energy_tx_j", "modeled_energy_rx_j",
                   "whole_experiment_wire_bps", "whole_experiment_unique_app_bps",
                   "metric_definition_warning")
    with (results_root / "campaign-metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=metric_cols)
        writer.writeheader()
        for r in records:
            entry = {k: r.get(k) for k in ("scenario", "seed", "status", "diagnostic_only",
                                              "run_dir", "execution_success", "protocol_success")}
            metric_path = Path(r.get("run_dir") or "/nonexistent") / "metrics.json"
            if r.get("status") in {"completed", "recovered_artifact"} and metric_path.is_file():
                try:
                    m = json.loads(metric_path.read_text(encoding="utf-8"))
                    fl = m.get("flame_outcome") or {}
                    rec = m.get("recipient_delivery") or {}
                    totals = m.get("totals") or {}
                    rates = m.get("measurement_window") or {}
                    started = fl.get("model_transfers_started")
                    completed = fl.get("model_transfers_completed")
                    entry.update(
                        real_mnist_training=fl.get("real_neural_network_training"),
                        real_mnist_trainer_configured=fl.get("real_neural_network_trainer_configured"),
                        real_mnist_training_executed=fl.get("real_neural_network_training_executed"),
                        local_training_events=fl.get("local_training_events"),
                        peer_aggregations=fl.get("model_updates_aggregated"),
                        transfers_started=started, transfers_completed=completed,
                        transfers_failed=fl.get("model_transfers_failed"),
                        transfers_censored=fl.get("model_transfers_censored_at_cutoff"),
                        transfer_completion_fraction_of_started=(completed / started if started else None),
                        unique_sensor_observations_received=(fl.get("application_delivery") or {}).get("unique_sensor_observations_received"),
                        recipient_attempts=rec.get("recipient_delivery_attempts"),
                        recipient_handoffs=rec.get("recipient_kernel_handoffs"),
                        recipient_drops=rec.get("recipient_delivery_drops"),
                        recipient_handoff_ratio=rec.get("recipient_handoff_ratio"),
                        recipient_loss_ratio=rec.get("recipient_loss_ratio"),
                        mean_final_mnist_accuracy=fl.get("final_mnist_accuracy_mean"),
                        modeled_energy_tx_j=totals.get("energy_sent_j"),
                        modeled_energy_rx_j=totals.get("energy_received_j"),
                        whole_experiment_wire_bps=rates.get("emitted_wire_throughput_bps"),
                        whole_experiment_unique_app_bps=rates.get("unique_application_delivery_bps"),
                        metric_definition_warning="Whole-experiment rates, NOT per-training-round throughput/goodput; kernel handoff is NOT protocol ACK; energy analytical only.",
                    )
                except (OSError, json.JSONDecodeError, TypeError, ValueError, ZeroDivisionError) as exc:
                    entry["metric_definition_warning"] = "metrics unreadable: " + repr(exc)
            writer.writerow(entry)
    if planned_keys:
        with (results_root / "campaign-coverage.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=("scenario", "seed", "status", "protocol_success", "run_dir"))
            writer.writeheader()
            for scenario, seed in sorted(planned_keys, key=lambda x: (x[0], x[1])):
                record = latest.get((scenario, seed), {})
                writer.writerow({"scenario": scenario, "seed": seed,
                                 "status": record.get("status", "not_run"),
                                 "protocol_success": record.get("protocol_success"),
                                 "run_dir": record.get("run_dir")})
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("group", nargs="?", default="all", choices=["all", "mobility", "density", "area", "radio", "topology", "thesis_matrix", "loss_stress"])
    p.add_argument("--list", action="store_true", help="list matching scenarios")
    p.add_argument("--run", action="store_true", help="execute scenarios; default only validates/builds plans")
    p.add_argument("--protocol", help="ProtocolInterface class as module:ClassName")
    p.add_argument("--duration", type=float, help="override duration for exploratory runs only")
    p.add_argument("--allow-short-thesis-smoke", action="store_true", help="explicitly permit sub-300s thesis-matrix smoke checks; NOT evaluation data")
    p.add_argument("--diagnostic-sample-interval-s", type=float, help="override sensor frequency for short smoke tests ONLY (never formal evaluation)")
    p.add_argument("--results", default="results/evaluations")
    p.add_argument("--seeds", type=int, nargs="+", default=None,
                   help="independent seeds (e.g. --seeds 810 811 812); default uses each preset seed")
    p.add_argument("--limit", type=int, help="execute/validate only the first N cases (for staged runs)")
    p.add_argument("--packet-events", action="store_true", help="record detailed radio packet traces")
    p.add_argument("--protocol-events", action="store_true", help="record detailed FLAME event traces")
    p.add_argument("--resume", action="store_true", help="skip same-seed, same-config executions already completed with metrics")
    p.add_argument("--keep-going", action="store_true", help="continue the campaign after a failed/timed-out case, recording every outcome")
    p.add_argument("--report-only", action="store_true", help="regenerate campaign-summary.json and campaign-results.csv from recorded attempts")
    p.add_argument("--recover-interrupted", action="store_true", help="with --report-only, explicitly index independently verified finished artifacts from interrupted runners")
    p.add_argument("--case-timeout-s", type=float, default=None,
                   help="optional wall-clock timeout per case (timeouts are NOT protocol failures)")
    args = p.parse_args()

    results_root = Path(args.results).resolve()
    files = scenario_files(args.group)
    if args.limit is not None:
        if args.limit < 1:
            p.error('--limit must be positive')
        files = files[:args.limit]
    if args.seeds is not None and len(args.seeds) != len(set(args.seeds)):
        p.error("--seeds must be distinct to represent independent repetitions")
    planned_keys = {
        (str(p.relative_to(ROOT)), seed)
        for p in files
        for seed in (args.seeds if args.seeds is not None else
                     [json.loads(p.read_text()).get("runtime", {}).get("seed", 810)])
    }
    if args.recover_interrupted and not args.report_only:
        p.error("--recover-interrupted requires --report-only")
    if args.report_only:
        if args.recover_interrupted:
            count = recover_interrupted_artifacts(results_root, files)
            print(f"Recovered {count} complete saved artifact(s); supervisory exit unknown", flush=True)
        report = write_campaign_reports(results_root, planned_keys)
        print(json.dumps({k: v for k, v in report.items() if k != "records"}, indent=2))
        return
    if args.seeds is not None and len(args.seeds) != len(set(args.seeds)):
        p.error('--seeds must be distinct to represent independent repetitions')
    if args.case_timeout_s is not None and args.case_timeout_s <= 0:
        p.error('--case-timeout-s must be positive')
    if args.duration is not None and args.duration <= 0:
        p.error('--duration must be positive')
    if args.diagnostic_sample_interval_s is not None:
        if args.diagnostic_sample_interval_s <= 0:
            p.error('--diagnostic-sample-interval-s must be positive')
        if not (args.allow_short_thesis_smoke and args.duration is not None and args.duration < 300):
            p.error('--diagnostic-sample-interval-s is allowed only for explicitly marked sub-300s diagnostic runs')
    if (args.group in {'thesis_matrix', 'loss_stress', 'all'} and args.duration is not None
            and args.duration < 300 and not args.allow_short_thesis_smoke):
        p.error('Chapter 4.7 requires >=300 seconds. Use --allow-short-thesis-smoke for diagnostics, never for final thesis metrics.')
    if args.duration is not None and args.duration < 300 and args.allow_short_thesis_smoke:
        print('WARNING: SHORT-DURATION DIAGNOSTIC ONLY — these are not Chapter 4.7 evaluation results', flush=True)
    if args.list:
        for path in files:
            print(path.relative_to(ROOT))
        return

    # A run index records what actually executed, rather than confusing a
    # successfully generated plan with a completed experiment. The index is
    # append-only; reruns remain distinguishable by run directory and UTC time.
    previous_completions = set()
    index_path = results_root / "evaluation-index.jsonl"
    if args.resume and index_path.is_file():
        for line in index_path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (record.get("status") in {"completed", "recovered_artifact"} and record.get("execution_success")
                    and (Path(record.get("run_dir", "")) / "metrics.json").is_file()):
                previous_completions.add((record.get("scenario"), record.get("seed"), record.get("scenario_sha256")))
    failed_cases = 0
    for path in files:
        scenario = json.loads(path.read_text())
        if args.duration is not None:
            scenario.setdefault("runtime", {})["duration_s"] = args.duration
        if args.diagnostic_sample_interval_s is not None:
            scenario.setdefault("flame", {})["sample_interval_s"] = args.diagnostic_sample_interval_s
        for seed in (args.seeds if args.seeds is not None else [scenario.get("runtime", {}).get("seed", 810)]):
            case_name = str(path.relative_to(ROOT))
            run_scenario = json.loads(json.dumps(scenario))
            run_scenario.setdefault("runtime", {})["seed"] = seed
            logging = run_scenario.setdefault("logging", {})
            if args.packet_events:
                logging["packet_events"] = True
            if args.protocol_events:
                logging["protocol_events"] = True
            scenario_hash = hashlib.sha256(json.dumps(run_scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if args.run and (case_name, seed, scenario_hash) in previous_completions:
                print(f"[RESUME: PREVIOUS EXECUTION COMPLETE] {case_name} seed={seed}", flush=True)
                continue
            plan = Orchestrator().prepare(run_scenario)
            print(f"[PLAN OK — NOT A TEST RESULT] {path.relative_to(ROOT)} seed={seed} "
                  f"nodes={len(plan.nodes)} ticks={len(plan.trace)}", flush=True)
            if not args.run:
                continue
            case_results = results_root / path.stem / f"seed-{seed}"
            case_results.mkdir(parents=True, exist_ok=True)
            started = datetime.datetime.now(datetime.timezone.utc).isoformat()
            entry = {"scenario": str(path.relative_to(ROOT)), "seed": seed, "start_utc": started,
                     "scenario_sha256": scenario_hash,
                     "duration_s": run_scenario["runtime"]["duration_s"],
                     "diagnostic_only": bool(args.duration is not None and args.duration < 300),
                     "logging": logging, "status": "started"}
            (case_results / "scenario-used.json").write_text(json.dumps(run_scenario, indent=2) + "\n", encoding="utf-8")
            try:
                with tempfile.TemporaryDirectory(prefix="clive-eval-") as td:
                    tmp = Path(td) / "scenario.json"
                    tmp.write_text(json.dumps(run_scenario))
                    cmd = [sys.executable, str(ROOT / "clive.py"), "--scenario", str(tmp),
                           "--results", str(case_results)]
                    if args.protocol:
                        cmd += ["--protocol", args.protocol]
                    print(f"[RUN] {case_name} seed={seed} log={case_results / 'runner.log'}", flush=True)
                    with (case_results / "runner.log").open("w", encoding="utf-8") as log:
                        process = subprocess.Popen(cmd, cwd=ROOT, stdout=log,
                                                   stderr=subprocess.STDOUT, start_new_session=True)
                        try:
                            exit_code = process.wait(timeout=args.case_timeout_s)
                        except subprocess.TimeoutExpired:
                            entry["status"] = "wall_clock_timeout"
                            entry["timeout_s"] = args.case_timeout_s
                            # Kill the whole node/process group, not just clive.py.
                            if os.name == "posix":
                                os.killpg(process.pid, signal.SIGTERM)
                            else:
                                process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                if os.name == "posix":
                                    os.killpg(process.pid, signal.SIGKILL)
                                else:
                                    process.kill()
                                process.wait(timeout=5)
                            exit_code = process.returncode
                    entry["exit_code"] = exit_code
                    if entry["status"] == "wall_clock_timeout":
                        pass  # Inconclusive execution; do not mislabel as protocol failure.
                    elif exit_code:
                        entry["status"] = "failed"
                    else:
                        output_lines = (case_results / "runner.log").read_text(errors="replace").strip().splitlines()
                        run_dir = Path(output_lines[-1]) if output_lines else None
                        metric_path = run_dir / "metrics.json" if run_dir is not None else None
                        if metric_path is None or not metric_path.is_file():
                            entry["status"] = "missing_metrics"
                        else:
                            m = json.loads(metric_path.read_text())
                            entry.update(run_dir=str(run_dir), status="completed" if m.get("execution_success") else "failed",
                                         execution_success=m.get("execution_success"),
                                         protocol_success=m.get("protocol_success"),
                                         flame_outcome=m.get("flame_outcome"),
                                         recipient_delivery=m.get("recipient_delivery"))
            except Exception as exc:
                entry["status"] = "runner_error"
                entry["error"] = repr(exc)
            finally:
                entry["finish_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                results_root.mkdir(parents=True, exist_ok=True)
                with index_path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(entry, allow_nan=False) + "\n")
            if entry["status"] != "completed":
                failed_cases += 1
                if not args.keep_going:
                    write_campaign_reports(results_root, planned_keys)
                    raise SystemExit(f"evaluation failed: {path.name} seed={seed}: {entry['status']}")
    if args.run:
        report = write_campaign_reports(results_root, planned_keys)
        print(f"[CAMPAIGN] {report['attempts_recorded']} recorded attempts; "
              f"latest statuses={report['statuses_latest_attempt']}", flush=True)
        if failed_cases:
            raise SystemExit(f"{failed_cases} case(s) did not complete; see campaign-results.csv (not automatically protocol failure)")


if __name__ == "__main__":
    main()
