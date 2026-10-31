#!/usr/bin/env bash
# CLIVE Control Center — open in your browser without VS Code or editing JSON.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python3 clive/control_center.py --open "$@"
