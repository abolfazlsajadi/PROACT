#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/tools/python_env.sh"
proact_select_python cli "$HERE"
exec "$PROACT_SELECTED_PYTHON" -m proact_host.cli "$@"
