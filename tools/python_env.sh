# Shared launcher policy. Sourced by the CLI, GUI and offline test runner.
proact_select_python() {
  local mode="$1" root="$2" check candidate
  local -a candidates
  export PYTHONPATH="$root/Software/Python${PYTHONPATH:+:$PYTHONPATH}"
  case "$mode" in
    cli) check='import proact_host' ;;
    gui) check='import proact_host; from PyQt6 import QtWidgets' ;;
    test) check='import proact_host, pytest, numpy' ;;
    *) echo "Unknown launcher mode: $mode" >&2; return 2 ;;
  esac
  if [[ -n "${PROACT_PYTHON:-}" ]]; then
    candidates=("$PROACT_PYTHON")
  elif [[ -n "${PROACT_VENV:-}" ]]; then
    candidates=("$PROACT_VENV/bin/python")
  else
    candidates=("$root/.venv/bin/python" "$HOME/.proact-venv/bin/python" python3 /usr/bin/python3)
  fi
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "$check" >/dev/null 2>&1; then
      PROACT_SELECTED_PYTHON="$candidate"
      return 0
    fi
  done
  echo "No suitable Python for $mode. Run: bash tools/setup_env.sh --with gui --with dev" >&2
  echo "PROACT_PYTHON or PROACT_VENV, when set, must point to a working environment." >&2
  return 1
}
