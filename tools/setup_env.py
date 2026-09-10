#!/usr/bin/env python3
"""Create or reuse a workspace environment without deleting existing files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path,
                        default=Path(os.environ.get("PROACT_VENV", ROOT / ".venv")))
    parser.add_argument("--with", dest="extras", action="append", default=[],
                        choices=("gui", "hdf5", "capture", "dev", "all"),
                        help="optional packages; repeat for multiple groups")
    parser.add_argument("--system-site-packages", action="store_true",
                        help="inherit base Python packages when creating a new environment")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing or installing")
    parser.add_argument("--no-install", action="store_true", help="create/reuse the environment without pip/network")
    args = parser.parse_args(argv)
    destination = args.venv.expanduser().absolute()
    if destination.resolve() == (Path.home() / ".proact-venv").resolve():
        parser.error("use a project environment; choose --venv .venv to preserve the bench environment")
    if destination.is_symlink():
        parser.error("the environment directory must not be a symlink to another environment")
    existing = (destination / "pyvenv.cfg").is_file()
    if destination.exists() and not existing and any(destination.iterdir()):
        parser.error(f"refusing to use a nonempty directory without pyvenv.cfg: {destination}")
    python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirement = str(ROOT / "Software/Python")
    if args.extras:
        requirement += "[" + ",".join(sorted(set(args.extras))) + "]"
    install = [str(python), "-m", "pip", "install", "--no-user", "-e", requirement]
    plan = dict(environment=str(destination), action="reuse" if existing else "create",
                install_command=None if args.no_install else install,
                removes_existing_files=False, hardware_accessed=False)
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    print(f"{'Reusing' if existing else 'Creating'} {destination}")
    if not existing:
        # CPython still creates lib64 -> lib with --copies on 64-bit POSIX.
        # Precreating this unused compatibility directory supports exFAT/NTFS.
        if os.name == "posix" and sys.maxsize > 2**32:
            (destination / "lib64").mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, symlinks=False,
                        system_site_packages=args.system_site_packages).create(destination)
    if not python.is_file():
        parser.error(f"environment interpreter is missing: {python}")
    prefix = subprocess.check_output([str(python), "-c", "import sys; print(sys.prefix)"], text=True).strip()
    if Path(prefix).resolve() != destination.resolve():
        parser.error("environment interpreter resolves to a different environment")
    if not args.no_install:
        subprocess.run(install, check=True)
    print("Ready. Run ./run_cli.sh doctor, ./run_gui.sh, or ./tools/run_tests.sh.")
    print("Use PROACT_VENV for launchers if you chose a custom --venv path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
