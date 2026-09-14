"""Offline exports for completed native acquisition datasets.

The resumable ``*_traces.npy`` + ``*_meta.npz`` pair remains authoritative.
Exports contain only rows below the metadata ``done`` marker and are written to a
temporary file before an atomic replace, so a failed export cannot damage either
the native capture or an older completed export.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import numpy as np


FORMATS = ("native", "h5", "csv")
OPTIONAL_ROW_FIELDS = ("group", "block", "ts", "scope_trigger_index")
_COPY_TARGET_BYTES = 4 * 1024 * 1024
CSV_SCHEMA = "proact-acquisition-csv-v2"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION_RE = re.compile(r"[0-9a-f]{32}\Z")


def human_bytes(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or unit == "TB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024


def estimate_csv_bytes(rows: int, samples: int, out_len: int = 16) -> int:
    """Conservative estimate for raw-int16 wide CSV output."""
    # Each sample needs up to six characters plus a comma. Hex metadata, index,
    # scale, and line ending are small compared with the waveform columns.
    metadata = 32 + 32 + 2 * out_len + 96  # includes group/block/timestamp text
    return int(rows) * (int(samples) * 7 + metadata)


def estimate_h5_bytes(rows: int, samples: int, out_len: int = 16,
                      key_varies: bool = False) -> int:
    """Conservative uncompressed HDF5 payload estimate."""
    return int(rows) * (int(samples) * 2 + 16 + int(out_len) + 25 +
                        (16 if key_varies else 0)) + 64 * 1024


def estimate_selected_bytes(cfg, formats, samples: int, out_len: int) -> int:
    total = 0
    if "h5" in formats:
        total += estimate_h5_bytes(cfg.traces, samples, out_len,
                                   key_varies=cfg.key_policy == "random")
    if "csv" in formats:
        total += estimate_csv_bytes(cfg.traces, samples, out_len)
    return total


def _native(base: str):
    from .native import open_native
    meta = open_native(base)
    traces = meta.traces_all
    done, samples = meta.done, meta.samples
    for name in ("input", "out", "key"):
        if name not in meta.files:
            meta.close()
            raise ValueError(f"native metadata is missing {name!r}")
    if meta["input"].shape[0] < done or meta["out"].shape[0] < done:
        meta.close()
        raise ValueError("native metadata arrays are shorter than the done marker")
    return traces, meta, done, samples


def _file_sha256(path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _csv_binding(summary: dict) -> str:
    fields = {
        name: summary[name] for name in (
            "schema", "complete", "rows", "allocated_rows", "samples",
            "csv_file", "csv_file_bytes", "csv_file_sha256", "generation_id",
            "native_row_integrity",
        )
    }
    encoded = json.dumps(
        fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"PROACT CSV completion binding\x00v1\x00" + encoded).hexdigest()


def validate_csv_export(base: str) -> dict:
    """Validate that a CSV completion marker certifies the exact published file."""
    path = os.path.abspath(base + ".csv")
    sidecar = os.path.abspath(base + "_csv_meta.json")
    try:
        with open(sidecar, encoding="utf-8") as stream:
            summary = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid or missing CSV completion sidecar: {exc}") from exc
    if not isinstance(summary, dict) or summary.get("schema") != CSV_SCHEMA:
        raise ValueError("CSV completion sidecar uses an unsupported schema")
    for name in ("rows", "allocated_rows", "samples", "csv_file_bytes"):
        value = summary.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"CSV completion field {name!r} must be a non-negative integer")
    if not isinstance(summary.get("complete"), bool):
        raise ValueError("CSV completion field 'complete' must be boolean")
    if summary["rows"] > summary["allocated_rows"]:
        raise ValueError("CSV completion rows exceed allocated rows")
    if summary["complete"] != (summary["rows"] == summary["allocated_rows"]):
        raise ValueError("CSV completion flag disagrees with row counts")
    expected_name = Path(path).name
    filename = summary.get("csv_file")
    if not isinstance(filename, str) or filename != expected_name:
        raise ValueError(
            f"CSV completion sidecar names {filename!r}, expected {expected_name!r}")
    expected_hash = summary.get("csv_file_sha256")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise ValueError("CSV completion sidecar has an invalid SHA-256 value")
    generation = summary.get("generation_id")
    if not isinstance(generation, str) or not _GENERATION_RE.fullmatch(generation):
        raise ValueError("CSV completion sidecar has an invalid generation id")
    binding = summary.get("csv_binding_sha256")
    if not isinstance(binding, str) or not _SHA256_RE.fullmatch(binding):
        raise ValueError("CSV completion sidecar has an invalid binding digest")
    if binding != _csv_binding(summary):
        raise ValueError("CSV completion sidecar binding digest mismatch")
    try:
        observed_size, observed_hash = _file_sha256(path)
    except OSError as exc:
        raise ValueError(f"CSV completion file is missing or unreadable: {exc}") from exc
    if observed_size != summary["csv_file_bytes"]:
        raise ValueError(
            f"CSV completion byte count mismatch: {observed_size} != "
            f"{summary['csv_file_bytes']}")
    if observed_hash != expected_hash:
        raise ValueError("CSV completion SHA-256 mismatch")
    return summary


def _json_value(value):
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return _json_value(value.item())
        return [_json_value(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _metadata_summary(meta) -> dict:
    # Legacy datasets can carry duplicate per-row aliases (pt/ct as well as
    # input/out).  Never inspect those large arrays while building a compact
    # JSON attribute/sidecar; NativeDataset may expose them as lazy memmaps.
    large = {"input", "out", "pt", "ct", "key", *OPTIONAL_ROW_FIELDS}
    summary = {}
    for name in meta.files:
        if name in large:
            continue
        value = meta[name]
        if value.size <= 128:
            summary[name] = _json_value(value)
    return summary


def _waveform_metadata(meta) -> dict:
    unit = (str(meta["waveform_unit"].item())
            if "waveform_unit" in meta.files else "legacy_normalized_adc_code")
    instrument = {}
    if "instrument_metadata_json" in meta.files:
        instrument = json.loads(str(meta["instrument_metadata_json"].item()))
    return {
        "waveform_unit": unit,
        "scale_counts_per_capture_unit": float(meta["scale"]),
        "capture_unit_formula":
            "capture_unit = sample_raw / scale_counts_per_capture_unit",
        "instrument": instrument,
    }


def _optional_row_arrays(meta, done: int) -> dict:
    arrays = {}
    for name in OPTIONAL_ROW_FIELDS:
        if name not in meta.files:
            continue
        value = meta[name]
        if value.ndim != 1 or value.shape[0] < done:
            raise ValueError(
                f"native metadata field {name!r} must be a per-row vector")
        arrays[name] = value
    return arrays


def _rows_per_chunk(samples: int, done: int) -> int:
    row_bytes = max(1, int(samples) * np.dtype(np.int16).itemsize)
    return max(1, min(done, _COPY_TARGET_BYTES // row_bytes))


def ensure_dependencies(formats) -> None:
    if "h5" not in formats:
        return
    try:
        import h5py  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "HDF5 export needs h5py. Run ./install.sh, then retry the command.") from exc


def _export_h5_unlocked(base: str, note=None) -> str:
    """Stream valid native rows into a chunked, compressed HDF5 file."""
    ensure_dependencies(("h5",))
    import h5py

    path = base + ".h5"
    tmp = path + ".tmp"
    traces, meta, done, samples = _native(base)
    try:
        rows = _rows_per_chunk(samples, done) if done else 1
        if note:
            note(f"HDF5 export: {done:,} rows x {samples} samples -> {path}")
        with h5py.File(tmp, "w") as h5:
            h5.attrs["schema"] = "proact-acquisition-h5-v1"
            h5.attrs["done"] = done
            h5.attrs["n_alloc"] = int(meta["n_alloc"])
            h5.attrs["samples"] = samples
            waveform = _waveform_metadata(meta)
            h5.attrs["waveform_unit"] = waveform["waveform_unit"]
            h5.attrs["scale_counts_per_capture_unit"] = \
                waveform["scale_counts_per_capture_unit"]
            h5.attrs["capture_unit_conversion"] = waveform["capture_unit_formula"]
            h5.attrs["instrument_metadata_json"] = json.dumps(
                waveform["instrument"], sort_keys=True)
            h5.attrs["native_row_integrity_json"] = json.dumps(
                meta.row_integrity, sort_keys=True, separators=(",", ":"))
            h5.attrs["config_json"] = json.dumps(_metadata_summary(meta), sort_keys=True)

            trace_options = (dict(chunks=(rows, samples), compression="gzip",
                                  compression_opts=1, shuffle=True)
                             if done else {})
            trace_ds = h5.create_dataset(
                "traces", shape=(done, samples), dtype=np.int16, **trace_options)
            for start in range(0, done, rows):
                stop = min(done, start + rows)
                trace_ds[start:stop] = traces[start:stop]

            for source_name, dest_name in (("input", "input"), ("out", "output")):
                source = meta[source_name]
                width = source.shape[1]
                arr_rows = max(1, min(done or 1, _COPY_TARGET_BYTES // max(width, 1)))
                row_options = (dict(chunks=(arr_rows, width), compression="gzip",
                                    compression_opts=1, shuffle=True)
                               if done else {})
                dest = h5.create_dataset(
                    dest_name, shape=(done, width), dtype=source.dtype, **row_options)
                for start in range(0, done, arr_rows):
                    stop = min(done, start + arr_rows)
                    dest[start:stop] = source[start:stop]

            target = str(_json_value(meta["target"])) if "target" in meta.files else ""
            input_alias = "nonce" if target in ("xoodyak", "ascon") else "plaintext"
            h5[input_alias] = h5["input"]
            h5["ciphertext"] = h5["output"]

            key = meta["key"]
            if key.ndim == 1:
                h5.create_dataset("key", data=key)
            else:
                key_rows = max(1, min(done or 1, _COPY_TARGET_BYTES // key.shape[1]))
                key_options = (dict(chunks=(key_rows, key.shape[1]), compression="gzip",
                                    compression_opts=1, shuffle=True)
                               if done else {})
                key_ds = h5.create_dataset(
                    "key", shape=(done, key.shape[1]), dtype=key.dtype, **key_options)
                for start in range(0, done, key_rows):
                    stop = min(done, start + key_rows)
                    key_ds[start:stop] = key[start:stop]

            for name, source in _optional_row_arrays(meta, done).items():
                field_rows = max(1, min(done or 1, _COPY_TARGET_BYTES // source.dtype.itemsize))
                field_options = (dict(chunks=(field_rows,), compression="gzip",
                                      compression_opts=1, shuffle=True)
                                 if done else {})
                dest = h5.create_dataset(
                    name, shape=(done,), dtype=source.dtype, **field_options)
                for start in range(0, done, field_rows):
                    stop = min(done, start + field_rows)
                    dest[start:stop] = source[start:stop]
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    finally:
        meta.close()


def export_h5(base: str, note=None) -> str:
    from .locking import dataset_lock
    with dataset_lock(base, "HDF5 export"):
        return _export_h5_unlocked(base, note=note)


def _export_csv_unlocked(base: str, note=None) -> tuple[str, str]:
    """Stream valid rows to wide CSV plus a compact JSON metadata sidecar."""
    path = base + ".csv"
    sidecar = base + "_csv_meta.json"
    tmp = path + ".tmp"
    sidecar_tmp = sidecar + ".tmp"
    traces, meta, done, samples = _native(base)
    try:
        waveform = _waveform_metadata(meta)
        scale = waveform["scale_counts_per_capture_unit"]
        target = str(_json_value(meta["target"])) if "target" in meta.files else ""
        input_name = "nonce" if target in ("xoodyak", "ascon") else "plaintext"
        # NativeDataset derives this for old embedded datasets that predate the
        # explicit out_len scalar, without loading their full output matrix.
        estimate = estimate_csv_bytes(done, samples, int(meta.out_len))
        if note:
            note(
                f"CSV export warning: about {human_bytes(estimate)}, wide and slow; "
                "HDF5 is recommended for large analysis campaigns.")

        inp = meta["input"]
        out = meta["out"]
        key = meta["key"]
        optional = _optional_row_arrays(meta, done)
        header = ["trace_index", "key_hex", f"{input_name}_hex", "output_hex",
                  "scale_counts_per_capture_unit"]
        header.extend(optional)
        header.extend(f"sample_{i:04d}_raw" for i in range(samples))
        rows = _rows_per_chunk(samples, done) if done else 1
        with open(tmp, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(header)
            fixed_key = bytes(key).hex() if key.ndim == 1 else None
            for start in range(0, done, rows):
                stop = min(done, start + rows)
                block = traces[start:stop]
                for offset, trace in enumerate(block):
                    index = start + offset
                    key_hex = fixed_key or bytes(key[index]).hex()
                    row_fields = [_json_value(optional[name][index]) for name in optional]
                    writer.writerow([
                        index, key_hex, bytes(inp[index]).hex(), bytes(out[index]).hex(),
                        scale, *row_fields, *trace.tolist(),
                    ])
            stream.flush()
            os.fsync(stream.fileno())

        csv_file_bytes, csv_file_sha256 = _file_sha256(tmp)
        generation_id = uuid.uuid4().hex

        summary = {
            "schema": CSV_SCHEMA,
            "complete": done == int(meta["n_alloc"]),
            "rows": done,
            "allocated_rows": int(meta["n_alloc"]),
            "samples": samples,
            "trace_dtype": "int16",
            "waveform_unit": waveform["waveform_unit"],
            "scale_counts_per_capture_unit": scale,
            "capture_unit_conversion": waveform["capture_unit_formula"],
            "instrument_metadata": waveform["instrument"],
            "input_column": f"{input_name}_hex",
            "optional_row_columns": list(optional),
            "config": _metadata_summary(meta),
            "native_row_integrity": meta.row_integrity,
            "csv_file": Path(path).name,
            "csv_file_bytes": csv_file_bytes,
            "csv_file_sha256": csv_file_sha256,
            "generation_id": generation_id,
        }
        summary["csv_binding_sha256"] = _csv_binding(summary)
        with open(sidecar_tmp, "w", encoding="utf-8") as stream:
            json.dump(summary, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # The JSON sidecar is the completion marker for the CSV pair. Remove the
        # old marker immediately before publishing the new CSV; a crash can leave
        # an unmarked CSV, but never a marker that certifies a partial replacement.
        try:
            os.unlink(sidecar)
        except FileNotFoundError:
            pass
        os.replace(tmp, path)
        os.replace(sidecar_tmp, sidecar)
        return path, sidecar
    except Exception:
        for candidate in (tmp, sidecar_tmp):
            try:
                os.unlink(candidate)
            except FileNotFoundError:
                pass
        raise
    finally:
        meta.close()


def export_csv(base: str, note=None) -> tuple[str, str]:
    from .locking import dataset_lock
    with dataset_lock(base, "CSV export"):
        return _export_csv_unlocked(base, note=note)


def export_selected(base: str, formats, note=None) -> list[str]:
    """Export selected additional formats. ``native`` is an intentional no-op."""
    selected = tuple(dict.fromkeys(formats or ("native",)))
    unknown = set(selected) - set(FORMATS)
    if unknown:
        raise ValueError(f"unknown export format(s): {', '.join(sorted(unknown))}")
    ensure_dependencies(selected)
    outputs = []
    if "h5" in selected:
        outputs.append(export_h5(base, note=note))
    if "csv" in selected:
        outputs.extend(export_csv(base, note=note))
    return outputs
