#!/usr/bin/env python3
"""Compare fresh-process host imports against an explicitly selected local commit.

This opt-in synthetic benchmark never selects a historical baseline implicitly.
Use --baseline with a trusted local commit containing the host package.
"""
import argparse
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True,
                        help="trusted local Git commit/ref containing the host package")
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/startup_benchmark.json")
    args = parser.parse_args()
    if args.repeats < 3: parser.error("use at least 3 repeats")
    try:
        baseline = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", args.baseline + "^{commit}"],
            cwd=ROOT, text=True, stderr=subprocess.PIPE).strip()
        for path in ("Software/Python/proact_host/__init__.py", "config/hardware.json"):
            subprocess.check_output(["git", "cat-file", "-e", baseline + ":" + path],
                                    cwd=ROOT, stderr=subprocess.PIPE)
        archive = subprocess.check_output(
            ["git", "archive", baseline, "Software/Python", "config"],
            cwd=ROOT, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        parser.error("--baseline must resolve to a local commit containing the host package and config/hardware.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = {"baseline": [], "updated": []}
    with tempfile.TemporaryDirectory(prefix="startup_baseline_", dir=args.output.parent) as temporary:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # Only ordinary tracked files/directories are needed by this benchmark.
            # Reject links rather than extracting a baseline outside the scratch tree.
            members = tar.getmembers()
            if any(not (member.isfile() or member.isdir()) or
                   Path(member.name).is_absolute() or ".." in Path(member.name).parts
                   for member in members):
                parser.error("--baseline archive must contain only regular files and directories")
            tar.extractall(temporary, members=members)
        for repetition in range(args.repeats + 1):
            order = ("baseline", "updated") if repetition % 2 == 0 else ("updated", "baseline")
            for name in order:
                source = Path(temporary) if name == "baseline" else ROOT
                env = os.environ.copy()
                env.update(PYTHONPATH=str(source / "Software/Python"), PYTHONDONTWRITEBYTECODE="1",
                           OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
                start = time.perf_counter()
                subprocess.run([sys.executable, "-B", "-c", "import proact_host"], env=env,
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                elapsed = time.perf_counter() - start
                if repetition: results[name].append(elapsed)
    medians = {name: statistics.median(values) for name, values in results.items()}
    report = dict(python=sys.executable, baseline_commit=baseline, repeats=args.repeats,
                  procedure="Fresh processes, one warm-up pair excluded; alternating order; same runtime; warm filesystem cache; no devices.",
                  seconds=results, median_seconds=medians,
                  median_speedup=medians["baseline"] / medians["updated"])
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
