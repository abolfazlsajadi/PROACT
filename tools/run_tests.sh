#!/usr/bin/env bash
# Offline tests; explicit test paths replace the default testpaths selection.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
source "$HERE/tools/python_env.sh"
proact_select_python test "$HERE"
cd "$HERE"
export PYTHONDONTWRITEBYTECODE=1
exec "$PROACT_SELECTED_PYTHON" -m pytest "$@"
