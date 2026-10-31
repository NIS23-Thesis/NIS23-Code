#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/run_scenario.sh <demo-name|json-path> [clive.py args...]" >&2
  echo "Demo names:" >&2
  find configs/demos -maxdepth 1 -name '*.json' -printf '  %f\n' | sed 's/\.json$//' | sort >&2
  exit 2
fi

NAME="$1"
shift
if [[ -f "$NAME" ]]; then
  SCENARIO="$NAME"
elif [[ -f "configs/demos/${NAME}.json" ]]; then
  SCENARIO="configs/demos/${NAME}.json"
else
  echo "Scenario not found: $NAME" >&2
  exit 2
fi

exec python3 clive.py --scenario "$SCENARIO" "$@"
