#!/bin/sh
# install.sh -- set up the PROACT acquisition framework on any PC.
#
# Robust to two common problems:
#   * the project sits on a drive whose filesystem has NO symlink support
#     (exFAT/NTFS) -> `python -m venv` fails on its lib64 symlink. We create the
#     venv under $HOME instead (always symlink-capable) and record its path.
#   * an existing working venv is already present -> we reuse it and only install
#     what is missing (fast; avoids re-downloading chipwhisperer).
#
#   ./install.sh                 # reuse a working venv, else create one under $HOME
#   ./install.sh --use-venv DIR  # use an existing venv you name
#   ./install.sh --system        # install into the current python (no venv)
#   ./install.sh --check         # report what is missing, install nothing
set -u
SCRIPT_DIR=$(dirname "$0") || exit 1
HERE=$(CDPATH= cd "$SCRIPT_DIR" 2>/dev/null && pwd -P) || {
  echo "ERROR: cannot resolve the installer directory." >&2
  exit 1
}
CDPATH= cd "$HERE" || {
  echo "ERROR: cannot enter the installer directory: $HERE" >&2
  exit 1
}

usage() {
  echo "usage: ./install.sh [--system|--check|--use-venv DIR]"
}

MODE="venv"; USE_VENV=""
while [ $# -gt 0 ]; do
  case "$1" in
    --system) MODE="system" ;;
    --check)  MODE="check" ;;
    --use-venv)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "ERROR: --use-venv requires a directory." >&2
        usage >&2
        exit 2
      fi
      USE_VENV="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac; shift
done

BASEPY="$(command -v python3 || true)"
[ -z "$BASEPY" ] && { echo "ERROR: python3 not found. Install Python 3.10+."; exit 1; }
echo ">> base python: $($BASEPY --version 2>&1) at $BASEPY"

# libraries: "import_name pip_spec"
DEPS='numpy numpy>=1.21
scipy scipy>=1.7
h5py h5py>=3.7
serial pyserial>=3.5
chipwhisperer chipwhisperer>=6.0
mcp2210 mcp2210-python==1.0.4
hid hidapi>=0.14
pyvisa pyvisa>=1.16
pyvisa_py pyvisa-py>=0.8
rich rich>=13.0
questionary questionary>=2.0
click click>=8.0
tqdm tqdm>=4.60
colorama colorama>=0.4
prompt_toolkit prompt_toolkit>=3.0'
# does this python have the libs needed just to RUN the CLI?
runnable() { "$1" -c "import numpy,click,rich,questionary,tqdm" >/dev/null 2>&1; }

dependency_ok() {
  "$1" - "$2" "$3" >/dev/null 2>&1 <<'PY'
import importlib
import importlib.metadata
import sys

module_name, requirement_text = sys.argv[1:3]
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement

requirement = Requirement(requirement_text)
try:
    importlib.import_module(module_name)
    installed = importlib.metadata.version(requirement.name)
except (ImportError, importlib.metadata.PackageNotFoundError):
    raise SystemExit(1)
raise SystemExit(0 if installed in requirement.specifier else 1)
PY
}

pick_py() {
  # 1. explicit --use-venv
  if [ -n "$USE_VENV" ]; then echo "$USE_VENV/bin/python"; return; fi
  # 2. --system
  if [ "$MODE" = "system" ]; then echo "$BASEPY"; return; fi
  # 3. honor the interpreter chosen by an earlier install, including a custom
  #    --use-venv path outside the standard candidate directories.
  if [ -f "$HERE/.venv_path" ]; then
    recorded="$(cat "$HERE/.venv_path" 2>/dev/null)"
    if [ -x "$recorded" ] && runnable "$recorded"; then
      echo "$recorded"; return
    fi
  fi
  # 4. reuse an existing venv that already runs the CLI (fast path)
  for cand in "$HOME/.proact_acq_venv" "$HERE/.venv"; do
    if [ -x "$cand/bin/python" ] && runnable "$cand/bin/python"; then
      echo "$cand/bin/python"; return
    fi
  done
  # A read-only dependency check must never create a venv.  Use the base
  # interpreter to report what is available and leave the filesystem untouched.
  if [ "$MODE" = "check" ]; then echo "$BASEPY"; return; fi
  if ! "$BASEPY" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "ERROR: Python 3.10 or newer is required to create the environment." >&2
    return 1
  fi
  # 5. create a new venv. Prefer in-place, but if this filesystem has no symlink
  #    support (exFAT/NTFS), put it under $HOME which always does.
  vdir="$HERE/.venv"
  if ! ln -s .__t__ .__sp__ 2>/dev/null; then
    vdir="$HOME/.proact_acq_venv"
    echo ">> this folder's filesystem has no symlink support; venv -> $vdir" >&2
  else rm -f .__sp__ .__t__; fi
  echo ">> creating venv at $vdir" >&2
  if ! "$BASEPY" -m venv --copies "$vdir" >&2; then
    echo "ERROR: venv creation failed (need the python3-venv package?)." >&2; return 1
  fi
  echo "$vdir/bin/python"
}

