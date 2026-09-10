#!/usr/bin/env python3
"""Opt-in offline checkpoint benchmark against an explicit trusted local commit.

Every worker runs in a fresh process and writes only temporary synthetic data.
Reported write time includes construction, appends, compression and fsync;
readback validation follows timing and RSS measurement. No device API is used.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Software/Python/proact_host/storage.py"


def module_at(path):
    spec = importlib.util.spec_from_file_location("bench_storage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(args):
    import numpy as np
    module = module_at(args.source)
    suffix = {"npz": ".npz", "h5": ".h5", "tracepack": ".tracepack"}[args.backend]
    if args.backend == "h5" and not module._HAVE_H5:
        return {"skipped": "h5py unavailable"}
    rng = np.random.default_rng(args.seed)
    expected_hash = hashlib.sha256()
    max_buffered = 0
    checkpoints = []
    start_cpu = time.process_time()
    start = time.perf_counter()
    kwargs = {"chunk_rows": args.save_every} if args.backend == "tracepack" else {}
    store = module.TraceStore(str(Path(args.destination) / ("synthetic" + suffix)),
                              {"synthetic": True, "seed": args.seed}, **kwargs)
    for index in range(args.rows[0]):
        waveform = rng.standard_normal(args.samples, dtype=np.float32)
        plaintext = index.to_bytes(16, "little")
        output = bytes([(index + 1) % 256]) * 16
        expected_hash.update(waveform.tobytes())
        expected_hash.update(plaintext)
        expected_hash.update(output)
        store.append(waveform, plaintext, bytes(16), output, valid=True)
        max_buffered = max(max_buffered, getattr(store, "buffered_count", store.count))
        if (index + 1) % args.save_every == 0:
            checkpoint_start = time.perf_counter()
            store.flush()
            checkpoints.append(time.perf_counter() - checkpoint_start)
    store.close()
    wall_seconds = time.perf_counter() - start
    cpu_seconds = time.process_time() - start_cpu
    # Capture this before full readback; it measures writer-process peak RSS.
    peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        peak_rss_kib /= 1024
    paths = list(Path(store.path).rglob("*")) if Path(store.path).is_dir() else [Path(store.path)]
    bytes_on_disk = sum(path.stat().st_size for path in paths if path.is_file())
    verify_hash = hashlib.sha256()
    parts = module.iter_chunks(store.path) if args.backend == "tracepack" else [module.load(store.path)]
    verified_rows = 0
    for part in parts:
        count = part["traces"].shape[0]
        assert part["plaintext"].shape == part["output"].shape == (count, 16)
        assert part["key"].shape == (count, 16)
        assert np.all(part["key"] == 0) and np.all(part["valid"] == 1)
        for index in range(count):
            verify_hash.update(part["traces"][index].tobytes())
            verify_hash.update(part["plaintext"][index].tobytes())
            verify_hash.update(part["output"][index].tobytes())
        verified_rows += count
    assert verified_rows == args.rows[0]
    assert verify_hash.hexdigest() == expected_hash.hexdigest()
    return {"wall_seconds": wall_seconds, "cpu_seconds": cpu_seconds,
            "peak_writer_rss_kib": peak_rss_kib, "bytes_on_disk": bytes_on_disk,
            "max_buffered_rows_after_append": max_buffered,
            "buffered_rows_after_close": getattr(store, "buffered_count", store.count),
            "explicit_flush_seconds": checkpoints,
            "verified_rows": verified_rows, "verified_sha256": verify_hash.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, nargs="+", default=[2000, 8000])
    parser.add_argument("--samples", type=int, default=600)
    parser.add_argument("--save-every", type=int, default=250)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--baseline",
                        help="required for benchmark runs: trusted local Git commit/ref containing storage.py")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/storage_benchmark.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source", help=argparse.SUPPRESS)
    parser.add_argument("--destination", help=argparse.SUPPRESS)
    parser.add_argument("--backend", choices=["npz", "h5", "tracepack"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(*args.rows, args.samples, args.save_every, args.repeats) < 1:
        parser.error("row counts, samples, save interval and repeats must be positive")
    if args.worker:
        print(json.dumps(worker(args)))
        return
    if not args.baseline:
        parser.error("--baseline is required; choose a trusted local commit containing storage.py")
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", args.baseline + "^{commit}"],
            cwd=ROOT, text=True, stderr=subprocess.PIPE).strip()
        baseline = subprocess.check_output(
            ["git", "show", revision + ":Software/Python/proact_host/storage.py"],
            cwd=ROOT, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        parser.error("--baseline must resolve to a local commit containing Software/Python/proact_host/storage.py")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import numpy as np
    have_h5 = importlib.util.find_spec("h5py") is not None
    variants = [("baseline_npz", "npz", True), ("updated_npz", "npz", False),
                ("tracepack", "tracepack", False)]
    if have_h5:
        variants.extend([("baseline_h5", "h5", True), ("updated_h5", "h5", False)])
    updated_source = MODULE_PATH.read_bytes()
    report = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version, "platform": platform.platform(), "numpy": np.__version__,
        "h5py": __import__("h5py").__version__ if have_h5 else None,
        "workspace": str(ROOT), "baseline_commit": revision,
        "baseline_storage_sha256": hashlib.sha256(baseline).hexdigest(),
        "updated_storage_sha256": hashlib.sha256(updated_source).hexdigest(),
        "parameters": {"rows": args.rows, "samples": args.samples, "save_every": args.save_every,
                       "repeats": args.repeats, "seed": args.seed},
        "conditions": ["Synthetic Gaussian float32 waveforms, 16-byte payloads; no hardware.",
                       "Each sample is a fresh process; warm filesystem cache may persist.",
                       "Variant order rotates by repetition to reduce order effects.",
                       "Times include identical data generation and checksum bookkeeping.",
                       "Peak RSS is sampled before correctness readback, and includes Python/imports.",
                       "tracepack auto-flushes at save_every; unchanged subsequent explicit flush/close is a no-op.",
                       "Updated snapshots fsync files; baseline durability depends on the selected commit. No power-loss simulation.",
                       "Measurements are local observations, not guaranteed capture throughput."],
        "measurements": [], "summary": []}
    with tempfile.TemporaryDirectory(prefix="storage-benchmark-", dir=args.output.parent) as temporary:
        scratch = Path(temporary)
        baseline_path = scratch / "baseline_storage.py"
        baseline_path.write_bytes(baseline)
        updated_path = scratch / "updated_storage.py"
        updated_path.write_bytes(updated_source)
        for rows in args.rows:
            for repeat in range(args.repeats):
                order = variants[repeat % len(variants):] + variants[:repeat % len(variants)]
                for name, backend, is_baseline in order:
                    destination = scratch / f"{name}-{rows}-{repeat}"
                    destination.mkdir()
                    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--rows", str(rows),
                               "--samples", str(args.samples), "--save-every", str(args.save_every),
                               "--seed", str(args.seed), "--source", str(baseline_path if is_baseline else updated_path),
                               "--backend", backend, "--destination", str(destination)]
                    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
                    measurement = dict(json.loads(result.stdout), variant=name, rows=rows, repeat=repeat + 1)
                    report["measurements"].append(measurement)
                    print(f"{name:14} rows={rows:6} repeat={repeat+1} write={measurement['wall_seconds']:.3f}s "
                          f"RSS={measurement['peak_writer_rss_kib'] / 1024:.1f} MiB", flush=True)
    for rows in args.rows:
        for name, _, _ in variants:
            items = [item for item in report["measurements"] if item["rows"] == rows and item["variant"] == name]
            summary = {"variant": name, "rows": rows}
            for key in ("wall_seconds", "cpu_seconds", "peak_writer_rss_kib", "bytes_on_disk",
                        "max_buffered_rows_after_append", "buffered_rows_after_close"):
                values = [item[key] for item in items]
                summary[key] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
            report["summary"].append(summary)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
