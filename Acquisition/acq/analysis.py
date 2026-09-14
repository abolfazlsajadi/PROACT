"""Post-capture CPA and first-order TVLA on one exact native dataset.

The native ``*_traces.npy`` + ``*_meta.npz`` pair is authoritative.  Every
analysis respects the metadata ``done`` marker and never discovers or merges
other campaigns.  This keeps post-capture automation safe for million-row runs
and independent of optional HDF5/CSV exports.
"""
from __future__ import annotations

import csv
import contextlib
import hashlib
import io
import json
import os
import time
import uuid

import numpy as np

from .alignment import (RAW_VARIANT, SCOPE_ALIGNED_VARIANT, analysis_base,
                        normalize_trace_variant, trace_processing)


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_ROOT = os.path.join(PACKAGE_ROOT, "analysis_models")
SCA_LIB = os.path.join(MODEL_ROOT, "aes")
AEAD_LIB = os.path.join(MODEL_ROOT, "aead")
TVLA_THRESHOLD = 4.5


def ensure_dependencies(cfg):
    """Fail before instrument access if a selected model source is unavailable."""
    if not cfg.auto_cpa:
        return
    if cfg.target in ("aes1", "aes2", "sw_rv", "sw_rv_masked"):
        path = os.path.join(SCA_LIB, "proact_sca.py")
        if not os.path.exists(path):
            raise RuntimeError(f"AES CPA dependency not found: {path}")
    elif cfg.target in ("ascon", "xoodyak"):
        required = ("engine.py", "hd_ascon.py", "hd_xoodyak.py", "vecperm.py")
        missing = [name for name in required
                   if not os.path.exists(os.path.join(AEAD_LIB, name))]
        if missing:
            raise RuntimeError(
                "specialized AEAD CPA dependencies are unavailable: " +
                ", ".join(missing))
        try:
            import scipy  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("specialized AEAD CPA requires scipy") from exc
    else:
        raise RuntimeError(f"no automatic CPA model for target {cfg.target!r}")


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(_jsonable(value), stream, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")
    os.replace(tmp, path)


def _write_npz(path, **arrays):
    tmp = path[:-4] + ".tmp.npz" if path.endswith(".npz") else path + ".tmp.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)


def _start_publication(report_path):
    """Invalidate the old completion marker before replacing any result artifact."""
    try:
        os.unlink(report_path)
    except FileNotFoundError:
        pass
    return uuid.uuid4().hex


def _invalidate_variant_comparison(base, kind):
    """Remove a paired marker before replacing either report generation."""
    try:
        os.unlink(base + f"_{kind}_raw_vs_scope_aligned.json")
    except FileNotFoundError:
        pass


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publication_record(generation_id, artifacts):
    """Bind the final JSON marker to the exact artifacts from this generation."""
    return {
        "generation_id": generation_id,
        "completion_marker": "this JSON was atomically published after every artifact",
        "artifacts": {
            name: {
                "path": os.path.abspath(path),
                "bytes": os.path.getsize(path),
                "sha256": _file_sha256(path),
            }
            for name, path in artifacts.items()
        },
    }


def _require_trace_variation(trace_sum, trace_square_sum, n, label):
    """Reject a CPA input whose selected rows have no measurable variation."""
    count = int(n)
    if count < 2:
        raise ValueError(f"{label} needs at least two selected traces")
    mean = np.asarray(trace_sum, dtype=np.float64) / count
    second = np.asarray(trace_square_sum, dtype=np.float64) / count
    variance = second - mean * mean
    scale = max(1.0, float(np.nanmax(np.abs(second))))
    tolerance = np.finfo(np.float64).eps * scale * 64.0
    finite_variable = np.isfinite(variance) & (variance > tolerance)
    if not finite_variable.any():
        raise ValueError(
            f"{label} selected traces have zero or numerically negligible "
            "across-row variance; correlation and key ranking are undefined")
    return finite_variable


def _score_row_diagnostics(scores, truth):
    """Return tie-safe guesses, conservative ranks, and informative-row flags."""
    matrix = np.asarray(scores, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("score matrix must be two-dimensional")
    truth = np.asarray(truth, dtype=np.int64)
    if truth.shape != (matrix.shape[0],):
        raise ValueError("truth vector does not match score rows")
    finite = np.isfinite(matrix)
    safe = np.where(finite, matrix, -np.inf)
    guesses = safe.argmax(axis=1).astype(np.int64)
    best = safe[np.arange(len(safe)), guesses]
    tolerance = np.maximum(1e-15, np.abs(best) * 1e-12)
    best_ties = (
        finite & (np.abs(matrix - best[:, None]) <= tolerance[:, None])
    ).sum(axis=1)
    informative = np.isfinite(best) & (best > tolerance) & (best_ties == 1)

    true_score = safe[np.arange(len(safe)), truth]
    true_tolerance = np.maximum(1e-15, np.abs(true_score) * 1e-12)
    better = (
        finite & (matrix > true_score[:, None] + true_tolerance[:, None])
    ).sum(axis=1)
    tied_with_truth = (
        finite &
        (np.abs(matrix - true_score[:, None]) <= true_tolerance[:, None])
    ).sum(axis=1)
    ranks = better + tied_with_truth
    invalid_truth = ~np.isfinite(true_score) | (tied_with_truth == 0)
    ranks[invalid_truth] = matrix.shape[1]
    ranks = np.clip(ranks, 1, matrix.shape[1]).astype(np.int64)
    return guesses, ranks, informative, best_ties.astype(np.int64)


def _open_native(base, trace_variant=RAW_VARIANT):
    from .native import open_native
    dataset = open_native(base)
    for name in ("input", "out", "key"):
        if name not in dataset.files:
            dataset.close()
            raise ValueError(f"native dataset is missing {name!r}")
    try:
        traces, processing = trace_processing(
            dataset, dataset.traces_all, dataset.done, trace_variant)
    except Exception:
        dataset.close()
        raise
    return traces, dataset, dataset.done, dataset.samples, processing


def _native_row_provenance(dataset):
    """Return the verified authoritative-row identity embedded in reports."""
    return dict(dataset.row_integrity)


def _fixed_key(meta):
    key = np.asarray(meta["key"], dtype=np.uint8)
    if key.shape != (16,):
        raise ValueError("automatic CPA requires one fixed 16-byte key")
    return bytes(key)


def _selection(meta, done, random_group_only):
    if not random_group_only:
        return None, "all rows below done"
    if "group" not in meta.files:
        raise ValueError("TVLA+CPA requires recorded per-row group labels")
    group = np.asarray(meta["group"][:done], dtype=np.uint8)
    if not np.isin(group, (0, 1)).all():
        raise ValueError("TVLA group labels below done must contain only 0/1")
    return group == 1, "stored group==1 (random-input TVLA rows only)"


def _sample_coordinates(meta, samples):
    """Return sample/time/cycle axes, preferring instrument timing readback."""
    try:
        clock_mhz = float(meta["clock_mhz"])
        adc_mul = int(meta["adc_mul"])
        requested_offset = int(meta["offset"]) if "offset" in meta.files else 0
    except (KeyError, TypeError, ValueError):
        return None
    if not np.isfinite(clock_mhz) or clock_mhz <= 0 or adc_mul <= 0:
        return None
    instrument = {}
    if "instrument_metadata_json" in meta.files:
        try:
            instrument = json.loads(str(meta["instrument_metadata_json"].item()))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            instrument = {}
    nominal_interval_s = 1.0 / (clock_mhz * 1e6 * adc_mul)
    try:
        observed_interval_s = float(instrument["observed_sample_interval_s"])
    except (KeyError, TypeError, ValueError):
        observed_interval_s = None
    if (observed_interval_s is None or not np.isfinite(observed_interval_s) or
            observed_interval_s <= 0):
        sample_interval_s = nominal_interval_s
        interval_source = "requested_clock_times_adc_mul"
        observed_interval_s = None
    else:
        sample_interval_s = observed_interval_s
        interval_source = "instrument_readback"
    try:
        observed_offset = int(instrument["observed_offset_samples"])
        if observed_offset < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        observed_offset = None
    offset = observed_offset if observed_offset is not None else requested_offset
    offset_source = ("instrument_readback" if observed_offset is not None
                     else "capture_request")
    try:
        observed_sample_count = int(instrument["observed_sample_count"])
        if observed_sample_count <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        observed_sample_count = None
    sample = np.arange(samples, dtype=np.float64)
    adc_rate_mhz = 1e-6 / sample_interval_s
    target_cycles_per_sample = sample_interval_s * clock_mhz * 1e6
    backend = str(meta["backend"].item()) if "backend" in meta.files else "unknown"
    # Husky applies adc.offset. ScopeBackend currently accepts the setting but does
    # not issue a horizontal-delay command, so trigger-relative scope coordinates
    # would be false precision; retain only capture-start coordinates there.
    trigger_relative = backend != "scope"
    return {
        "sample": sample,
        "cycle_from_capture_start": sample * target_cycles_per_sample,
        "time_from_capture_start_ns": sample * sample_interval_s * 1e9,
        "cycle_from_requested_trigger": ((sample + offset) * target_cycles_per_sample
                                          if trigger_relative
                                          else np.full(samples, np.nan)),
        "time_from_requested_trigger_ns": (
            (sample + offset) * sample_interval_s * 1e9
            if trigger_relative else np.full(samples, np.nan)),
        "clock_mhz": clock_mhz,
        "adc_mul": adc_mul,
        "adc_rate_mhz": adc_rate_mhz,
        "nominal_adc_rate_mhz": clock_mhz * adc_mul,
        "sample_interval_s": sample_interval_s,
        "observed_sample_interval_s": observed_interval_s,
        "sample_interval_source": interval_source,
        "requested_offset_samples": requested_offset,
        "offset_samples": offset,
        "observed_offset_samples": observed_offset,
        "offset_source": offset_source,
        "observed_sample_count": observed_sample_count,
        "sample_count_matches_native": (
            observed_sample_count == samples if observed_sample_count is not None
            else None),
        "backend": backend,
        "trigger_relative_available": trigger_relative,
    }


def _coordinate_report(coords, sample):
    if coords is None or sample is None or int(sample) < 0:
        return None
    sample = int(sample)
    if sample >= len(coords["sample"]):
        return None
    trigger_cycle = float(coords["cycle_from_requested_trigger"][sample])
    trigger_time = float(coords["time_from_requested_trigger_ns"][sample])
    return {
        "sample": sample,
        "nominal_target_cycle_from_capture_start":
            float(coords["cycle_from_capture_start"][sample]),
        "nominal_time_from_capture_start_ns":
            float(coords["time_from_capture_start_ns"][sample]),
        "nominal_target_cycle_from_requested_trigger":
            trigger_cycle if np.isfinite(trigger_cycle) else None,
        "nominal_time_from_requested_trigger_ns":
            trigger_time if np.isfinite(trigger_time) else None,
        "coordinate_basis": coords["sample_interval_source"],
    }


def _sampling_report(coords):
    if coords is None:
        return {"available": False,
                "reason": "clock_mhz/adc_mul metadata is missing or invalid"}
    return {
        "available": True,
        "clock_mhz": coords["clock_mhz"],
        "adc_mul": coords["adc_mul"],
        "nominal_adc_rate_mhz": coords["nominal_adc_rate_mhz"],
        "effective_adc_rate_mhz": coords["adc_rate_mhz"],
        "effective_sample_interval_s": coords["sample_interval_s"],
        "observed_sample_interval_s": coords["observed_sample_interval_s"],
        "sample_interval_source": coords["sample_interval_source"],
        "requested_offset_samples": coords["requested_offset_samples"],
        "effective_offset_samples": coords["offset_samples"],
        "observed_offset_samples": coords["observed_offset_samples"],
        "offset_source": coords["offset_source"],
        "observed_sample_count": coords["observed_sample_count"],
        "sample_count_matches_native": coords["sample_count_matches_native"],
        "backend": coords["backend"],
        "trigger_relative_available": coords["trigger_relative_available"],
        "coordinate_status": (
            "sample spacing and offset prefer instrument readback when recorded; "
            "target-clock cycles still use recorded clock_mhz, and trigger latency "
            "is not measured"),
    }


def _welch_from_native(traces, group, done, chunk):
    samples = traces.shape[1]
    count = [0, 0]
    sums = [np.zeros(samples), np.zeros(samples)]
    sums2 = [np.zeros(samples), np.zeros(samples)]
    for start in range(0, done, chunk):
        stop = min(done, start + chunk)
        labels = group[start:stop]
        block = np.asarray(traces[start:stop], dtype=np.float64)
        for label in (0, 1):
            selected = block[labels == label]
            count[label] += selected.shape[0]
            if selected.size:
                sums[label] += selected.sum(axis=0)
                sums2[label] += np.einsum("ij,ij->j", selected, selected)
    if min(count) < 2:
        raise ValueError("TVLA needs at least two valid traces in each group")
    means = [sums[i] / count[i] for i in (0, 1)]
    variances = [
        np.maximum((sums2[i] - count[i] * means[i] * means[i]) /
                   (count[i] - 1), 0.0)
        for i in (0, 1)
    ]
    denominator = np.sqrt(variances[0] / count[0] + variances[1] / count[1])
    difference = means[0] - means[1]
    with np.errstate(divide="ignore", invalid="ignore"):
        t_curve = difference / denominator
    # 0/0 has no Welch statistic.  A nonzero mean difference divided by a zero
    # standard error is an infinite statistic and must remain an immediate
    # threshold crossing rather than being silently converted to a no-result NaN.
    t_curve[(denominator == 0) & (difference == 0)] = np.nan
    return t_curve, means, variances, count


def _tvla_protocol_diagnostics(group, blocks, done, chunk):
    """Validate labels and stream parity/block-balance diagnostics in O(chunk) RAM."""
    counts = [0, 0]
    even_counts = [0, 0]
    block_count = 0
    block_fraction_min = None
    block_fraction_max = None
    current_block = None
    current_total = current_fixed = 0

    def finish_block():
        nonlocal block_count, block_fraction_min, block_fraction_max
        nonlocal current_total, current_fixed
        if current_block is None:
            return
        if current_total <= 0:
            raise ValueError("TVLA block contains no rows")
        fraction = current_fixed / current_total
        block_count += 1
        block_fraction_min = (fraction if block_fraction_min is None
                              else min(block_fraction_min, fraction))
        block_fraction_max = (fraction if block_fraction_max is None
                              else max(block_fraction_max, fraction))

    step = max(1, int(chunk))
    for start in range(0, int(done), step):
        stop = min(int(done), start + step)
        labels = np.asarray(group[start:stop], dtype=np.uint8)
        if np.any(labels > 1):
            raise ValueError("TVLA group labels below done must contain only 0/1")
        parity = (np.arange(start, stop, dtype=np.int64) & 1) == 0
        for label in (0, 1):
            selected = labels == label
            counts[label] += int(np.count_nonzero(selected))
            even_counts[label] += int(np.count_nonzero(parity & selected))

        if blocks is None:
            continue
        block_values = np.asarray(blocks[start:stop], dtype=np.int64)
        if np.any(block_values < 0):
            raise ValueError("TVLA block labels below done must be non-negative")
        boundaries = np.flatnonzero(block_values[1:] != block_values[:-1]) + 1
        run_start = 0
        for run_stop in np.append(boundaries, len(block_values)):
            block_id = int(block_values[run_start])
            if current_block is None:
                current_block = block_id
            elif block_id != current_block:
                previous = current_block
                finish_block()
                if block_id <= previous:
                    raise ValueError("TVLA block labels must increase monotonically")
                current_block = block_id
                current_total = current_fixed = 0
            run_labels = labels[run_start:run_stop]
            current_total += len(run_labels)
            current_fixed += int(np.count_nonzero(run_labels == 0))
            run_start = int(run_stop)
    finish_block()
    if min(counts) == 0:
        frac_even = [float("nan"), float("nan")]
    else:
        frac_even = [even_counts[i] / counts[i] for i in (0, 1)]
    return {
        "counts": counts,
        "even_fraction": frac_even,
        "block_count": block_count,
        "block_fixed_fraction_min": block_fraction_min,
        "block_fixed_fraction_max": block_fraction_max,
    }


def _run_tvla_unlocked(base, chunk=50_000, note=None,
                       trace_variant=RAW_VARIANT):
    """Run first-order fixed-vs-random Welch TVLA and save NPZ/CSV/JSON."""
    trace_variant = normalize_trace_variant(trace_variant)
    traces, meta, done, samples, processing = _open_native(base, trace_variant)
    try:
        if "group" not in meta.files:
            raise ValueError("TVLA requires recorded per-row group labels")
        group = meta["group"]
        if note:
            note(f"TVLA [{trace_variant}]: streaming {done:,} native rows x "
                 f"{samples} samples")
        # A capture checkpoint can be millions of rows; it is not a safe analysis
        # allocation size. Bound the float64 trace block to about 64 MiB including
        # one selected-group copy.
        memory_rows = max(1, (64 * 1024 * 1024) // max(1, samples * 16))
        analysis_chunk = min(max(1, int(chunk)), memory_rows)
        blocks = meta["block"] if "block" in meta.files else None
        protocol = _tvla_protocol_diagnostics(
            group, blocks, done, analysis_chunk)
        t_curve, means, variances, counts = _welch_from_native(
            traces, group, done, analysis_chunk)
        if counts != protocol["counts"]:
            raise ValueError("TVLA group counts changed while the dataset was analyzed")
        if "tvla" in meta.files and bool(meta["tvla"]) and done == int(meta["n_alloc"]):
            requested = (int(meta["tvla_per_class"])
                         if "tvla_per_class" in meta.files else done // 2)
            if counts != [requested, requested]:
                raise ValueError(
                    f"completed TVLA group counts are {counts}, expected "
                    f"[{requested}, {requested}]")
            if protocol["block_count"] and (
                    protocol["block_fixed_fraction_min"] != 0.5 or
                    protocol["block_fixed_fraction_max"] != 0.5):
                raise ValueError("completed TVLA capture contains an imbalanced block")
            fixed_input = (np.asarray(meta["fixed_input"], dtype=np.uint8)
                           if "fixed_input" in meta.files else None)
            if fixed_input is not None and fixed_input.shape == (16,):
                inputs = meta["input"]
                for start in range(0, done, analysis_chunk):
                    stop = min(done, start + analysis_chunk)
                    labels = np.asarray(group[start:stop], dtype=np.uint8)
                    fixed_rows = np.asarray(inputs[start:stop])[labels == 0]
                    if len(fixed_rows) and np.any(fixed_rows != fixed_input):
                        raise ValueError(
                            "TVLA group 0 contains a row that differs from fixed_input")
        coords = _sample_coordinates(meta, samples)
        defined = ~np.isnan(t_curve)
        if not defined.any():
            raise ValueError(
                "TVLA is undefined at every sample; each fixed/random group needs "
                "finite within-group variance before a threshold claim can be made")
        peak_sample = int(np.nanargmax(np.abs(t_curve)))
        peak = float(abs(t_curve[peak_sample]))
        over = int(np.count_nonzero(np.abs(t_curve[defined]) > TVLA_THRESHOLD))
        infinite_samples = int(np.count_nonzero(np.isinf(t_curve)))

        frac_even = protocol["even_fraction"]
        parity_alias = abs(frac_even[0] - frac_even[1])
        order = str(meta["tvla_order"].item()) if "tvla_order" in meta.files else "unknown"
        confounded = bool(parity_alias >= 0.5)
        if confounded:
            interpretation = (
                "confounded: TVLA group is strongly aliased with trace-index parity; "
                "do not use this run alone for a leakage claim")
        elif peak > TVLA_THRESHOLD:
            interpretation = (
                "first-order fixed-vs-random difference detected; controls and "
                "independent replication are still required for a causal claim")
        else:
            interpretation = (
                "no first-order threshold crossing in this run; this does not prove "
                "absence of leakage")

        output_base = analysis_base(base, trace_variant)
        array_path = output_base + "_tvla_arrays.npz"
        csv_path = output_base + "_tvla_samples.csv"
        report_path = output_base + "_tvla.json"
        generation_id = _start_publication(report_path)
        _write_npz(
            array_path, sample=np.arange(samples, dtype=np.int64), t=t_curve,
            mean_fixed=means[0], mean_random=means[1],
            variance_fixed=variances[0], variance_random=variances[1],
            n_fixed=np.int64(counts[0]), n_random=np.int64(counts[1]),
            threshold=np.float64(TVLA_THRESHOLD),
            nominal_target_cycle_from_capture_start=(
                coords["cycle_from_capture_start"] if coords is not None
                else np.full(samples, np.nan)),
            nominal_time_from_capture_start_ns=(
                coords["time_from_capture_start_ns"] if coords is not None
                else np.full(samples, np.nan)),
            nominal_target_cycle_from_requested_trigger=(
                coords["cycle_from_requested_trigger"] if coords is not None
                else np.full(samples, np.nan)),
            nominal_time_from_requested_trigger_ns=(
                coords["time_from_requested_trigger_ns"] if coords is not None
                else np.full(samples, np.nan)))
        csv_tmp = csv_path + ".tmp"
        with open(csv_tmp, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([
                "sample", "nominal_target_cycle_from_capture_start",
                "nominal_time_from_capture_start_ns",
                "nominal_target_cycle_from_requested_trigger",
                "nominal_time_from_requested_trigger_ns", "welch_t",
                "mean_fixed_raw", "mean_random_raw", "variance_fixed_raw",
                "variance_random_raw"])
            for s in range(samples):
                if coords is None:
                    coordinate_values = ["", "", "", ""]
                else:
                    coordinate_values = [
                        coords["cycle_from_capture_start"][s],
                        coords["time_from_capture_start_ns"][s],
                        coords["cycle_from_requested_trigger"][s],
                        coords["time_from_requested_trigger_ns"][s]]
                writer.writerow([s, *coordinate_values, t_curve[s], means[0][s],
                                 means[1][s], variances[0][s], variances[1][s]])
        os.replace(csv_tmp, csv_path)
        publication = _publication_record(
            generation_id, {"arrays": array_path, "samples_csv": csv_path})
        report = {
            "schema": "proact-tvla-v1",
            "result_kind": "first_order_fixed_vs_random_welch_tvla",
            "source": {"native_base": os.path.abspath(base), "done": done,
                       "native_row_integrity": _native_row_provenance(meta),
                       "allocated": int(meta["n_alloc"]), "samples": samples,
                       "analysis_chunk_rows": analysis_chunk,
                       "analysis_chunk_budget_bytes": 64 * 1024 * 1024},
            "trace_preprocessing": processing,
            "groups": {"label_source": "stored metadata group",
                       "0": "fixed input", "1": "random input",
                       "n_fixed": counts[0], "n_random": counts[1],
                       "requested_per_class": int(meta["tvla_per_class"])
                       if "tvla_per_class" in meta.files else None,
                       "order": order},
            "threshold": TVLA_THRESHOLD,
            "max_abs_t": peak,
            "max_abs_t_is_infinite": bool(np.isinf(peak)),
            "peak_sample": peak_sample,
            "peak_coordinate": _coordinate_report(coords, peak_sample),
            "sampling_coordinates": _sampling_report(coords),
            "samples_over_threshold": over,
            "infinite_t_samples": infinite_samples,
            "parity": {"even_fraction_fixed": frac_even[0],
                       "even_fraction_random": frac_even[1],
                       "group_parity_alias": parity_alias,
                       "confounded": confounded},
            "blocks": {"count": protocol["block_count"],
                       "fixed_fraction_min":
                           protocol["block_fixed_fraction_min"],
                       "fixed_fraction_max":
                           protocol["block_fixed_fraction_max"]},
            "interpretation": interpretation,
            "minimum_trace_count_claimed": False,
            "publication": publication,
            "outputs": {"arrays": array_path, "samples_csv": csv_path,
                        "report": report_path},
        }
        _write_json(report_path, report)
        if note:
            note(f"TVLA [{trace_variant}] complete: max |t|={peak:.3f} at sample "
                 f"{peak_sample}; {interpretation}")
        return report
    finally:
        meta.close()


def run_tvla(base, chunk=50_000, note=None, *, trace_variant=RAW_VARIANT):
    from .locking import dataset_lock
    variant = normalize_trace_variant(trace_variant)
    with dataset_lock(base, f"TVLA analysis [{variant}]"):
        _invalidate_variant_comparison(base, "tvla")
        return _run_tvla_unlocked(
            base, chunk=chunk, note=note, trace_variant=variant)


def _load_aes_engine():
    if not os.path.exists(os.path.join(SCA_LIB, "proact_sca.py")):
        raise RuntimeError(f"AES CPA dependency not found: {SCA_LIB}/proact_sca.py")
    from analysis_models.aes.proact_sca import CPA, detection_floor, round10_key
    return CPA, detection_floor, round10_key


def _round0_from_round10(rk10):
    """Invert the AES-128 key schedule for a complete round-10 candidate."""
    from analysis_models.aes.proact_sca import RCON, SBOX
    words = [list(rk10[i:i + 4]) for i in range(0, 16, 4)]
    for round_index in range(9, -1, -1):
        c0, c1, c2, c3 = words
        p3 = [a ^ b for a, b in zip(c3, c2)]
        p2 = [a ^ b for a, b in zip(c2, c1)]
        p1 = [a ^ b for a, b in zip(c1, c0)]
        g = p3[1:] + p3[:1]
        g = [SBOX[x] for x in g]
        g[0] ^= RCON[round_index]
        p0 = [a ^ b for a, b in zip(c0, g)]
        words = [p0, p1, p2, p3]
    return bytes(sum(words, []))


def _run_aes_cpa(base, target, random_group_only=False, chunk=20_000, note=None,
                 trace_variant=RAW_VARIANT):
    CPA, detection_floor, round10_key = _load_aes_engine()
    profiles = {
        "aes1": dict(model="last_hd", ma=1, poi="key_free_shared"),
        "aes2": dict(model="last_hd", ma=1, poi="key_free_shared"),
        "sw_rv": dict(model="first_sbox", ma=2, poi="full_grid"),
        "sw_rv_masked": dict(model="first_sbox", ma=2, poi="full_grid"),
    }
    profile = profiles[target]
    trace_variant = normalize_trace_variant(trace_variant)
    traces, meta, done, samples, processing = _open_native(base, trace_variant)
    try:
        recorded_core = str(meta["target"].item())
        if recorded_core != target:
            raise ValueError(
                f"AES metadata core {recorded_core!r} != requested {target!r}")
        key = _fixed_key(meta)
        selected_mask, selection_note = _selection(meta, done, random_group_only)
        pt, ct = meta["input"], meta["out"]
        cpa = CPA(samples, profile["model"], ma=profile["ma"])
        selected_n = 0
        begun = time.monotonic()
        for start in range(0, done, max(1, int(chunk))):
            stop = min(done, start + max(1, int(chunk)))
            if selected_mask is None:
                rows = slice(start, stop)
                block_t = traces[rows]
                block_pt, block_ct = pt[rows], ct[rows]
            else:
                keep = selected_mask[start:stop]
                if not keep.any():
                    continue
                block_t = np.asarray(traces[start:stop])[keep]
                block_pt = np.asarray(pt[start:stop])[keep]
                block_ct = np.asarray(ct[start:stop])[keep]
            n_block = len(block_t)
            cpa.update(block_t, block_pt, block_ct, 0, n_block)
            selected_n += n_block
            if note and selected_n and selected_n % max(100_000, int(chunk) * 10) == 0:
                note(f"CPA {target} [{trace_variant}]: accumulated "
                     f"{selected_n:,} selected rows")
        if selected_n < 2:
            raise ValueError("automatic CPA needs at least two varying-input traces")
        if selected_mask is None:
            head = np.asarray(pt[:min(done, 2000)])
        else:
            head = np.asarray(pt[np.flatnonzero(selected_mask)[:2000]])
        if len(np.unique(head, axis=0)) < 2:
            raise ValueError("automatic CPA selected inputs do not vary")
        _require_trace_variation(
            cpa.St, cpa.St2, selected_n, f"CPA {target}")

        true_target = (round10_key(key) if profile["model"] == "last_hd" else key)
        poi_info = None
        poi = None
        poi_locked = False
        if profile["poi"] == "key_free_shared":
            floor = detection_floor(selected_n, samples)
            poi_info = cpa.find_poi(floor)
            poi_locked = bool(poi_info["z"] >= 6.0)
            poi = int(poi_info["poi"]) if poi_locked else None

        peak_by_guess = np.zeros((16, 256), np.float64)
        peak_sample_by_guess = np.zeros((16, 256), np.int32)
        full_grid_peak_rho_by_guess = np.zeros((16, 256), np.float64)
        full_grid_peak_sample_by_guess = np.zeros((16, 256), np.int32)
        rho_true = np.zeros(16, np.float64)
        for byte in range(16):
            rho = cpa.rho(byte)
            full_grid_peak_rho_by_guess[byte] = rho.max(axis=1)
            full_grid_peak_sample_by_guess[byte] = rho.argmax(axis=1)
            view = rho[:, poi:poi + 1] if poi is not None else rho
            peaks = view.max(axis=1)
            peak_by_guess[byte] = peaks
            peak_sample_by_guess[byte] = (
                np.full(256, poi, dtype=np.int32) if poi is not None
                else view.argmax(axis=1).astype(np.int32))
            rho_true[byte] = float(peaks[true_target[byte]])
        guesses_i, ranks_i, informative, best_tie_counts = _score_row_diagnostics(
            peak_by_guess, np.frombuffer(true_target, np.uint8))
        guesses = guesses_i.astype(np.uint8)
        ranks = ranks_i.astype(np.int16)
        correct = guesses == np.frombuffer(true_target, np.uint8)
        informative_correct = correct & informative
        recovered_hypothesis = bytes(guesses)
        candidate_master = (_round0_from_round10(recovered_hypothesis)
                            if profile["model"] == "last_hd"
                            else recovered_hypothesis)
        verify_indices = (np.arange(min(done, 3), dtype=np.int64)
                          if selected_mask is None
                          else np.flatnonzero(selected_mask)[:3])
        from . import paths as _paths  # noqa: F401 -- installs checked-out host path
        from proact_host.validation import aes128_encrypt_block
        candidate_matches = ([] if not informative.all() else [
            aes128_encrypt_block(candidate_master, bytes(np.asarray(pt[i], np.uint8))) ==
            bytes(np.asarray(ct[i], np.uint8))
            for i in verify_indices])
        blind_candidate_verified = bool(candidate_matches) and all(candidate_matches)

        output_base = analysis_base(base, trace_variant)
        arrays_path = output_base + "_cpa_arrays.npz"
        csv_path = output_base + "_cpa_bytes.csv"
        report_path = output_base + "_cpa.json"
        generation_id = _start_publication(report_path)
        coords = _sample_coordinates(meta, samples)
        selected_peak_samples = peak_sample_by_guess[
            np.arange(16), guesses].astype(np.int32)
        true_peak_samples = peak_sample_by_guess[
            np.arange(16), np.frombuffer(true_target, np.uint8)].astype(np.int32)
        _write_npz(
            arrays_path, peak_rho_by_guess=peak_by_guess, guess=guesses,
            peak_sample_by_guess=peak_sample_by_guess,
            full_grid_peak_rho_by_guess=full_grid_peak_rho_by_guess,
            full_grid_peak_sample_by_guess=full_grid_peak_sample_by_guess,
            selected_guess_peak_sample=selected_peak_samples,
            true_hypothesis_peak_sample=true_peak_samples,
            true_hypothesis=np.frombuffer(true_target, np.uint8), rank=ranks,
            correct=informative_correct, raw_top1_matches_truth=correct,
            informative=informative, best_tie_count=best_tie_counts,
            rho_true=rho_true,
            poi_score=(poi_info["score"] if poi_info else np.zeros(0)),
            selected_n=np.int64(selected_n))
        tmp = csv_path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["byte", "guess", "true_hypothesis", "rank",
                             "informative", "best_tie_count", "correct",
                             "rho_true", "rho_best",
                             "guess_peak_sample", "true_peak_sample",
                             "guess_peak_nominal_target_cycle",
                             "guess_peak_nominal_time_ns",
                             "true_peak_nominal_target_cycle",
                             "true_peak_nominal_time_ns"])
            for byte in range(16):
                guess_coord = _coordinate_report(coords, selected_peak_samples[byte])
                true_coord = _coordinate_report(coords, true_peak_samples[byte])
                writer.writerow([byte, int(guesses[byte]), true_target[byte],
                                 int(ranks[byte]), bool(informative[byte]),
                                 int(best_tie_counts[byte]),
                                 bool(informative_correct[byte]), rho_true[byte],
                                 float(peak_by_guess[byte].max()),
                                 int(selected_peak_samples[byte]),
                                 int(true_peak_samples[byte]),
                                 (guess_coord or {}).get(
                                     "nominal_target_cycle_from_requested_trigger", ""),
                                 (guess_coord or {}).get(
                                     "nominal_time_from_requested_trigger_ns", ""),
                                 (true_coord or {}).get(
                                     "nominal_target_cycle_from_requested_trigger", ""),
                                 (true_coord or {}).get(
                                     "nominal_time_from_requested_trigger_ns", "")])
        os.replace(tmp, csv_path)
        publication = _publication_record(
            generation_id, {"arrays": arrays_path, "bytes_csv": csv_path})
        report = {
            "schema": "proact-cpa-v1",
            "result_kind": "aes_key_hypothesis_evaluation",
            "core": target,
            "source": {"native_base": os.path.abspath(base), "done": done,
                       "native_row_integrity": _native_row_provenance(meta),
                       "selected_n": selected_n, "selection": selection_note,
                       "samples": samples},
            "trace_preprocessing": processing,
            "model": profile["model"],
            "model_description": (
                "HW(INV_SBOX[ct[b] xor k10] xor ct[ShiftRows[b]])"
                if profile["model"] == "last_hd"
                else "HW(SBOX[plaintext[b] xor key[b]])"),
            "hypothesis_key_round": "AES round 10" if profile["model"] == "last_hd"
                                    else "AES round 0",
            "moving_average": profile["ma"],
            "sampling_coordinates": _sampling_report(coords),
            "peak_samples": {
                "scoring_basis": ("locked key-free shared POI" if poi is not None
                                  else "full capture grid"),
                "selected_guess": selected_peak_samples.tolist(),
                "true_hypothesis": true_peak_samples.tolist(),
                "full_grid_locations_saved": True,
            },
            "poi": {"strategy": profile["poi"], "sample": poi,
                    "locked": poi_locked,
                    "z": float(poi_info["z"]) if poi_info else None,
                    "note": ("key-free POI did not reach z=6; full-grid result reported"
                             if poi_info and not poi_locked else None)},
            "hypothesis_selection_used_true_key": False,
            "true_key_used_for": ["post-selection correctness scoring"],
            "recovered_hypothesis": recovered_hypothesis.hex(),
            "candidate_master_key": candidate_master.hex(),
            "true_master_key": key.hex(),
            "informative_bytes": int(informative.sum()),
            "correct_bytes": int(informative_correct.sum()),
            "of_bytes": 16,
            "all_hypothesis_bytes_match_stored_ground_truth": bool(
                informative_correct.all()),
            "raw_top1_matches_stored_ground_truth": int(correct.sum()),
            "noninformative_byte_indices": np.flatnonzero(~informative).tolist(),
            "blind_candidate_verified": blind_candidate_verified,
            "blind_candidate_verification": {
                "rows_checked": len(candidate_matches),
                "row_indices": verify_indices.tolist(),
                "all_ciphertexts_matched": blind_candidate_verified,
                "uses_stored_true_key": False,
            },
            "all_bytes_recovered": bool(
                informative_correct.all() and blind_candidate_verified),
            "guessing_entropy_bits": float(np.log2(ranks.astype(np.float64)).sum()),
            "minimum_trace_count_claimed": False,
            "masked_first_order_diagnostic": (
                target == "sw_rv_masked"),
            "masking_caveat": (
                "This is a first-order unmasked S-box leakage diagnostic. "
                "Effective masking is expected to suppress this model; failure "
                "to recover is not proof of security, and no second-order "
                "recovery is run automatically."
                if target == "sw_rv_masked" else None),
            "elapsed_s": time.monotonic() - begun,
            "publication": publication,
            "outputs": {"arrays": arrays_path, "bytes_csv": csv_path,
                        "report": report_path},
        }
        _write_json(report_path, report)
        if note:
            note(f"CPA {target} [{trace_variant}]: "
                 f"{int(informative_correct.sum())}/16 informative hypothesis bytes at "
                 f"N={selected_n:,}; no minimum trace count claimed")
        return report
    finally:
        meta.close()


def _load_aead_models():
    required = ("engine.py", "hd_ascon.py", "hd_xoodyak.py", "vecperm.py")
    missing = [name for name in required if not os.path.exists(os.path.join(AEAD_LIB, name))]
    if missing:
        raise RuntimeError(
            "specialized AEAD CPA dependencies are unavailable: " + ", ".join(missing))
    from analysis_models.aead import engine as E
    from analysis_models.aead import hd_ascon as HA
    from analysis_models.aead import hd_xoodyak as HX
    return E, HA, HX


def _aead_accumulator_file(path, acc, key, window):
    snapshot = acc.snapshot()
    n = int(snapshot["n"])
    arrays = {"win": np.asarray(window, dtype=np.int64),
              "key": np.frombuffer(key, np.uint8),
              "snap_ns": np.asarray([n], dtype=np.int64)}
    arrays.update({f"s{n}_{name}": value for name, value in snapshot.items()})
    _write_npz(path, **arrays)


def _run_aead_cpa(base, target, random_group_only=False, note=None,
                  trace_variant=RAW_VARIANT):
    E, HA, HX = _load_aead_models()
    trace_variant = normalize_trace_variant(trace_variant)
    traces, native_meta, done, samples, processing = _open_native(
        base, trace_variant)
    try:
        key = _fixed_key(native_meta)
        selection_mask, selection_note = _selection(
            native_meta, done, random_group_only)
        recorded_core = str(native_meta["target"].item())
        if recorded_core != target:
            raise ValueError(
                f"AEAD metadata core {recorded_core!r} != requested {target!r}")
        nonce_all = native_meta["input"]
        ciphertext_all = native_meta["out"]
        if nonce_all.ndim != 2 or nonce_all.shape[0] < done or nonce_all.shape[1] != 16:
            raise ValueError("native AEAD input must be an (n_alloc,16) nonce array")
        from . import paths as _paths  # noqa: F401 -- installs checked-out host path
        from proact_host import aead_soft
        encrypt = (aead_soft.ascon128_encrypt if target == "ascon"
                   else aead_soft.xoodyak_encrypt)
        checked = min(20, done)
        matched = 0
        fixed_ad = bytes(16)
        fixed_message = bytes(16)
        for i in range(checked):
            ct, tag = encrypt(key, bytes(np.asarray(nonce_all[i], np.uint8)),
                              fixed_ad, fixed_message)
            matched += (ct + tag) == bytes(np.asarray(ciphertext_all[i], np.uint8))
        verified = matched == checked
        if not verified:
            raise ValueError(
                f"AEAD ciphertext reference failed: {matched}/{checked} rows match")
        backend = str(native_meta["backend"].item())
        triggercfg = int(native_meta["aead_trigger"])
        adc_mul = int(native_meta["adc_mul"])
        offset = int(native_meta["offset"])
        if backend != "husky" or triggercfg != 0x12 or adc_mul <= 0:
            raise ValueError(
                "specialized AEAD model window requires Husky, triggercfg 0x12, "
                "and a positive recorded adc_mul")
        # The validated [20,64) default at adc_mul=4 is target cycles [5,16).
        # Express it in cycles so other synchronous Husky ADC ratios do not move
        # the modeled operation; account for the recorded trigger offset.
        window_lo = max(0, int(round(5 * adc_mul - offset)))
        window_hi = min(samples, int(round(16 * adc_mul - offset)))
        if window_hi <= window_lo:
            raise ValueError(
                "recorded AEAD window does not contain validated target cycles [5,16)")
        window_samples = window_hi - window_lo
        if target == "ascon":
            if not HA.selftest():
                raise RuntimeError("ASCON positional model self-test failed")
            positions, classes_n, hypotheses = 64, 64, 64
            class_fn = lambda x: HA.classes(x).astype(np.int64)
            corr = E.Corr(positions * classes_n, window_samples)
            analysis_chunk = 1024
        else:
            if not HX.selftest(key=bytes(range(16)), n=64):
                raise RuntimeError("Xoodyak positional model self-test failed")
            positions, classes_n, hypotheses = 128, 16, 16
            class_fn = lambda x: E.xoo_hd_known_bits(x).reshape(
                x.shape[0], positions).astype(np.int64)
            corr = E.Corr(positions * classes_n, window_samples)
            analysis_chunk = 2048

        selected_n = 0
        nonce_probe = []
        begun = time.monotonic()
        for start in range(0, done, analysis_chunk):
            stop = min(done, start + analysis_chunk)
            nonce = np.ascontiguousarray(nonce_all[start:stop], dtype=np.uint8)
            block = np.ascontiguousarray(
                traces[start:stop, window_lo:window_hi], dtype=np.float32)
            if selection_mask is not None:
                keep = selection_mask[start:stop]
                nonce, block = nonce[keep], block[keep]
            if not len(block):
                continue
            if sum(len(x) for x in nonce_probe) < 1000:
                nonce_probe.append(nonce[:1000])
            classes = class_fn(nonce)
            model = np.zeros((len(block), positions * classes_n), np.float32)
            columns = (np.arange(positions) * classes_n)[None, :] + classes
            model[np.arange(len(block))[:, None], columns] = 1.0
            corr.update(model, block)
            selected_n += len(block)
            if note and selected_n and selected_n % 100_000 < len(block):
                note(f"CPA {target} [{trace_variant}]: accumulated "
                     f"{selected_n:,} selected rows")
        if selected_n < 2:
            raise ValueError("specialized AEAD CPA needs at least two selected rows")
        probe = np.concatenate(nonce_probe, axis=0)[:1000]
        if len(np.unique(probe, axis=0)) < 2:
            raise ValueError("selected AEAD nonces do not vary")
        _require_trace_variation(
            corr.st, corr.stt, selected_n, f"CPA {target}")

        output_base = analysis_base(base, trace_variant)
        accumulator_path = output_base + "_cpa_accumulator.npz"
        report_path = output_base + "_cpa.json"
        generation_id = _start_publication(report_path)
        _aead_accumulator_file(
            accumulator_path, corr, key, (window_lo, window_hi))
        # The legacy helpers print their raw argmax score before this adapter can
        # reject tied or noninformative rows.  Capture that diagnostic so CLI
        # output cannot claim false positional recovery when every score is tied.
        helper_stdout = io.StringIO()
        if target == "ascon":
            with contextlib.redirect_stdout(helper_stdout):
                results, _, ns = HA.score(accumulator_path)
            final = results[int(ns[-1])]
            true_hyp = HA.truth_hyp(key)
            joint = final["joint"]
            guess = np.asarray(joint["hb"], dtype=np.int16)
            score_matrix = np.asarray(joint["S"], dtype=np.float64)
            rank = (score_matrix > score_matrix[
                np.arange(positions), true_hyp][:, None]).sum(axis=1) + 1
            raw_binomial_p = float(joint["binom_p"])
            model_name = "ASCON round-1 x0 register-HD plus column-HW joint positional score"
            caveat = (
                "This positional model does not by itself identify the full 128-bit "
                "ASCON key; no automatic full-key decoder was run.")
        else:
            with contextlib.redirect_stdout(helper_stdout):
                results, _, ns = HX.score(accumulator_path)
            final = results[int(ns[-1])]
            true_hyp = HX.truth_hyp(key)
            guess = np.asarray(final["hb"], dtype=np.int16)
            score_matrix = np.asarray(final["S"], dtype=np.float64)
            rank = (score_matrix > score_matrix[
                np.arange(positions), true_hyp][:, None]).sum(axis=1) + 1
            raw_binomial_p = float(final["binom_p"])
            model_name = "Xoodyak round-1 three-bit register-HD joint positional score"
            caveat = (
                "The reported joint score is sqrt(sum of squared Pearson correlations), "
                "not one Pearson correlation; no automatic GF(2)/ISD decoder was run.")

        guess_i, rank_i, informative, best_tie_counts = _score_row_diagnostics(
            score_matrix, true_hyp)
        guess = guess_i.astype(np.int16)
        rank = rank_i.astype(np.int16)
        informative_correct = informative & (guess == true_hyp)
        correct_positions = int(np.count_nonzero(informative_correct))
        # The model helpers' binomial tail assumes every position had a unique,
        # meaningful top score.  Suppress it when any row is tied/noninformative.
        binomial_p = raw_binomial_p if informative.all() else None

        # Preserve where every hypothesis peaks so later crop/alignment work can
        # distinguish a correct-model peak from readout or nuisance activity.
        cnt = corr.sm.reshape(positions, classes_n)
        sums = corr.smt.reshape(positions, classes_n, -1)
        if target == "ascon":
            values = HA.build_tables()
            score_cube = (
                HA.rho_all(cnt, sums, corr.st, corr.stt, selected_n,
                           values[..., 0]) ** 2 +
                HA.rho_all(cnt, sums, corr.st, corr.stt, selected_n,
                           values[..., 1]) ** 2)
        else:
            values = HX.hd_value_tensor()
            score_cube = sum(
                HX.rho_all(cnt, sums, corr.st, corr.stt, selected_n,
                           values[..., component]) ** 2
                for component in range(3))
        peak_sample_by_guess = (score_cube.argmax(axis=2) + window_lo).astype(np.int32)
        peak_score_by_guess = score_cube.max(axis=2)
        del score_cube, values
        selected_peak_samples = peak_sample_by_guess[np.arange(positions), guess]
        true_peak_samples = peak_sample_by_guess[np.arange(positions), true_hyp]
        coords = _sample_coordinates(native_meta, samples)
        diagnostics_path = output_base + "_cpa_arrays.npz"
        _write_npz(
            diagnostics_path, guess=guess, true_hypothesis=true_hyp, rank=rank,
            peak_sample_by_guess=peak_sample_by_guess,
            peak_score_by_guess=peak_score_by_guess,
            selected_guess_peak_sample=selected_peak_samples,
            true_hypothesis_peak_sample=true_peak_samples,
            informative=informative, best_tie_count=best_tie_counts,
            selected_n=np.int64(selected_n),
            window=np.asarray([window_lo, window_hi], dtype=np.int64))

        coverage = np.asarray(corr.sm).reshape(positions, classes_n)
        position_path = output_base + "_cpa_positions.csv"
        tmp = position_path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([
                "position", "guess_hypothesis", "true_hypothesis", "rank",
                "informative", "best_tie_count", "correct",
                "guess_peak_sample", "true_peak_sample",
                "guess_peak_nominal_target_cycle", "guess_peak_nominal_time_ns",
                "true_peak_nominal_target_cycle", "true_peak_nominal_time_ns"])
            for position in range(positions):
                guess_coord = _coordinate_report(coords, selected_peak_samples[position])
                true_coord = _coordinate_report(coords, true_peak_samples[position])
                writer.writerow([position, int(guess[position]), int(true_hyp[position]),
                                 int(rank[position]), bool(informative[position]),
                                 int(best_tie_counts[position]),
                                 bool(informative_correct[position]),
                                 int(selected_peak_samples[position]),
                                 int(true_peak_samples[position]),
                                 (guess_coord or {}).get(
                                     "nominal_target_cycle_from_requested_trigger", ""),
                                 (guess_coord or {}).get(
                                     "nominal_time_from_requested_trigger_ns", ""),
                                 (true_coord or {}).get(
                                     "nominal_target_cycle_from_requested_trigger", ""),
                                 (true_coord or {}).get(
                                     "nominal_time_from_requested_trigger_ns", "")])
        os.replace(tmp, position_path)
        publication = _publication_record(
            generation_id, {
                "sufficient_statistics": accumulator_path,
                "diagnostic_arrays": diagnostics_path,
                "positions_csv": position_path,
            })
        report = {
            "schema": "proact-cpa-v1",
            "result_kind": "positional_hypothesis_evaluation",
            "core": target,
            "source": {"native_base": os.path.abspath(base), "done": done,
                       "native_row_integrity": _native_row_provenance(native_meta),
                       "selected_n": selected_n, "selection": selection_note,
                       "samples": samples, "window": [window_lo, window_hi],
                       "window_basis": (
                           "target cycles [5,16) relative to Husky triggercfg 0x12; "
                           "equivalent to samples [20,64) at adc_mul=4, offset=0")},
            "trace_preprocessing": processing,
            "model": model_name,
            "sampling_coordinates": _sampling_report(coords),
            "peak_samples": {
                "basis": "per-hypothesis maximum within the specialized model window",
                "selected_guess": selected_peak_samples.tolist(),
                "true_hypothesis": true_peak_samples.tolist(),
            },
            "positions": positions,
            "informative_positions": int(informative.sum()),
            "noninformative_position_indices": np.flatnonzero(~informative).tolist(),
            "hypotheses_per_position": hypotheses,
            "known_input_classes": classes_n,
            "class_coverage": {"minimum_rows": int(coverage.min()),
                               "maximum_rows": int(coverage.max()),
                               "empty_position_classes": int(np.count_nonzero(coverage == 0))},
            "positions_top1_correct": correct_positions,
            "positions_total": positions,
            "chance_top1_probability_per_position": 1.0 / hypotheses,
            "chance_expected_top1_positions": int(informative.sum()) / hypotheses,
            "heuristic_binomial_tail_under_independent_positions": binomial_p,
            "binomial_tail_assumption": (
                "positions are independent Bernoulli trials and every score row has "
                "a unique informative maximum; the value is omitted when that score "
                "condition fails, and is descriptive rather than a paper p-value"),
            "hypothesis_selection_used_true_key": False,
            "true_key_used_for": ["reference-output validation",
                                  "post-selection correctness scoring"],
            "model_selftest": True,
            "ciphertext_reference": {"matched": matched, "checked": checked,
                                     "all_matched": verified},
            "full_key_recovery_attempted": False,
            "full_key_recovered": None,
            "minimum_trace_count_claimed": False,
            "caveat": caveat,
            "elapsed_s": time.monotonic() - begun,
            "publication": publication,
            "outputs": {"sufficient_statistics": accumulator_path,
                        "diagnostic_arrays": diagnostics_path,
                        "positions_csv": position_path, "report": report_path},
        }
        _write_json(report_path, report)
        if note:
            note(f"CPA {target} [{trace_variant}]: {correct_positions}/{positions} "
                 "informative positional "
                 f"hypotheses ({int(informative.sum())} informative rows) at "
                 f"N={selected_n:,}; full-key recovery not attempted")
        return report
    finally:
        native_meta.close()


def _run_cpa_unlocked(base, target, random_group_only=False, note=None, *,
                      trace_variant=RAW_VARIANT):
    variant = normalize_trace_variant(trace_variant)
    if target in ("aes1", "aes2", "sw_rv", "sw_rv_masked"):
        return _run_aes_cpa(
            base, target, random_group_only, note=note,
            trace_variant=variant)
    if target in ("xoodyak", "ascon"):
        return _run_aead_cpa(
            base, target, random_group_only, note=note,
            trace_variant=variant)
    raise ValueError(f"no automatic CPA model for target {target!r}")


def run_cpa(base, target, random_group_only=False, note=None, *,
            trace_variant=RAW_VARIANT):
    from .locking import dataset_lock
    variant = normalize_trace_variant(trace_variant)
    with dataset_lock(base, f"CPA analysis {target} [{variant}]"):
        _invalidate_variant_comparison(base, "cpa")
        return _run_cpa_unlocked(
            base, target, random_group_only, note=note,
            trace_variant=variant)


def _comparison_metrics(report, kind):
    if kind == "tvla":
        return {
            "max_abs_t": report["max_abs_t"],
            "peak_sample": report["peak_sample"],
            "samples_over_threshold": report["samples_over_threshold"],
            "n_fixed": report["groups"]["n_fixed"],
            "n_random": report["groups"]["n_random"],
        }
    metrics = {
        "result_kind": report["result_kind"],
        "selected_n": report["source"]["selected_n"],
    }
    for name in ("correct_bytes", "informative_bytes", "all_bytes_recovered",
                 "positions_top1_correct", "informative_positions",
                 "full_key_recovered"):
        if name in report:
            metrics[name] = report[name]
    return metrics


def _published_report_binding(report):
    """Verify and bind a report marker and every artifact it publishes."""
    report_path = report["outputs"]["report"]
    with open(report_path, encoding="utf-8") as stream:
        published = json.load(stream)
    expected_generation = report["publication"]["generation_id"]
    if published.get("publication", {}).get("generation_id") != expected_generation:
        raise ValueError("analysis report generation changed before comparison")
    for field in ("schema", "result_kind", "source", "trace_preprocessing"):
        if published.get(field) != _jsonable(report.get(field)):
            raise ValueError(
                f"analysis report {field} changed before comparison")
    for artifact in published["publication"]["artifacts"].values():
        artifact_path = artifact["path"]
        if (not os.path.isfile(artifact_path) or
                os.path.getsize(artifact_path) != artifact["bytes"] or
                _file_sha256(artifact_path) != artifact["sha256"]):
            raise ValueError(
                "analysis artifact changed before raw/aligned comparison")
    return {
        "report": os.path.abspath(report_path),
        "report_sha256": _file_sha256(report_path),
        "generation_id": expected_generation,
    }


def _write_variant_comparison(base, kind, raw_report, aligned_report):
    """Bind raw and aligned reports from the same native dataset."""
    raw_binding = _published_report_binding(raw_report)
    aligned_binding = _published_report_binding(aligned_report)
    raw_rows = raw_report["source"].get("native_row_integrity", {})
    aligned_rows = aligned_report["source"].get("native_row_integrity", {})
    digest = raw_rows.get("sha256")
    same_native_rows = bool(
        raw_report["source"]["done"] == aligned_report["source"]["done"] and
        raw_rows == aligned_rows and raw_rows.get("digest_verified") is True and
        digest)
    path = base + f"_{kind}_raw_vs_scope_aligned.json"
    report = {
        "schema": "proact-raw-vs-scope-aligned-v1",
        "analysis": kind,
        "source": {
            "native_base": os.path.abspath(base),
            "done": raw_report["source"]["done"],
            "native_row_integrity": raw_rows,
            "same_native_rows": same_native_rows,
        },
        "raw": {
            **raw_binding,
            "trace_preprocessing": raw_report["trace_preprocessing"],
            "metrics": _comparison_metrics(raw_report, kind),
        },
        "scope_trigger_aligned": {
            **aligned_binding,
            "trace_preprocessing": aligned_report["trace_preprocessing"],
            "metrics": _comparison_metrics(aligned_report, kind),
        },
        "comparison_design": (
            "paired post-capture preprocessing comparison over identical native "
            "rows; the authoritative raw waveform file is never modified"),
        "minimum_trace_count_claimed": False,
    }
    if not report["source"]["same_native_rows"]:
        raise ValueError(
            "raw and aligned reports lack one matching verified native row digest")
    _write_json(path, report)
    return path


def run_requested(cfg, note=None, *, run_tvla_flag=None, run_cpa_flag=None,
                  capture_is_tvla=None):
    """Run the analyses selected in ``cfg`` and return their output paths."""
    outputs = []
    do_tvla = cfg.tvla if run_tvla_flag is None else bool(run_tvla_flag)
    do_cpa = cfg.auto_cpa if run_cpa_flag is None else bool(run_cpa_flag)
    grouped_capture = cfg.tvla if capture_is_tvla is None else bool(capture_is_tvla)
    variants = ([RAW_VARIANT, SCOPE_ALIGNED_VARIANT]
                if cfg.backend == "scope" else [RAW_VARIANT])
    from .locking import dataset_lock
    with dataset_lock(cfg.base, "requested post-capture analysis set"):
        if do_tvla:
            _invalidate_variant_comparison(cfg.base, "tvla")
            reports = []
            for variant in variants:
                report = _run_tvla_unlocked(
                    cfg.base, chunk=cfg.chunk, note=note, trace_variant=variant)
                reports.append(report)
                outputs.extend(report["outputs"].values())
            if len(reports) == 2:
                outputs.append(_write_variant_comparison(
                    cfg.base, "tvla", reports[0], reports[1]))
        if do_cpa:
            _invalidate_variant_comparison(cfg.base, "cpa")
            reports = []
            for variant in variants:
                report = _run_cpa_unlocked(
                    cfg.base, cfg.target, random_group_only=grouped_capture,
                    note=note, trace_variant=variant)
                reports.append(report)
                outputs.extend(report["outputs"].values())
            if len(reports) == 2:
                outputs.append(_write_variant_comparison(
                    cfg.base, "cpa", reports[0], reports[1]))
    return outputs