PY="$(pick_py)" || exit 1
[ -x "$PY" ] || {
  echo "ERROR: configured Python is not executable: $PY" >&2
  exit 1
}
if "$PY" -c 'import sys' >/dev/null 2>&1; then
  if ! "$PY" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "ERROR: configured Python must be version 3.10 or newer: $PY" >&2
    exit 1
  fi
elif [ "$MODE" != "check" ]; then
  echo "ERROR: configured Python is not usable: $PY" >&2
  exit 1
fi
echo ">> using python: $PY"

if [ "$MODE" != "check" ] && [ -d "$HERE/.venv_path" ]; then
  echo "ERROR: could not record the selected interpreter: $HERE/.venv_path is a directory" >&2
  exit 1
fi

# install missing deps
MISSING=""; MISSING_COUNT=0; echo ">> checking libraries..."
while IFS=' ' read -r imp spec; do
  if dependency_ok "$PY" "$imp" "$spec"; then
    printf "   [ok]      %s (%s)\n" "$imp" "$spec"
  else
    printf "   [MISSING] %s -> %s\n" "$imp" "$spec"
    MISSING="$MISSING $spec"
    MISSING_COUNT=$((MISSING_COUNT + 1))
  fi
done <<EOF
$DEPS
EOF
if [ "$MISSING_COUNT" -eq 0 ]; then echo ">> all dependencies present."
elif [ "$MODE" = "check" ]; then
  echo ">> --check: $MISSING_COUNT required dependencies missing." >&2
  exit 1
else
  echo ">> installing $MISSING_COUNT package(s)..."
  "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
  # Dependency specifications contain no whitespace; intentional field splitting
  # passes each missing requirement as one pip argument under any POSIX shell.
  # shellcheck disable=SC2086
  "$PY" -m pip install $MISSING || {
    echo "ERROR: pip failed. Try: $PY -m pip install -r requirements.txt"; exit 1; }
fi

# Record the interpreter so run.sh finds it. --check is observational and must
# not change a previous selection or create a new configuration file.
if [ "$MODE" != "check" ]; then
  VENV_PATH_FILE="$HERE/.venv_path"
  VENV_PATH_TMP="$HERE/.venv_path.tmp.$$"
  if [ -d "$VENV_PATH_FILE" ] ||
     ! printf '%s\n' "$PY" > "$VENV_PATH_TMP" ||
     ! mv -f "$VENV_PATH_TMP" "$VENV_PATH_FILE"; then
    rm -f "$VENV_PATH_TMP"
    echo "ERROR: could not record the selected interpreter in $VENV_PATH_FILE" >&2
    exit 1
  fi
fi

# Verify the framework imports from this public checkout. Built firmware is
# checked only when a live campaign selects a path that needs it.
echo ">> verifying framework..."
if PROACT_REPO="${PROACT_REPO:-$HERE/..}" "$PY" -c "
import sys; sys.path.insert(0,'$HERE')
from acq.run import run
from acq.paths import REPO
print('   runtime:', REPO); print('   framework imports OK')"; then
  if [ "$MODE" = "check" ]; then
    echo ">> --check complete; no environment or configuration was changed."
  else
    echo ""; echo "=========================================================="
    echo " Setup complete.  Run:   ./run.sh"
    echo " (interpreter recorded in .venv_path -> $PY)"
    echo "=========================================================="
  fi
else
  echo "WARNING: framework import failed; run from a PROACT clone or set PROACT_REPO."; exit 1
fi
