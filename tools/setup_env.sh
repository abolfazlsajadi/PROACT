#!/usr/bin/env bash
# Repeatable setup for this workspace; no shared-environment removal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "${PROACT_SETUP_PYTHON:-python3}" "$HERE/setup_env.py" "$@"
