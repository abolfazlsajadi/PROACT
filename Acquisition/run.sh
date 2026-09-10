#!/usr/bin/env bash
# Launch only the offline configuration UI; no shared-environment fallback.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PY="${PROACT_ACQ_PYTHON:-$HERE/.venv/bin/python}"
if [[ -z "${PROACT_ACQ_PYTHON:-}" && -L "$HERE/.venv" ]]; then
  echo "The project .venv points to another environment; choose it explicitly with PROACT_ACQ_PYTHON." >&2
  exit 1
fi
if ! command -v "$PY" >/dev/null 2>&1; then
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: ./run.sh [offline configuration UI options]"
    echo "Set up with ./install.sh, or select Python with PROACT_ACQ_PYTHON."
    echo "This launcher does not provide instrument control or waveform acquisition."
    exit 0
  fi
  echo "Offline UI interpreter not found: $PY" >&2
  echo "Run ./install.sh or set PROACT_ACQ_PYTHON to an environment you choose." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
if ! "$PY" -B -c 'import click, rich, questionary' >/dev/null 2>&1; then
  echo "Offline UI dependencies are missing from: $PY" >&2
  echo "Run ./install.sh --check to inspect the selected environment." >&2
  exit 1
fi
exec "$PY" -B "$HERE/acquire.py" "$@"
