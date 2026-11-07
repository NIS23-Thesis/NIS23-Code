#!/usr/bin/env python3
"""Run CLIVE's non-stress validation suite."""
from __future__ import annotations
import subprocess
import sys
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    "validate_configs.py",
    "validate_features.py",
    "validate_mobility_storage.py",
    "validate_optimizations.py",
    "validate_nodesocket.py",
    "validate_visualizer.py",
    "validate_visualizer_links.py",
    "validate_visualizer_second_pass.py",
    "validate_first_hello_and_traffic.py",
    "validate_connection_tracker.py",
    "validate_clive.py",
    "validate_flame_integration.py",
    "validate_control_center.py",
    "validate_memory_optimizations.py",
    "validate_integration_repair.py",
    "validate_event_stream_refactor.py",
    "validate_thesis_alignment.py",
    "validate_extended_model_metrics.py",
    "validate_simple_event_map.py",
    "validate_simple_event_map_js.js",
    "validate_clear_map_stages.js",
    "validate_guided_map.js",
    "validate_event_stream_js.js",
    "validate_connection_tracker_js.js",
]
for name in CHECKS:
    print(f"\n=== {name} ===", flush=True)
    executable = shutil.which('node') if name.endswith('.js') else sys.executable
    if executable is None:
        raise RuntimeError('Node.js is required for the event-stream frontend regression test')
    try:
        subprocess.run([executable, str(ROOT / "validation" / name)], cwd=ROOT, check=True, timeout=125)
    except subprocess.TimeoutExpired as error:
        raise SystemExit(f'{name}: exceeded 125-second validation timeout; not a PASS') from error
print("\nALL CLIVE VALIDATIONS: PASS")
