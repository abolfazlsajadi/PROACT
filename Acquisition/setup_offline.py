#!/usr/bin/env python3
"""Install the offline configuration UI without choosing a shared bench environment."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
_INSPECT = """
from importlib import metadata, util
import json, sys
names = ('click', 'rich', 'questionary')
dependencies = []
for name in names:
    try:
        available = util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        available = False
    try:
        version = metadata.version(name)
    except metadata.PackageNotFoundError:
        version = None
    dependencies.append(dict(name=name, available=available, version=version))
print(json.dumps(dict(prefix=sys.prefix, base_prefix=sys.base_prefix,
                     python_version=list(sys.version_info[:3]),
                     dependencies=dependencies)))
"""


def _inspect(python):
    result = subprocess.run(
        [str(python), "-B", "-c", _INSPECT], check=True, capture_output=True,
        text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    return json.loads(result.stdout)


def _show(python, report):
    print(f"Offline UI Python: {python}")
    for item in report["dependencies"]:
        state = "available" if item["available"] else "MISSING"
        print(f"  {item['name']}: {state} ({item['version'] or 'version unavailable'})")
    print("Package discovery only; no instrument drivers or devices are used.")
    return all(item["available"] for item in report["dependencies"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report dependencies without creating, writing, or installing anything")
    parser.add_argument("--use-venv", metavar="DIR", type=Path,
                        help="explicit dedicated environment to create or reuse (default: .venv)")
    parser.add_argument("--no-install", action="store_true",
                        help="create or reuse an environment without installing UI dependencies")
    args = parser.parse_args(argv)
    explicit_python = os.environ.get("PROACT_ACQ_PYTHON", "").strip()
    if explicit_python and args.use_venv is not None:
        parser.error("choose either PROACT_ACQ_PYTHON or --use-venv DIR")

    destination = None if explicit_python else (
        args.use_venv if args.use_venv is not None else ROOT / ".venv"
    ).expanduser().absolute()
    python = explicit_python or str(destination / "bin" / "python")
    if destination is not None and destination.is_symlink():
        parser.error("the environment directory must not be a symlink to another environment")

    # This branch precedes every mkdir, environment creation, and pip call.
    # Legacy .venv_path files are deliberately never read or written.
    if args.check:
        try:
            report = _inspect(python)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"Cannot inspect offline UI interpreter {python}: {exc}", file=sys.stderr)
            return 1
        if tuple(report["python_version"]) < (3, 9):
            print("Python 3.9+ is required.", file=sys.stderr)
            return 1
        if destination is not None and Path(report["prefix"]).resolve() != destination.resolve():
            print("Selected interpreter belongs to a different environment.", file=sys.stderr)
            return 1
        return 0 if _show(python, report) else 1

    if destination is not None:
        if destination.exists() and not destination.is_dir():
            parser.error(f"environment path is not a directory: {destination}")
        existing = (destination / "pyvenv.cfg").is_file()
        if destination.exists() and not existing and any(destination.iterdir()):
            parser.error(f"refusing a nonempty directory without pyvenv.cfg: {destination}")
        if not existing:
            print(f"Creating dedicated offline UI environment: {destination}")
            # CPython otherwise creates lib64 -> lib even with --copies.
            # An unused compatibility directory also works on exFAT.
            if os.name == "posix" and sys.maxsize > 2**32:
                (destination / "lib64").mkdir(parents=True, exist_ok=True)
            venv.EnvBuilder(with_pip=True, symlinks=False,
                            system_site_packages=False).create(destination)
    try:
        report = _inspect(python)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(f"cannot inspect selected interpreter {python}: {exc}")
    if tuple(report["python_version"]) < (3, 9):
        parser.error("Python 3.9+ is required")
    if destination is not None and Path(report["prefix"]).resolve() != destination.resolve():
        parser.error("selected interpreter belongs to a different environment")
    if not args.no_install:
        if Path(report["prefix"]).resolve() == Path(report["base_prefix"]).resolve():
            parser.error("installation requires a virtual environment; use the default .venv setup")
        subprocess.run(
            [str(python), "-B", "-m", "pip", "install", "--disable-pip-version-check",
             "--no-user", "-r", str(ROOT / "requirements.txt")],
            check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        report = _inspect(python)
    available = _show(python, report)
    if explicit_python or args.use_venv is not None:
        print(f"Launch with PROACT_ACQ_PYTHON={shlex.quote(str(python))} ./run.sh")
    else:
        print("Launch with ./run.sh")
    if args.no_install:
        print("No dependency installation was performed.")
        return 0
    return 0 if available else 1


if __name__ == "__main__":
    raise SystemExit(main())
