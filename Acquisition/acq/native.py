"""Authoritative native-dataset loader and v2 sidecar schema.

V2 keeps large per-row fields in ``.npy`` memmaps and the atomic metadata file
small.  Older datasets with arrays embedded in ``_meta.npz`` remain readable.
Only rows below ``done`` are authoritative in either schema.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import struct
import zipfile

import numpy as np

from .config import UART_DIVISOR, auto_uart_baud


SCHEMA = "proact-native-v2"
ROW_FIELDS = ("input", "out", "group", "block", "ts", "scope_trigger_index")
ROW_DIGEST_SCHEMA = "proact-native-row-sha256-v1"
ROW_DIGEST_FIELDS = (
    "row_integrity_required", "row_digest_schema", "row_digest_sha256",
    "row_digest_done", "row_digest_unverified_prefix_rows",
    "row_integrity_status",
)
_ROW_DIGEST_ORDER = (
    "trace", "input", "out", "group", "block", "ts", "key",
    "scope_trigger_index",
)
_ROW_DIGEST_DOMAIN = b"PROACT native authoritative rows\x00v1\x00"
_DIGEST_CHUNK_BYTES = 8 * 1024 * 1024

_INTEGRAL_METADATA_FIELDS = frozenset({
    "done", "n_alloc", "samples", "out_len", "traces", "chunk", "warmup",
    "tvla_per_class", "tvla_block_size", "aead_trigger", "uart_fixed_divisor",
    "uart_host_baud", "adc_mul", "offset", "scope_channel", "seed",
    "row_digest_done", "row_digest_unverified_prefix_rows",
})
_BOOLEAN_METADATA_FIELDS = frozenset({
    "key_varies", "auto_cpa", "tvla", "force_reset", "allow_nofit",
    "row_integrity_required",
})
_FAULT_COUNTER_FIELDS = frozenset({
    "fails", "recoveries", "quality_rejects", "clipped_rejects",
    "nonfinite_rejects", "constant_rejects", "stale_rejects",
    "instrument_rejects", "output_mismatches",
    "consecutive_quality_rejects", "quality_abort_threshold", "trig_count",
})
_INTEGRAL_METADATA_FIELDS = _INTEGRAL_METADATA_FIELDS | _FAULT_COUNTER_FIELDS
_FIRMWARE_IDENTITY_PIN_ATTR = "_proact_firmware_identity_pin"


def row_path(base, field):
    return f"{base}_{field}.npy"


def legacy_member_view(meta_path, field):
    """Memory-map one ZIP_STORED NPY member without materializing it."""
    member = field + ".npy"
    with zipfile.ZipFile(meta_path) as archive:
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_STORED:
            raise ValueError(
                f"compressed legacy row field {field!r} cannot be memory-mapped; "
                "resume it once to migrate to native v2 sidecars")
    with open(meta_path, "rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30 or struct.unpack("<I", header[:4])[0] != 0x04034B50:
            raise ValueError(f"invalid local ZIP header for legacy field {field!r}")
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        stream.seek(info.header_offset + 30 + name_len + extra_len)
        version = np.lib.format.read_magic(stream)
        shape, fortran, dtype = np.lib.format._read_array_header(stream, version)
        data_offset = stream.tell()
    return np.memmap(meta_path, dtype=dtype, mode="r", offset=data_offset,
                     shape=shape, order="F" if fortran else "C")


def _sha256(path):
    """Hash one stable file image and reject concurrent replacement/modification."""
    def identity(value):
        return (
            value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            opened_before = os.fstat(stream.fileno())
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            opened_after = os.fstat(stream.fileno())
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"required firmware artifact not found: {path}. Set PROACT_REPO or "
            "the matching PROACT_* firmware environment variable before capture.") \
            from exc
    path_after = os.stat(path)
    if (identity(opened_before) != identity(opened_after) or
            identity(opened_after) != identity(path_after)):
        raise RuntimeError(
            f"firmware host artifact changed while it was being hashed: {path}")
    return digest.hexdigest()


def _firmware_identity(target):
    from .paths import CONTROLLER_VMEM, SWRV_FW, SWRV_MASKED_FW
    paths = {"controller_vmem": CONTROLLER_VMEM}
    if target == "sw_rv":
        paths.update(
            sw_rv_imem=os.path.join(SWRV_FW, "sw_rv_imem.vmem"),
            sw_rv_dmem=os.path.join(SWRV_FW, "sw_rv_dmem.vmem"))
    elif target == "sw_rv_masked":
        paths.update(
            sw_rv_masked_imem=os.path.join(
                SWRV_MASKED_FW, "sw_rv_imem.vmem"),
            sw_rv_masked_dmem=os.path.join(
                SWRV_MASKED_FW, "sw_rv_dmem.vmem"))
    return {name: {"host_artifact_sha256": _sha256(path),
                   "basename": os.path.basename(path),
                   "status": "host file identity; not proof of code executing on silicon"}
            for name, path in paths.items()}


def verify_or_pin_firmware_identity(cfg):
    """Pin host firmware bytes to one config object and refuse later changes.

    Recovery paths re-read these files when they program the controller or reload
    Sw-RV memory.  A path-only hash cache could therefore let one dataset span two
    host firmware images while continuing to advertise the first image's digest.
    """
    current = _firmware_identity(cfg.target)
    pinned = getattr(cfg, _FIRMWARE_IDENTITY_PIN_ATTR, None)
    if pinned is None:
        pinned = copy.deepcopy(current)
        setattr(cfg, _FIRMWARE_IDENTITY_PIN_ATTR, pinned)
    elif current != pinned:
        changed = sorted(
            name for name in set(current) | set(pinned)
            if current.get(name) != pinned.get(name))
        raise RuntimeError(
            "firmware host artifact changed during this acquisition campaign: " +
            ", ".join(changed) + ". Restore the original files or use a new campaign.")
    return copy.deepcopy(pinned)


def capture_signature(cfg, samples, out_len):
    """Immutable capture settings used to refuse mixed-protocol resumes."""
    # Scope fields cannot affect a Husky capture. Canonicalize them to the
    # historical values so the new scope auto-detection UI does not strand an
    # in-progress Husky dataset made before that UI existed.
    if cfg.backend == "scope":
        scope_resource = str(cfg.scope_resource or "")
        scope_clock_source = cfg.scope_clock_source
        scope_dialect = cfg.scope_dialect
        scope_trig_source = cfg.scope_trig_source
        scope_trig_level = float(cfg.scope_trig_level)
        scope_channel = int(cfg.scope_channel)
    else:
        scope_resource = ""
        scope_clock_source = "husky"
        scope_dialect = "keysight"
        scope_trig_source = "EXTernal"
        scope_trig_level = 1.5
        scope_channel = 1
    signature = {
        "target": cfg.target,
        "traces": int(cfg.traces),
        "key_policy": cfg.key_policy,
        "input_policy": cfg.input_policy,
        "fixed_key_hex": bytes(cfg.fixed_key).hex(),
        "fixed_input_hex": bytes(cfg.fixed_input).hex(),
        "tvla": bool(cfg.tvla),
        "tvla_per_class": int(cfg.tvla_per_class),
        "tvla_order": cfg.tvla_order,
        "tvla_block_size": int(cfg.tvla_block_size),
        "warmup": int(cfg.warmup),
        "trigger_mode": cfg.trigger_mode,
        "aead_trigger": int(cfg.aead_trigger),
        "gain_db": float(cfg.gain_db),
        "clock_mhz": float(cfg.clock_mhz),
        "uart_fixed_divisor": UART_DIVISOR,
        "uart_host_baud": int(cfg.uart_host_baud),
        "adc_mul": int(cfg.adc_mul),
        "samples": int(samples),
        "offset": int(cfg.offset),
        "backend": cfg.backend,
        "scope_resource": scope_resource,
        "scope_clock_source": scope_clock_source,
        "scope_dialect": scope_dialect,
        "scope_trig_source": scope_trig_source,
        "scope_trig_level": scope_trig_level,
        "scope_channel": scope_channel,
        "seed": int(cfg.seed),
        "out_len": int(out_len),
        "firmware": verify_or_pin_firmware_identity(cfg),
    }
    if cfg.target == "sw_rv_masked":
        signature["target_rng_reseed_policy"] = (
            "host_os_csprng_nonzero_u32_before_every_operation")
    return signature


def normalized_signature(value):
    """Load a signature and supply UART fields absent from pre-integration v2 data."""
    data = json.loads(value) if isinstance(value, str) else dict(value)
    if "uart_fixed_divisor" not in data:
        data["uart_fixed_divisor"] = UART_DIVISOR
    if "uart_host_baud" not in data:
        # Before this field existed Target.open() always used 115200. Successful
        # archived runs were taken at the 50 MHz baseline; preserve their resume.
        clock_mhz = float(data.get("clock_mhz", 50.0))
        data["uart_host_baud"] = auto_uart_baud(clock_mhz)
    if "scope_clock_source" not in data:
        # Before this field existed ScopeBackend never configured a board clock;
        # any historical scope capture therefore depended on an external source.
        # Husky captures ignore the scope-only field and retain their canonical
        # value so old Husky datasets remain resumable.
        data["scope_clock_source"] = (
            "external" if data.get("backend") == "scope" else "husky")
    if "scope_trig_level" not in data:
        # The backend used a fixed 1.5 V threshold before this became a CLI field.
        data["scope_trig_level"] = 1.5
    return data


def signature_json(cfg, samples, out_len):
    return json.dumps(capture_signature(cfg, samples, out_len),
                      sort_keys=True, separators=(",", ":"))


def _meta_scalar(value):
    value = np.asarray(value)
    if value.ndim != 0:
        return value.tolist()
    return value.item()


def exact_integer_scalar(value, name):
    """Return an integer scalar without accepting floats, bools, or text."""
    value = np.asarray(value)
    if value.ndim != 0 or value.dtype.kind not in ("i", "u"):
        raise ValueError(f"native metadata {name!r} must be an integer scalar")
    return int(value.item())


def exact_boolean_scalar(value, name):
    """Return a boolean scalar without treating arbitrary values as truthy."""
    value = np.asarray(value)
    if value.ndim != 0 or value.dtype.kind != "b":
        raise ValueError(f"native metadata {name!r} must be a boolean scalar")
    return bool(value.item())


def _exact_text_scalar(value, name):
    value = np.asarray(value)
    if value.ndim != 0:
        raise ValueError(f"native metadata {name!r} must be a text scalar")
    item = value.item()
    if not isinstance(item, str):
        raise ValueError(f"native metadata {name!r} must be a text scalar")
    return item


def validate_metadata_scalar_types(meta):
    """Reject lossy coercions of every persisted integer/boolean setting."""
    files = set(meta.files)
    for name in sorted(_INTEGRAL_METADATA_FIELDS & files):
        exact_integer_scalar(meta[name], name)
    for name in sorted(_BOOLEAN_METADATA_FIELDS & files):
        exact_boolean_scalar(meta[name], name)
    for name in ("baud", "scope_transfer_timeout_ms"):
        if name not in files:
            continue
        value = np.asarray(meta[name])
        if value.ndim != 0:
            raise ValueError(
                f"native metadata {name!r} must be an integer or null scalar")
        if value.item() is not None:
            exact_integer_scalar(value, name)


def _row_digest_layout(arrays):
    required = {"trace", "input", "out", "group", "block", "ts"}
    names = tuple(name for name in _ROW_DIGEST_ORDER if name in arrays)
    unknown = set(arrays) - set(_ROW_DIGEST_ORDER)
    missing = required - set(names)
    if unknown or missing:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            detail.append("unknown " + ", ".join(sorted(unknown)))
        raise ValueError("invalid native row-digest layout: " + "; ".join(detail))
    first_lengths = {int(np.asarray(arrays[name]).shape[0]) for name in names
                     if np.asarray(arrays[name]).ndim >= 1}
    if len(first_lengths) != 1 or any(np.asarray(arrays[name]).ndim < 1 for name in names):
        raise ValueError("native row-digest arrays must share one row dimension")
    layout = [
        {"name": name, "dtype": np.asarray(arrays[name]).dtype.str,
         "shape": list(np.asarray(arrays[name]).shape)}
        for name in names
    ]
    encoded = json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return names, encoded


def new_row_digest_state(arrays):
    """Create an incremental digest state for the canonical sidecar layout."""
    names, layout = _row_digest_layout(arrays)
    hashers = {}
    for name in names:
        hasher = hashlib.sha256()
        hasher.update(_ROW_DIGEST_DOMAIN)
        hasher.update(b"field\x00")
        encoded_name = name.encode("ascii")
        hasher.update(struct.pack("<H", len(encoded_name)))
        hasher.update(encoded_name)
        hashers[name] = hasher
    return {
        "names": names, "layout": layout, "hashers": hashers, "done": 0,
        "array_ids": {name: id(arrays[name]) for name in names},
    }


def extend_row_digest(state, arrays, start, stop):
    """Append rows ``[start, stop)`` to a digest state without rereading its prefix."""
    start = exact_integer_scalar(np.asarray(start), "row digest start")
    stop = exact_integer_scalar(np.asarray(stop), "row digest stop")
    if start != state["done"] or stop < start:
        raise ValueError(
            f"row digest must extend its {state['done']} row prefix, got {start}:{stop}")
    names = state["names"]
    if (set(arrays) != set(names) or any(
            id(arrays[name]) != state["array_ids"].get(name) for name in names)):
        current_names, layout = _row_digest_layout(arrays)
        if current_names != names or layout != state["layout"]:
            raise ValueError("native row-digest layout changed while capturing")
        state["array_ids"] = {name: id(arrays[name]) for name in names}
    total = int(np.asarray(arrays[names[0]]).shape[0])
    if stop > total:
        raise ValueError(f"row digest stop {stop} exceeds allocation {total}")
    for name in names:
        value = np.asarray(arrays[name])
        row_bytes = max(1, int(value[0:1].nbytes)) if total else 1
        rows_per_chunk = max(1, _DIGEST_CHUNK_BYTES // row_bytes)
        hasher = state["hashers"][name]
        for offset in range(start, stop, rows_per_chunk):
            limit = min(stop, offset + rows_per_chunk)
            hasher.update(np.ascontiguousarray(value[offset:limit]).tobytes(order="C"))
    state["done"] = stop
    return state


def finish_row_digest(state, done):
    """Bind field digests, layout, and the authoritative checkpoint marker."""
    done = exact_integer_scalar(np.asarray(done), "row digest done")
    if done != state["done"]:
        raise ValueError(
            f"row digest covers {state['done']} rows but checkpoint requests {done}")
    hasher = hashlib.sha256()
    hasher.update(_ROW_DIGEST_DOMAIN)
    hasher.update(b"checkpoint\x00")
    hasher.update(struct.pack("<I", len(state["layout"])))
    hasher.update(state["layout"])
    hasher.update(struct.pack("<Q", done))
    for name in state["names"]:
        encoded_name = name.encode("ascii")
        hasher.update(struct.pack("<H", len(encoded_name)))
        hasher.update(encoded_name)
        hasher.update(state["hashers"][name].digest())
    return hasher.hexdigest()


def recompute_row_digest(arrays, done):
    state = new_row_digest_state(arrays)
    extend_row_digest(state, arrays, 0, done)
    return state, finish_row_digest(state, done)


def verify_row_integrity(meta, arrays, done, *, prepare_unverified=False):
    """Verify a checkpoint digest or identify a pre-digest dataset explicitly."""
    fields = set(ROW_DIGEST_FIELDS)
    present = fields & set(meta.files)
    if not present:
        state = recompute_row_digest(arrays, done)[0] if prepare_unverified else None
        return ({
            "status": "unverified_legacy_baseline",
            "digest_verified": False,
            "digest_schema": None,
            "checkpoint_rows": done,
            "unverified_prefix_rows": done,
            "sha256": None,
        }, state)
    if present != fields:
        missing = ", ".join(sorted(fields - present))
        raise ValueError(f"native row-integrity metadata is incomplete; missing {missing}")
    if not exact_boolean_scalar(
            meta["row_integrity_required"], "row_integrity_required"):
        raise ValueError("native row-integrity metadata cannot disable verification")
    schema = _exact_text_scalar(meta["row_digest_schema"], "row_digest_schema")
    if schema != ROW_DIGEST_SCHEMA:
        raise ValueError(f"unsupported native row digest schema {schema!r}")
    stored = _exact_text_scalar(meta["row_digest_sha256"], "row_digest_sha256")
    if (len(stored) != 64 or stored != stored.lower() or
            any(char not in "0123456789abcdef" for char in stored)):
        raise ValueError("native row digest is not a canonical SHA-256 value")
    digest_done = exact_integer_scalar(meta["row_digest_done"], "row_digest_done")
    prefix = exact_integer_scalar(
        meta["row_digest_unverified_prefix_rows"],
        "row_digest_unverified_prefix_rows")
    if digest_done != done:
        raise ValueError(
            f"native row digest marker {digest_done} disagrees with done {done}")
    if not 0 <= prefix <= done:
        raise ValueError(
            f"invalid native unverified-prefix marker {prefix}/{done}")
    expected_status = (
        "verified_checkpoint_digest_with_unverified_legacy_prefix"
        if prefix else "verified_checkpoint_digest")
    status = _exact_text_scalar(meta["row_integrity_status"], "row_integrity_status")
    if status != expected_status:
        raise ValueError(
            f"native row-integrity status {status!r} disagrees with prefix {prefix}")
    state, observed = recompute_row_digest(arrays, done)
    if not hmac.compare_digest(stored, observed):
        raise ValueError(
            "native row digest mismatch: an authoritative trace or row sidecar changed")
    return ({
        "status": status,
        "digest_verified": True,
        "digest_schema": schema,
        "checkpoint_rows": done,
        "unverified_prefix_rows": prefix,
        "sha256": stored,
    }, state)


def validate_signature_scalars(meta):
    """Reject v2 scalar metadata that contradicts its immutable signature."""
    validate_metadata_scalar_types(meta)
    files = set(meta.files)
    if "capture_signature_json" not in files:
        return
    try:
        signature = normalized_signature(
            str(meta["capture_signature_json"].item()))
    except Exception as exc:
        raise ValueError(f"invalid native capture signature: {exc}") from exc

    integral_signature_fields = {
        "traces", "tvla_per_class", "tvla_block_size", "warmup",
        "aead_trigger", "uart_fixed_divisor", "uart_host_baud", "adc_mul",
        "samples", "offset", "scope_channel", "seed", "out_len",
    }
    boolean_signature_fields = {"tvla"}
    converters = {
        "target": str, "traces": lambda value: value, "key_policy": str,
        "input_policy": str, "tvla": lambda value: value,
        "tvla_per_class": lambda value: value,
        "tvla_order": str, "tvla_block_size": lambda value: value,
        "warmup": lambda value: value,
        "trigger_mode": str, "aead_trigger": lambda value: value,
        "gain_db": float,
        "clock_mhz": float, "uart_fixed_divisor": lambda value: value,
        "uart_host_baud": lambda value: value, "adc_mul": lambda value: value,
        "samples": lambda value: value, "offset": lambda value: value,
        "backend": str,
        "scope_resource": lambda value: "" if value is None else str(value),
        "scope_clock_source": str, "scope_dialect": str,
        "scope_trig_source": str, "scope_trig_level": float,
        "scope_channel": lambda value: value, "seed": lambda value: value,
        "out_len": lambda value: value,
    }
    disagreements = []
    for name, convert in converters.items():
        if name not in signature or name not in files:
            continue
        # Husky signatures intentionally canonicalize scope-only fields so UI
        # defaults cannot strand an existing Husky capture. Those scalar fields
        # are irrelevant unless this is actually a scope dataset.
        if name.startswith("scope_") and signature.get("backend") != "scope":
            continue
        try:
            if name in integral_signature_fields and (
                    not isinstance(signature[name], int) or
                    isinstance(signature[name], bool)):
                raise TypeError
            if name in boolean_signature_fields and not isinstance(
                    signature[name], bool):
                raise TypeError
            observed = convert(_meta_scalar(meta[name]))
            expected = convert(signature[name])
        except Exception:
            disagreements.append(name)
            continue
        if observed != expected:
            disagreements.append(name)

    for scalar_name, signature_name in (("n_alloc", "traces"),):
        if scalar_name in files and signature_name in signature:
            expected = signature[signature_name]
            if (not isinstance(expected, int) or isinstance(expected, bool) or
                    exact_integer_scalar(meta[scalar_name], scalar_name) != expected):
                disagreements.append(scalar_name)

    for scalar_name, signature_name in (
            ("fixed_key", "fixed_key_hex"),
            ("fixed_input", "fixed_input_hex")):
        if scalar_name in files and signature_name in signature:
            try:
                value = np.asarray(meta[scalar_name], dtype=np.uint8)
                observed = bytes(value).hex() if value.shape == (16,) else None
            except Exception:
                observed = None
            if observed != str(signature[signature_name]):
                disagreements.append(scalar_name)

    if "key_varies" in files and "key_policy" in signature:
        if exact_boolean_scalar(meta["key_varies"], "key_varies") != (
                str(signature["key_policy"]) == "random"):
            disagreements.append("key_varies")
    if disagreements:
        raise ValueError(
            "native metadata scalars disagree with capture signature: " +
            ", ".join(sorted(set(disagreements))))


class NativeDataset:
    """Read-only view spanning legacy embedded fields and v2 sidecars."""

    def __init__(self, base):
        self.base = os.path.abspath(base)
        self.trace_path = self.base + "_traces.npy"
        self.meta_path = self.base + "_meta.npz"
        trace_exists = os.path.exists(self.trace_path)
        meta_exists = os.path.exists(self.meta_path)
        if trace_exists != meta_exists:
            missing = self.meta_path if trace_exists else self.trace_path
            raise ValueError(
                f"incomplete native pair; refusing to recreate or truncate: {missing}")
        if not trace_exists:
            raise FileNotFoundError(f"native dataset does not exist: {self.base}")
        self._meta = np.load(self.meta_path, allow_pickle=True)
        try:
            validate_metadata_scalar_types(self._meta)
            self.done = exact_integer_scalar(self._meta["done"], "done")
            self.n_alloc = exact_integer_scalar(self._meta["n_alloc"], "n_alloc")
            self.samples = exact_integer_scalar(self._meta["samples"], "samples")
            scale = float(self._meta["scale"])
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError(f"invalid native ADC scale {scale}")
            if "out_len" in self._meta.files:
                self.out_len = exact_integer_scalar(self._meta["out_len"], "out_len")
            else:
                # Older embedded metadata omitted out_len. Read only the stored
                # NPY member header/view; indexing NpzFile["out"] would allocate
                # the complete per-row output array.
                out_view = legacy_member_view(self.meta_path, "out")
                if out_view.ndim != 2:
                    raise ValueError("legacy out field must be two-dimensional")
                self.out_len = int(out_view.shape[1])
                del out_view
            if "key_varies" in self._meta.files:
                self.key_varies = exact_boolean_scalar(
                    self._meta["key_varies"], "key_varies")
            else:
                # The key member is 16 bytes for fixed-key captures and (N,16)
                # for varying-key captures. Inspect it in place for old schemas.
                key_view = legacy_member_view(self.meta_path, "key")
                self.key_varies = key_view.ndim == 2
                del key_view
            if not 0 <= self.done <= self.n_alloc:
                raise ValueError(f"invalid native done marker {self.done}/{self.n_alloc}")
            self.traces_all = np.load(self.trace_path, mmap_mode="r")
            if (self.traces_all.dtype != np.int16 or
                    self.traces_all.shape != (self.n_alloc, self.samples)):
                raise ValueError(
                    f"native trace array is {self.traces_all.dtype}"
                    f"{self.traces_all.shape}, expected int16"
                    f"{(self.n_alloc, self.samples)}")
            self.schema = (str(self._meta["schema"].item())
                           if "schema" in self._meta.files else "legacy-embedded-v1")
            if self.schema == SCHEMA:
                validate_signature_scalars(self._meta)
            self._rows = {}
            if self.schema == SCHEMA:
                self._open_sidecars()
                self.row_integrity, _unused = verify_row_integrity(
                    self._meta, self._row_digest_arrays(), self.done)
            elif self.schema == "legacy-embedded-v1":
                self._open_embedded()
                self.row_integrity = {
                    "status": "unverified_legacy_baseline",
                    "digest_verified": False,
                    "digest_schema": None,
                    "checkpoint_rows": self.done,
                    "unverified_prefix_rows": self.done,
                    "sha256": None,
                }
            else:
                raise ValueError(f"unsupported native storage schema {self.schema!r}")
            self.files = sorted(set(self._meta.files) | set(self._rows))
        except Exception:
            self.close()
            raise

    def _expected(self):
        expected = {
            "input": (np.dtype(np.uint8), (self.n_alloc, 16)),
            "out": (np.dtype(np.uint8), (self.n_alloc, self.out_len)),
            "group": (np.dtype(np.uint8), (self.n_alloc,)),
            "block": (np.dtype(np.int64), (self.n_alloc,)),
            "ts": (np.dtype(np.float64), (self.n_alloc,)),
        }
        if self.key_varies:
            expected["key"] = (np.dtype(np.uint8), (self.n_alloc, 16))
        return expected

    def _open_sidecars(self):
        if "row_manifest_json" not in self._meta.files:
            raise ValueError("v2 metadata is missing row_manifest_json")
        manifest = json.loads(str(self._meta["row_manifest_json"].item()))
        for field, (dtype, shape) in self._expected().items():
            entry = manifest.get(field)
            if not isinstance(entry, dict):
                raise ValueError(f"v2 metadata is missing sidecar manifest field {field}")
            filename = str(entry.get("file", ""))
            if not filename or filename != os.path.basename(filename):
                raise ValueError(f"unsafe/non-relative sidecar name for {field}: {filename!r}")
            expected_filename = os.path.basename(row_path(self.base, field))
            if filename != expected_filename:
                raise ValueError(
                    f"native {field} sidecar manifest names {filename!r}, "
                    f"expected {expected_filename!r}")
            path = os.path.join(os.path.dirname(self.meta_path), filename)
            if not os.path.exists(path):
                raise ValueError(f"native sidecar is missing: {path}")
            value = np.load(path, mmap_mode="r")
            if value.dtype != dtype or value.shape != shape:
                raise ValueError(
                    f"native {field} sidecar is {value.dtype}{value.shape}, "
                    f"expected {dtype}{shape}")
            if str(entry.get("dtype")) != dtype.str or tuple(entry.get("shape", ())) != shape:
                raise ValueError(f"native {field} sidecar disagrees with its manifest")
            self._rows[field] = value
        phase = manifest.get("scope_trigger_index")
        if phase is not None:
            dtype, shape = np.dtype(np.float64), (self.n_alloc,)
            filename = str(phase.get("file", "")) if isinstance(phase, dict) else ""
            expected_filename = os.path.basename(
                row_path(self.base, "scope_trigger_index"))
            if filename != expected_filename:
                raise ValueError(
                    "native scope_trigger_index sidecar manifest name is invalid")
            path = os.path.join(os.path.dirname(self.meta_path), filename)
            if not os.path.exists(path):
                raise ValueError(f"native sidecar is missing: {path}")
            value = np.load(path, mmap_mode="r")
            if (value.dtype != dtype or value.shape != shape or
                    str(phase.get("dtype")) != dtype.str or
                    tuple(phase.get("shape", ())) != shape):
                raise ValueError(
                    "native scope_trigger_index sidecar disagrees with its manifest")
            self._rows["scope_trigger_index"] = value
        if not self.key_varies:
            key = np.asarray(self._meta["key"], dtype=np.uint8)
            if key.shape != (16,):
                raise ValueError("fixed-key v2 metadata must store one 16-byte key")
            expected = None
            if "capture_signature_json" in self._meta.files:
                signature = normalized_signature(
                    str(self._meta["capture_signature_json"].item()))
                expected_hex = signature.get("fixed_key_hex")
                if expected_hex is not None:
                    try:
                        expected = bytes.fromhex(str(expected_hex))
                    except ValueError as exc:
                        raise ValueError(
                            "capture signature has invalid fixed_key_hex") from exc
            elif "fixed_key" in self._meta.files:
                expected = bytes(np.asarray(self._meta["fixed_key"], dtype=np.uint8))
            if expected is not None and bytes(key) != expected:
                raise ValueError(
                    "stored fixed key disagrees with capture signature/fixed_key metadata")

    def _open_embedded(self):
        expected = self._expected()
        required = {"input", "out"} | ({"key"} if self.key_varies else set())
        for field, (dtype, shape) in expected.items():
            if field not in self._meta.files:
                if field in required:
                    raise ValueError(f"legacy native metadata is missing {field!r}")
                continue
            value = legacy_member_view(self.meta_path, field)
            if value.dtype != dtype or value.shape != shape:
                raise ValueError(
                    f"legacy {field} is {value.dtype}{value.shape}, expected {dtype}{shape}")
            self._rows[field] = value
        if not self.key_varies:
            key = np.asarray(self._meta["key"], dtype=np.uint8)
            if key.shape != (16,):
                raise ValueError("legacy fixed key must have shape (16,)")

    def _row_digest_arrays(self):
        arrays = {"trace": self.traces_all}
        arrays.update(self._rows)
        return arrays

    def __contains__(self, name):
        return name in self._rows or name in self._meta.files

    def __getitem__(self, name):
        if name in self._rows:
            return self._rows[name]
        return self._meta[name]

    @property
    def traces(self):
        return self.traces_all[:self.done]

    @property
    def meta(self):
        return self

    def close(self):
        meta = getattr(self, "_meta", None)
        if meta is not None:
            try:
                meta.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def open_native(base):
    return NativeDataset(base)
