#!/usr/bin/env python3
"""Run CLIVE's non-stress validation suite."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    "validate_configs.py",
    "validate_features.py",
    "validate_nodesocket.py",
    "validate_visualizer.py",
    "validate_clive.py",
]
for name in CHECKS:
    print(f"\n=== {name} ===", flush=True)
    subprocess.run([sys.executable, str(ROOT / "validation" / name)], cwd=ROOT, check=True)
print("\nALL CLIVE VALIDATIONS: PASS")
