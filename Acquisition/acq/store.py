"""Durable, resumable native storage with bounded-cost checkpoints.

The trace and every per-row field live in ``.npy`` memmaps. Checkpointing flushes
their dirty pages, then atomically replaces a small scalar ``_meta.npz`` containing
the authoritative ``done`` marker and a relative sidecar manifest. Its cost no
longer grows with the requested row count at every checkpoint.

Legacy metadata with embedded row arrays is migrated once before a partial capture
is resumed. Sidecars are committed first and metadata last, so the legacy file
remains authoritative if migration is interrupted.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import zipfile
from typing import Optional

import numpy as np

from .native import (
    ROW_DIGEST_SCHEMA, ROW_DIGEST_FIELDS, SCHEMA, capture_signature,
    exact_boolean_scalar, exact_integer_scalar, extend_row_digest,
    finish_row_digest, new_row_digest_state, normalized_signature, row_path,
    signature_json, validate_signature_scalars, verify_row_integrity,
)


SCALE = 32767.0 / 0.5
_ROW_ORDER = ("input", "out", "group", "block", "ts", "key",
              "scope_trigger_index")
_RESERVED_META = {
    "schema", "done", "n_alloc", "samples", "scale", "key", "input", "out",
    "group", "block", "ts", "scope_trigger_index", "pt", "ct", "out_len",
    "key_varies", "row_manifest_json",
    "capture_signature_json",
    "waveform_unit", "instrument_metadata_json",
    *ROW_DIGEST_FIELDS,
}
_TIMING_METADATA_FIELDS = {
    "requested_sample_count", "requested_sample_interval_s",
    "observed_sample_count", "observed_sample_interval_s",
    "observed_offset_samples", "timing_observation_source",
}
_DYNAMIC_SCOPE_METADATA_FIELDS = {
    # Sub-sample trigger phase can legitimately change on every acquisition.
    # Deterministic timebase reference/position and PT_OFF remain strict below.
    "scope_preamble_xorigin_s", "scope_preamble_xzero_s",
    "scope_trigger_index_from_preamble",
}


def encode_trace(trace_v):
    """Return the exact int16 representation committed to native storage."""
    return np.clip(trace_v * SCALE, -32768, 32767).astype(np.int16)


def trace_digest(trace_v):
    """Fingerprint a waveform in its durable, quantized representation."""
    encoded = np.ascontiguousarray(encode_trace(trace_v))
    return hashlib.blake2b(encoded.view(np.uint8), digest_size=8).digest()


def _instrument_metadata_compatible(stored, current, backend):
    if stored == current:
        return True
    # Historical Husky datasets predate explicit timing readback. Their immutable
    # capture signature already pins requested samples/offset/clock; permit a
    # one-time metadata upgrade only when none of the new observed fields existed.
    if backend == "husky" and not (_TIMING_METADATA_FIELDS & set(stored)):
        reduced = {k: v for k, v in current.items()
                   if k not in _TIMING_METADATA_FIELDS}
        return stored == reduced
    if backend == "scope":
        stable_stored = {k: v for k, v in stored.items()
                         if k not in _DYNAMIC_SCOPE_METADATA_FIELDS}
        stable_current = {k: v for k, v in current.items()
                          if k not in _DYNAMIC_SCOPE_METADATA_FIELDS}
        return stable_stored == stable_current
    return False


def _scalar(value):
    value = np.asarray(value)
    if value.ndim == 0:
        return value.item()
    return value.tolist()


def _same(a, b):
    if isinstance(b, bytes):
        return bytes(np.asarray(a, dtype=np.uint8)) == b
    if isinstance(b, float):
        try:
            return float(a) == b
        except (TypeError, ValueError):
            return False
    return _scalar(a) == b


def _atomic_array(path, dtype, shape, source=None, fill=None, valid_rows=None):
    """Write one complete NPY sidecar and publish it with an atomic replace."""
    tmp = path + ".migrate.tmp.npy"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    out = np.lib.format.open_memmap(tmp, mode="w+", dtype=dtype, shape=shape)
    rows = shape[0] if valid_rows is None else min(shape[0], int(valid_rows))
    if source is not None:
        step = 65_536
        for start in range(0, rows, step):
            stop = min(rows, start + step)
            out[start:stop] = source[start:stop]
    elif fill is not None:
        out[:rows] = fill
    out.flush()
    del out
    os.replace(tmp, path)


def _legacy_member_view(meta_path, field):
    """Memory-map an uncompressed NPY member inside a legacy NPZ."""
    member = field + ".npy"
    with zipfile.ZipFile(meta_path) as archive:
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_STORED:
            return None
    with open(meta_path, "rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30 or struct.unpack("<I", header[:4])[0] != 0x04034B50:
            return None
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        stream.seek(info.header_offset + 30 + name_len + extra_len)
        version = np.lib.format.read_magic(stream)
        shape, fortran, dtype = np.lib.format._read_array_header(stream, version)
        data_offset = stream.tell()
    return np.memmap(meta_path, dtype=dtype, mode="r", offset=data_offset,
                     shape=shape, order="F" if fortran else "C")


class TraceStore:
    def __init__(self, cfg, samples: int, out_len: int, key_varies: bool,
                 instrument_meta: Optional[dict] = None):
        self.cfg = cfg
        self.base = cfg.base
        self.tr_path = self.base + "_traces.npy"
        self.meta_path = self.base + "_meta.npz"
        self.S = exact_integer_scalar(np.asarray(samples), "samples")
        self.out_len = exact_integer_scalar(np.asarray(out_len), "out_len")
        self.key_varies = exact_boolean_scalar(
            np.asarray(key_varies), "key_varies")
        self.N = exact_integer_scalar(np.asarray(cfg.traces), "traces")
        self.instrument_meta = dict(instrument_meta or {
            "waveform_unit": "normalized_adc_code"})
        self.waveform_unit = str(
            self.instrument_meta.get("waveform_unit", "normalized_adc_code"))
        self.done = 0
        self.ts_kind = "elapsed_monotonic_seconds_excluding_session_gaps"
        self._alloc()

    def _specs(self):
        specs = {
            "input": (np.uint8, (self.N, 16)),
            "out": (np.uint8, (self.N, self.out_len)),
            "group": (np.uint8, (self.N,)),
            "block": (np.int64, (self.N,)),
            "ts": (np.float64, (self.N,)),
        }
        if self.key_varies:
            specs["key"] = (np.uint8, (self.N, 16))
        if self.cfg.backend == "scope":
            specs["scope_trigger_index"] = (np.float64, (self.N,))
        return specs

    def _validate_existing(self, old):
        try:
            validate_signature_scalars(old)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not np.isfinite(float(old["scale"])) or float(old["scale"]) != SCALE:
            raise SystemExit(
                f"resume scale mismatch: stored {float(old['scale'])}, expected {SCALE}")
        stored_n = exact_integer_scalar(old["n_alloc"], "n_alloc")
        stored_samples = exact_integer_scalar(old["samples"], "samples")
        stored_done = exact_integer_scalar(old["done"], "done")
        if stored_n != self.N or stored_samples != self.S:
            raise SystemExit(
                f"resume mismatch for {self.base}: existing n_alloc/samples="
                f"{stored_n}/{stored_samples} != requested "
                f"{self.N}/{self.S}. Use a new --suffix to keep both.")
        if "instrument_metadata_json" in old.files:
            try:
                stored_instrument = json.loads(
                    str(old["instrument_metadata_json"].item()))
            except Exception as exc:
                raise SystemExit(f"invalid stored instrument metadata: {exc}")
            if not _instrument_metadata_compatible(
                    stored_instrument, self.instrument_meta, self.cfg.backend):
                raise SystemExit(
                    "resume mismatch: instrument waveform calibration changed; "
                    "use a new --suffix")
        elif self.cfg.backend == "scope" and stored_done > 0:
            raise SystemExit(
                "cannot safely resume an older scope dataset without recorded "
                "waveform calibration; use a new --suffix")
        if self.cfg.backend == "scope" and stored_done > 0:
            manifest = (json.loads(str(old["row_manifest_json"].item()))
                        if "row_manifest_json" in old.files else {})
            if "scope_trigger_index" not in manifest:
                raise SystemExit(
                    "cannot safely resume a scope dataset without per-row trigger "
                    "phase; use a new --suffix")
        if "out_len" in old.files:
            stored_out_len = exact_integer_scalar(old["out_len"], "out_len")
        else:
            # Do not materialise a legacy (N,out_len) member merely to inspect
            # its width.  The historical writer used uncompressed np.savez, so
            # the member can normally be viewed directly inside the archive.
            out_view = _legacy_member_view(self.meta_path, "out")
            out_value = out_view if out_view is not None else old["out"]
            stored_out_len = int(out_value.shape[1])
            del out_value, out_view
        if "key_varies" in old.files:
            stored_key_varies = exact_boolean_scalar(old["key_varies"], "key_varies")
        else:
            key_view = _legacy_member_view(self.meta_path, "key")
            key_value = key_view if key_view is not None else old["key"]
            stored_key_varies = key_value.ndim == 2
            del key_value, key_view
        if stored_out_len != self.out_len:
            raise SystemExit("resume mismatch: output width changed; use a new --suffix")
        if stored_key_varies != self.key_varies:
            raise SystemExit("resume mismatch: key policy changed; use a new --suffix")
        if "capture_signature_json" in old.files:
            stored = str(old["capture_signature_json"].item())
            wanted = signature_json(self.cfg, self.S, self.out_len)
            if normalized_signature(stored) != normalized_signature(wanted):
                try:
                    have_d = normalized_signature(stored)
                    want_d = normalized_signature(wanted)
                    changed = [k for k in sorted(set(have_d) | set(want_d))
                               if have_d.get(k) != want_d.get(k)]
                except Exception:
                    changed = ["capture signature"]
                raise SystemExit(
                    "resume mismatch in immutable capture settings: " +
                    ", ".join(changed) + ". Use the original settings or a new --suffix.")
            return

        defaults = {"tvla": False, "tvla_per_class": 0, "tvla_order": "block",
                    "tvla_block_size": 100, "warmup": 0,
                    "scope_clock_source": "external", "scope_trig_level": 1.5}
        fields = {
            "target": self.cfg.target, "traces": self.N,
            "key_policy": self.cfg.key_policy,
            "input_policy": self.cfg.input_policy,
            "fixed_key": bytes(self.cfg.fixed_key),
            "fixed_input": bytes(self.cfg.fixed_input),
            "tvla": bool(self.cfg.tvla),
            "tvla_per_class": int(self.cfg.tvla_per_class),
            "tvla_order": self.cfg.tvla_order,
            "tvla_block_size": int(self.cfg.tvla_block_size),
            "warmup": int(self.cfg.warmup),
            "trigger_mode": self.cfg.trigger_mode,
            "aead_trigger": int(self.cfg.aead_trigger),
            "gain_db": float(self.cfg.gain_db),
            "clock_mhz": float(self.cfg.clock_mhz),
            "adc_mul": int(self.cfg.adc_mul),
            "offset": int(self.cfg.offset),
            "backend": self.cfg.backend,
            "seed": int(self.cfg.seed),
        }
        if self.cfg.backend == "scope":
            fields.update(
                scope_resource=str(self.cfg.scope_resource or ""),
                scope_dialect=self.cfg.scope_dialect,
                scope_trig_source=self.cfg.scope_trig_source,
                scope_trig_level=float(self.cfg.scope_trig_level),
                scope_channel=int(self.cfg.scope_channel),
                scope_clock_source=getattr(
                    self.cfg, "scope_clock_source", "external"))
        changed = []
        for name, wanted in fields.items():
            if name in old.files:
                if not _same(old[name], wanted):
                    changed.append(name)
            elif name in defaults and defaults[name] != wanted:
                changed.append(name)
        stored_key = np.asarray(old["key"], dtype=np.uint8)
        if not self.key_varies and (
                stored_key.shape != (16,) or bytes(stored_key) != bytes(self.cfg.fixed_key)):
            changed.append("stored fixed key")
        if changed:
            raise SystemExit(
                "resume mismatch in legacy capture settings: " +
                ", ".join(sorted(set(changed))) +
                ". Use the original settings or a new --suffix.")

    def _manifest(self):
        return {
            field: {"file": os.path.basename(row_path(self.base, field)),
                    "dtype": np.dtype(dtype).str, "shape": list(shape)}
            for field, (dtype, shape) in self._specs().items()
        }

    def _open_sidecars(self):
        manifest = self._manifest()
        arrays = {}
        for field, (_dtype, _shape) in self._specs().items():
            path = row_path(self.base, field)
            if not os.path.exists(path):
                raise SystemExit(f"resume sidecar missing: {path}")
            value = np.load(path, mmap_mode="r+")
            expected = manifest[field]
            if value.dtype.str != expected["dtype"] or list(value.shape) != expected["shape"]:
                raise SystemExit(
                    f"resume sidecar mismatch for {field}: {value.dtype}{value.shape}")
            arrays[field] = value
        self.INP, self.OUT = arrays["input"], arrays["out"]
        self.GRP, self.BLK, self.TS = arrays["group"], arrays["block"], arrays["ts"]
        self.SCOPE_TRIGGER_INDEX = arrays.get("scope_trigger_index")
        self.KEY = arrays.get(
            "key", np.frombuffer(bytes(self.cfg.fixed_key), np.uint8).copy())

    def _row_digest_arrays(self):
        arrays = {
            "trace": self.mm, "input": self.INP, "out": self.OUT,
            "group": self.GRP, "block": self.BLK, "ts": self.TS,
        }
        if self.key_varies:
            arrays["key"] = self.KEY
        if self.SCOPE_TRIGGER_INDEX is not None:
            arrays["scope_trigger_index"] = self.SCOPE_TRIGGER_INDEX
        return arrays

    def _initialize_unverified_row_integrity(self):
        self._row_digest_state = new_row_digest_state(self._row_digest_arrays())
        extend_row_digest(
            self._row_digest_state, self._row_digest_arrays(), 0, self.done)
        self._row_digest_unverified_prefix = self.done
        self.row_integrity = {
            "status": "unverified_legacy_baseline",
            "digest_verified": False,
            "digest_schema": None,
            "checkpoint_rows": self.done,
            "unverified_prefix_rows": self.done,
            "sha256": None,
        }

    def _verify_or_initialize_row_integrity(self, meta):
        try:
            info, state = verify_row_integrity(
                meta, self._row_digest_arrays(), self.done,
                prepare_unverified=True)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        self.row_integrity = info
        self._row_digest_state = state
        self._row_digest_unverified_prefix = info["unverified_prefix_rows"]

    def _checkpoint_meta(self, n, extra=None, preserved=None):
        digest = finish_row_digest(self._row_digest_state, n)
        status = (
            "verified_checkpoint_digest_with_unverified_legacy_prefix"
            if self._row_digest_unverified_prefix
            else "verified_checkpoint_digest")
        meta = dict(self.cfg.to_meta())
        if preserved:
            meta.update({k: v for k, v in preserved.items() if k not in _RESERVED_META})
        meta.update(
            schema=SCHEMA, done=int(n), n_alloc=self.N, samples=self.S,
            scale=SCALE, out_len=self.out_len, key_varies=self.key_varies,
            key=(np.asarray(self.KEY, dtype=np.uint8) if not self.key_varies
                 else np.frombuffer(bytes(self.cfg.fixed_key), np.uint8).copy()),
            row_manifest_json=json.dumps(self._manifest(), sort_keys=True),
            capture_signature_json=json.dumps(
                capture_signature(self.cfg, self.S, self.out_len),
                sort_keys=True, separators=(",", ":")),
            row_integrity_required=True,
            row_digest_schema=ROW_DIGEST_SCHEMA,
            row_digest_sha256=digest,
            row_digest_done=int(n),
            row_digest_unverified_prefix_rows=self._row_digest_unverified_prefix,
            row_integrity_status=status,
            ts_kind=self.ts_kind,
            group_labels="0=fixed,1=random,255=not_tvla")
        meta.update(
            waveform_unit=self.waveform_unit,
            instrument_metadata_json=json.dumps(
                self.instrument_meta, sort_keys=True, separators=(",", ":")))
        if extra:
            meta.update(extra)
        tmp = self.meta_path[:-len(".npz")] + ".tmp.npz"
        np.savez(tmp, **meta)
        os.replace(tmp, self.meta_path)
        self.row_integrity = {
            "status": status,
            "digest_verified": True,
            "digest_schema": ROW_DIGEST_SCHEMA,
            "checkpoint_rows": int(n),
            "unverified_prefix_rows": self._row_digest_unverified_prefix,
            "sha256": digest,
        }

    def _migrate_legacy(self, old):
        self.ts_kind = (
            "legacy_elapsed_seconds; timestamps may reset at unknown resume boundaries")
        preserved = {}
        for name in old.files:
            if name in _RESERVED_META:
                continue
            value = old[name]
            if value.size <= 128:
                preserved[name] = value.copy()
        for field, (dtype, shape) in self._specs().items():
            source = None
            if field in old.files:
                source = _legacy_member_view(self.meta_path, field)
                if source is None:             # compressed legacy fallback
                    source = old[field]
            fill = None
            if source is None:
                fill = 255 if field == "group" else (-1 if field == "block" else 0)
            _atomic_array(row_path(self.base, field), dtype, shape,
                          source=source, fill=fill, valid_rows=self.done)
        self._open_sidecars()
        self.mm = np.load(self.tr_path, mmap_mode="r+")
        if self.mm.dtype != np.int16 or self.mm.shape != (self.N, self.S):
            raise SystemExit("resume trace memmap dtype/shape mismatch")
        self._initialize_unverified_row_integrity()
        self._checkpoint_meta(self.done, preserved=preserved)

    def _fresh(self):
        _atomic_array(self.tr_path, np.int16, (self.N, self.S))
        for field, (dtype, shape) in self._specs().items():
            _atomic_array(row_path(self.base, field), dtype, shape)
        self.mm = np.load(self.tr_path, mmap_mode="r+")
        self._open_sidecars()
        self.done = 0
        self._row_digest_state = new_row_digest_state(self._row_digest_arrays())
        self._row_digest_unverified_prefix = 0
        self._checkpoint_meta(0)

    def _alloc(self):
        trace_exists = os.path.exists(self.tr_path)
        meta_exists = os.path.exists(self.meta_path)
        sidecars = [row_path(self.base, f) for f in _ROW_ORDER]
        if trace_exists != meta_exists:
            raise SystemExit(
                "incomplete native trace/metadata pair; refusing to recreate or "
                "truncate it. Recover the missing file or use a new --suffix.")
        if not trace_exists:
            leftovers = [p for p in sidecars if os.path.exists(p)]
            if leftovers:
                raise SystemExit(
                    "orphan native sidecars exist; refusing to overwrite: " +
                    ", ".join(leftovers))
            self._fresh()
        else:
            with np.load(self.meta_path, allow_pickle=True) as old:
                try:
                    self.done = exact_integer_scalar(old["done"], "done")
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
                if not 0 <= self.done <= self.N:
                    raise SystemExit(f"invalid resume done marker {self.done}/{self.N}")
                self._validate_existing(old)
                schema = (str(old["schema"].item())
                          if "schema" in old.files else "legacy-embedded-v1")
                if schema == SCHEMA:
                    if "ts_kind" in old.files:
                        self.ts_kind = str(old["ts_kind"].item())
                    stored_manifest = json.loads(str(old["row_manifest_json"].item()))
                    if stored_manifest != self._manifest():
                        raise SystemExit("resume sidecar manifest differs from requested layout")
                    self._open_sidecars()
                    self.mm = np.load(self.tr_path, mmap_mode="r+")
                    if self.mm.dtype != np.int16 or self.mm.shape != (self.N, self.S):
                        raise SystemExit("resume trace memmap dtype/shape mismatch")
                    self._verify_or_initialize_row_integrity(old)
                elif schema == "legacy-embedded-v1":
                    self._migrate_legacy(old)
                else:
                    raise SystemExit(f"unsupported resume storage schema {schema!r}")
        self._ts_offset = float(self.TS[self.done - 1]) if self.done else 0.0
        self._clock_origin = time.monotonic()

    def start_recording_session(self):
        self._ts_offset = float(self.TS[self.done - 1]) if self.done else 0.0
        self._clock_origin = time.monotonic()

    def last_trace_digest(self):
        """Fingerprint the last authoritative row, including across resume."""
        if not self.done:
            return None
        encoded = np.ascontiguousarray(self.mm[self.done - 1])
        return hashlib.blake2b(encoded.view(np.uint8), digest_size=8).digest()

    def append(self, i: int, trace_v: np.ndarray, key: bytes, inp: bytes, out: bytes,
               group: int = 255, block: int = -1, scope_trigger_index=None):
        try:
            index = exact_integer_scalar(np.asarray(i), "append index")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if index != self.done or not 0 <= index < self.N:
            raise ValueError(
                f"append index must equal authoritative done={self.done}, got {i}")
        self.mm[index] = encode_trace(trace_v)
        self.TS[index] = self._ts_offset + (time.monotonic() - self._clock_origin)
        self.GRP[index] = int(group)
        self.BLK[index] = int(block)
        self.INP[index] = list(inp)
        self.OUT[index] = list(out[:self.out_len])
        if self.SCOPE_TRIGGER_INDEX is not None:
            if scope_trigger_index is None or not np.isfinite(float(scope_trigger_index)):
                raise ValueError(
                    "scope capture is missing a finite per-row trigger index")
            self.SCOPE_TRIGGER_INDEX[index] = float(scope_trigger_index)
        if self.key_varies:
            self.KEY[index] = list(key)
        extend_row_digest(
            self._row_digest_state, self._row_digest_arrays(), index, index + 1)
        self.done = index + 1

    def checkpoint(self, n: int, extra: Optional[dict] = None):
        try:
            marker = exact_integer_scalar(np.asarray(n), "checkpoint marker")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if marker != self.done or not 0 <= marker <= self.N:
            raise ValueError(
                f"checkpoint marker must equal authoritative done={self.done}, got {n}")
        protected = _RESERVED_META | set(self.cfg.to_meta())
        conflicts = protected.intersection(extra or {})
        if conflicts:
            raise ValueError(
                "checkpoint extra cannot overwrite protected metadata: " +
                ", ".join(sorted(conflicts)))
        for value in (self.mm, self.INP, self.OUT, self.TS, self.GRP, self.BLK,
                      self.SCOPE_TRIGGER_INDEX):
            if value is None:
                continue
            value.flush()
        if self.key_varies:
            self.KEY.flush()
        self._checkpoint_meta(marker, extra=extra)

    def close(self):
        for value in (getattr(self, name, None) for name in
                      ("mm", "INP", "OUT", "TS", "GRP", "BLK", "KEY",
                       "SCOPE_TRIGGER_INDEX")):
            try:
                value.flush()
            except Exception:
                pass
