#!/usr/bin/env bash
# Set up the offline configuration UI in a dedicated environment.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.9+ is required to run the offline setup helper." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
exec python3 -B "$HERE/setup_offline.py" "$@"
