#!/bin/sh
# run.sh -- launch the acquire CLI using the interpreter install.sh recorded,
# never falling back to a bare python3 that lacks the dependencies.
SCRIPT_DIR=$(dirname "$0") || exit 1
HERE=$(CDPATH= cd "$SCRIPT_DIR" 2>/dev/null && pwd -P) || {
  echo "Cannot resolve the acquisition directory." >&2
  exit 1
}
# A normal public clone has proact_host one directory above Acquisition. A full
# design checkout can be selected explicitly when built firmware is required.
export PROACT_REPO="${PROACT_REPO:-$HERE/..}"

PY="${PROACT_ACQ_PYTHON:-}"
if [ -z "$PY" ] && [ -f "$HERE/.venv_path" ]; then
  PY="$(cat "$HERE/.venv_path" 2>/dev/null)"
fi
[ -x "$PY" ] || PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$HOME/.proact_acq_venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No configured interpreter. Run ./install.sh first." >&2
  exit 1
fi
# sanity: does it actually have the CLI deps?
if ! "$PY" -c "import click,rich,questionary" >/dev/null 2>&1; then
  echo "Interpreter '$PY' is missing dependencies. Run ./install.sh." >&2
  exit 1
fi
exec "$PY" "$HERE/acquire.py" "$@"
