#!/usr/bin/env bash
# CLIVE is localhost-only. This launches the default localhost scenario.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 clive.py --scenario localhost.json --results results
