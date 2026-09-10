"""Trace storage with atomic checkpoints and an opt-in bounded-buffer format.

``.npz`` and ``.h5`` are compatible, whole-dataset snapshots: they retain all
rows in memory and rewrite them on flush. ``.tracepack`` is a directory of
immutable NPZ chunks plus an atomically replaced manifest. It keeps at most
``chunk_rows`` pending rows and never rewrites committed waveform chunks.

A completed flush survives *process interruption*: readers see the preceding
or new checkpoint. Pending rows can be lost. This is not a power-loss guarantee,
a concurrent-writer API, or an automatic acquisition-resume facility.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Dict, Iterator, Optional
import uuid
import warnings

import numpy as np

try:
    import h5py
    _HAVE_H5 = True
except ImportError:
    _HAVE_H5 = False

_FIELDS = ("traces", "plaintext", "key", "output", "expected")
_LENGTHS = dict(zip(_FIELDS, ("trace_lengths", "plaintext_lengths", "key_lengths",
                             "output_lengths", "expected_lengths")))
_H5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_PACK_FORMAT = "proact.tracepack"


def _atomic_write(path, writer):
    """Publish a same-directory temporary file only after writing and fsync.

    There is deliberately no in-place truncation of the last good checkpoint.
    Parent-directory durability after sudden power loss is not guaranteed.
    """
    path = os.fspath(path)
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".",
                                      suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        os.close(fd)
        writer(temporary)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_npz(path, arrays):
    def write(temporary):
        with open(temporary, "wb") as handle:
            np.savez_compressed(handle, **arrays)
    _atomic_write(path, write)


def _write_json(path, value):
    # Serialize before touching any checkpoint; unsupported metadata is an
    # error with the previous on-disk state still intact.
    encoded = json.dumps(value, sort_keys=True, indent=2, allow_nan=False)
    def write(temporary):
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    _atomic_write(path, write)


class TraceStore:
    """Collect index-aligned trace records and persist checkpoints.

    Path suffix selects the format. Without a recognized suffix HDF5 is used
    when available, otherwise NPZ. For compatibility, explicit ``.h5`` without
    h5py still contains NPZ; ``storage_format`` records the actual container and
    :func:`load` probes it. ``.tracepack`` requires a new directory and supports
    :func:`iter_chunks` for bounded reads. A store is single-writer only.
    """
    def __init__(self, path: str, metadata: Optional[Dict] = None, *,
                 chunk_rows: int = 1024):
        path = os.fspath(path)
        suffix = Path(path).suffix.lower()
        if suffix not in (".h5", ".hdf5", ".npz", ".tracepack"):
            suffix = ".h5" if _HAVE_H5 else ".npz"
            path += suffix
        if not isinstance(chunk_rows, int) or isinstance(chunk_rows, bool) or chunk_rows < 1:
            raise ValueError("chunk_rows must be a positive integer")
        self.path = path
        self._format = ("chunked" if suffix == ".tracepack" else
                        "hdf5" if suffix in (".h5", ".hdf5") and _HAVE_H5 else "npz")
        self.meta = copy.deepcopy(dict(metadata or {}))
        self.meta.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.meta.setdefault("format", self._format)
        self.meta["storage_format"] = self._format
        self.meta["storage_schema_version"] = 2
        self._state = {"records": [], "committed": 0, "head": None, "n_chunks": 0,
                       "widths": {name: 0 for name in _FIELDS}, "expected_rows": 0}
        self._failures = []
        self._last_checkpoint = None
        self._chunk_rows = chunk_rows
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if self._format == "chunked":
            # Also prevents accidentally replacing another run or starting two
            # writers in one directory. Recovery is read-only via the manifest.
            os.mkdir(self.path)

    def append(self, trace, plaintext: bytes, key: bytes, output: bytes,
               expected: Optional[bytes] = None, valid: Optional[bool] = None):
        """Copy and validate all fields before adding one complete row.

        A rejected row does not alter count or any field. Automatic chunk flush
        can raise after the row has been accepted; retry ``flush()``, not append.
        """
        waveform = np.array(trace, dtype=np.float32, copy=True)
        if waveform.ndim != 1:
            raise ValueError("trace must be a one-dimensional sequence")
        payloads = [np.frombuffer(value, dtype=np.uint8).copy()
                    for value in (plaintext, key, output,
                                  b"" if expected is None else expected)]
        flag = -1 if valid is None else int(bool(valid))
        # No user-controlled conversion remains after this point.
        self._state["records"].append((waveform, *payloads, flag))
        if self._format == "chunked" and self.buffered_count >= self._chunk_rows:
            self.flush()

    def record_failure(self, index: int, reason: str):
        self._failures.append({"index": int(index), "reason": str(reason),
                               "time": time.strftime("%H:%M:%S")})

    @property
    def count(self) -> int:
        return self._state["committed"] + self.buffered_count

    @property
    def buffered_count(self) -> int:
        """Rows resident in the writer (all rows for snapshot formats)."""
        return len(self._state["records"])

    def flush(self):
        """Commit all accepted rows; a failed write preserves the last checkpoint."""
        fingerprint = (self.count, json.dumps(self.meta, allow_nan=False),
                       json.dumps(self._failures, allow_nan=False))
        checkpoint = (os.path.join(self.path, "manifest.json")
                      if self._format == "chunked" else self.path)
        if fingerprint == self._last_checkpoint and os.path.isfile(checkpoint):
            return
        if self._format == "chunked":
            self._flush_chunked()
        elif self._format == "hdf5":
            self._flush_h5()
        else:
            self._flush_npz()
        self._last_checkpoint = fingerprint

    @staticmethod
    def _vstack(rows, dtype):
        """Pad short rows to the longest without dropping or truncating rows."""
        if not rows:
            return np.empty((0, 0), dtype)
        out = np.zeros((len(rows), max(map(len, rows))), dtype)
        for i, row in enumerate(rows):
            out[i, :len(row)] = row
        return out

    @staticmethod
    def _ragged_rows(rows):
        if not rows:
            return []
        width = max(map(len, rows))
        return [i for i, row in enumerate(rows) if len(row) != width]

    def _arrays(self):
        records = self._state["records"]
        arrays = {}
        for column, name in enumerate(_FIELDS):
            values = [record[column] for record in records]
            arrays[_LENGTHS[name]] = np.asarray([len(row) for row in values], np.int64)
            arrays[name] = self._vstack(values, np.float32 if name == "traces" else np.uint8)
        present = arrays["expected_lengths"] != 0
        if not np.any(present):
            arrays["expected"] = np.empty((0, 0), np.uint8)
        elif not np.all(present):
            arrays["expected_present"] = present.astype(np.int8)
        arrays["valid"] = np.asarray([record[5] for record in records], np.int8)
        return arrays

    def _snapshot(self):
        arrays = self._arrays()
        meta = copy.deepcopy(self.meta)
        meta["n_traces"] = self.count
        short = np.flatnonzero(arrays["trace_lengths"] < arrays["traces"].shape[1])
        if short.size:
            arrays["valid"][short] = 0
            meta["ragged_trace_rows"] = short.tolist()
            meta["trace_samples"] = int(arrays["traces"].shape[1])
            warnings.warn(f"{short.size} incomplete trace rows were zero-padded and marked invalid",
                          RuntimeWarning, stacklevel=3)
        return arrays, meta

    def _flush_npz(self):
        arrays, meta = self._snapshot()
        arrays.update(metadata=json.dumps(meta, allow_nan=False),
                      failures=json.dumps(self._failures, allow_nan=False))
        _write_npz(self.path, arrays)

    def _flush_h5(self):
        arrays, meta = self._snapshot()
        metadata_json = json.dumps(meta, allow_nan=False)
        failures_json = json.dumps(self._failures, allow_nan=False)
        def write(temporary):
            with h5py.File(temporary, "w") as handle:
                for name, array in arrays.items():
                    handle.create_dataset(name, data=array,
                                          compression="gzip" if name == "traces" else None)
                # Retain old attributes for external HDF5 consumers. Exact JSON
                # additionally gives load() identical metadata types to NPZ.
                for key, value in meta.items():
                    handle.attrs[key] = value if isinstance(value, (int, float, str)) else json.dumps(value)
                handle.attrs["failures"] = failures_json
                handle.attrs["_proact_metadata_json"] = metadata_json
        _atomic_write(self.path, write)

    def _flush_chunked(self):
        arrays = self._arrays()
        pending = self.buffered_count
        previous = self._state
        head = previous["head"]
        n_chunks = previous["n_chunks"]
        widths = dict(previous["widths"])
        expected_rows = previous["expected_rows"]
        # Check serializability before producing even an orphan chunk.
        json.dumps(self.meta, allow_nan=False)
        json.dumps(self._failures, allow_nan=False)
        if pending:
            name = "chunk-" + uuid.uuid4().hex + ".npz"
            chunk_path = os.path.join(self.path, name)
            # An immutable predecessor link keeps the manifest bounded: a
            # frequent checkpoint does not rewrite a growing list of chunks.
            arrays["_previous_chunk"] = json.dumps(head)
            _write_npz(chunk_path, arrays)
            chunk_widths = {field: int(arrays[field].shape[1]) for field in _FIELDS}
            head = {"file": name, "start": previous["committed"], "count": pending,
                    "widths": chunk_widths, "sha256": _sha256(chunk_path)}
            n_chunks += 1
            widths = {field: max(widths[field], chunk_widths[field]) for field in _FIELDS}
            expected_rows += int(np.count_nonzero(arrays["expected_lengths"]))
        meta = copy.deepcopy(self.meta)
        meta["n_traces"] = self.count
        manifest = {"format": _PACK_FORMAT, "version": 1, "n_traces": self.count,
                    "widths": widths, "expected_rows": expected_rows,
                    "metadata": meta, "failures": self._failures, "head": head, "n_chunks": n_chunks}
        # Never delete a published chunk on error: interruption immediately
        # after manifest replacement may already have committed it. Orphans
        # from interrupted earlier writes are harmless and ignored by readers.
        _write_json(os.path.join(self.path, "manifest.json"), manifest)
        # One state swap releases the buffer and advances the row count. If
        # interruption occurs before this assignment, retrying writes the same
        # logical checkpoint from the preceding state, without duplicate rows.
        self._state = {"records": [], "committed": previous["committed"] + pending,
                       "head": head, "n_chunks": n_chunks,
                       "widths": widths, "expected_rows": expected_rows}

    def close(self):
        self.flush()
        return self.path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Preserve completed accepted records even if the surrounding workload
        # fails. If this save fails, retain the original exception and report
        # best-effort on stderr; warning filters must not replace that failure.
        if exc_type is None:
            self.close()
        else:
            try:
                self.close()
            except Exception as error:
                try:
                    print(f"proact: could not checkpoint after workload failure: {error}",
                          file=sys.stderr)
                except Exception:
                    # Even an unavailable stderr must not obscure the workload
                    # exception already being propagated by the context manager.
                    pass


def _read_npz(path):
    # Context management closes the ZIP/file handle, including decoding errors.
    # Metadata-free, post-processed legacy NPZ files are returned as stored;
    # absent fields are not invented as successful validation records.
    with np.load(path, allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files if key not in ("metadata", "failures")}
        data["metadata"] = json.loads(str(archive["metadata"])) if "metadata" in archive else {}
        data["failures"] = json.loads(str(archive["failures"])) if "failures" in archive else []
    return data


def _manifest(path):
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format") != _PACK_FORMAT or manifest.get("version") != 1:
        raise ValueError("unsupported tracepack format/version")
    count = manifest["n_traces"]
    n_chunks = manifest["n_chunks"]
    if (type(count) is not int or count < 0 or type(n_chunks) is not int or
            n_chunks < 0 or n_chunks > count):
        raise ValueError("invalid tracepack row/chunk count")
    if type(manifest["expected_rows"]) is not int or not 0 <= manifest["expected_rows"] <= count:
        raise ValueError("invalid tracepack expected row count")
    if any(type(manifest["widths"][field]) is not int or manifest["widths"][field] < 0
           for field in _FIELDS):
        raise ValueError("invalid tracepack field width")
    # Walk immutable links, reading only their small ZIP metadata/length
    # members. Validate expected-data counts before yielding any row, so a
    # damaged manifest cannot suppress the expected_present mask.
    # Waveforms are decompressed later, one chunk at a time. The descriptor
    # index uses O(number of chunks) memory; writer manifest space is O(1),
    # apart from caller metadata and the failure log.
    chunks = []
    cursor = count
    observed_expected_rows = 0
    chunk = manifest["head"]
    while chunk is not None:
        if len(chunks) >= n_chunks:
            raise ValueError("tracepack chunk chain exceeds committed count")
        if not re.fullmatch(r"chunk-[0-9a-f]{32}\.npz", chunk["file"]):
            raise ValueError("invalid tracepack chunk filename")
        if (type(chunk["count"]) is not int or type(chunk["start"]) is not int or
                chunk["count"] <= 0 or chunk["start"] < 0 or
                chunk["start"] + chunk["count"] != cursor):
            raise ValueError("tracepack chunks are not contiguous with committed row count")
        if not re.fullmatch(r"[0-9a-f]{64}", chunk["sha256"]):
            raise ValueError("invalid tracepack chunk checksum")
        chunks.append(chunk)
        cursor = chunk["start"]
        with np.load(os.path.join(path, chunk["file"]), allow_pickle=False) as archive:
            expected_lengths = archive["expected_lengths"]
            expected_width = chunk["widths"]["expected"]
            if (type(expected_width) is not int or expected_width < 0 or
                    expected_lengths.shape != (chunk["count"],) or
                    expected_lengths.dtype.kind not in "iu" or
                    np.any(expected_lengths < 0) or np.any(expected_lengths > expected_width)):
                raise ValueError("tracepack chunk has inconsistent expected rows/lengths")
            observed_expected_rows += int(np.count_nonzero(expected_lengths))
            chunk = json.loads(str(archive["_previous_chunk"]))
    if cursor != 0 or len(chunks) != n_chunks:
        raise ValueError("tracepack row count does not match chunk chain")
    if observed_expected_rows != manifest["expected_rows"]:
        raise ValueError("tracepack expected row count does not match stored rows")
    manifest["chunks"] = list(reversed(chunks))
    return manifest


def _iter_pack(path, manifest, verify):
    for chunk in manifest["chunks"]:
        filename = os.path.join(path, chunk["file"])
        if verify and _sha256(filename) != chunk["sha256"]:
            raise ValueError(f"tracepack checksum mismatch: {chunk['file']}")
        data = _read_npz(filename)
        data.pop("_previous_chunk", None)
        count = chunk["count"]
        for field in _FIELDS:
            array = data[field]
            lengths = data[_LENGTHS[field]]
            stored_count = 0 if field == "expected" and chunk["widths"][field] == 0 else count
            if (array.shape != (stored_count, chunk["widths"][field]) or
                    lengths.shape != (count,) or np.any(lengths < 0) or
                    np.any(lengths > chunk["widths"][field])):
                raise ValueError(f"tracepack chunk has inconsistent {field} rows/lengths")
            width = manifest["widths"][field]
            if width < chunk["widths"][field]:
                raise ValueError("tracepack global width is smaller than a stored chunk")
            if field == "expected" and width == 0:
                continue
            if array.shape != (count, width):
                padded = np.zeros((count, width), array.dtype)
                padded[:array.shape[0], :array.shape[1]] = array
                data[field] = padded
        if data["valid"].shape != (count,):
            raise ValueError("tracepack chunk has inconsistent valid rows")
        short = np.flatnonzero(data["trace_lengths"] < manifest["widths"]["traces"])
        data["valid"][short] = 0
        if 0 < manifest["expected_rows"] < manifest["n_traces"]:
            data["expected_present"] = (data["expected_lengths"] != 0).astype(np.int8)
        else:
            data.pop("expected_present", None)
        data["metadata"] = dict(manifest["metadata"])
        data["metadata"]["row_start"] = chunk["start"]
        data["metadata"]["row_stop"] = chunk["start"] + count
        if short.size:
            data["metadata"]["ragged_trace_rows"] = (short + chunk["start"]).tolist()
            data["metadata"]["trace_samples"] = manifest["widths"]["traces"]
        data["failures"] = manifest["failures"]
        yield data


def iter_chunks(path: str, *, verify: bool = True) -> Iterator[Dict]:
    """Read one committed tracepack checkpoint in bounded, aligned chunks.

    Only ``.tracepack`` directories stream. Reads retain the small descriptor
    index plus one waveform chunk. Each returned chunk uses the
    checkpoint's global column widths and includes ``metadata.row_start`` and
    ``row_stop``. Snapshot files yield one whole-dataset dictionary. ``verify``
    checks stored chunk SHA-256 digests (integrity, not authenticity).
    """
    path = os.fspath(path)
    if os.path.isdir(path):
        manifest = _manifest(path)  # freeze one checkpoint, even during writes
        yield from _iter_pack(path, manifest, verify)
    else:
        yield load(path)


def load(path: str) -> Dict:
    """Load an entire committed dataset into RAM; use iter_chunks for large packs.

    New HDF5 and NPZ snapshots return decoded metadata and a top-level failures
    list. Legacy HDF5 metadata retains its raw attributes to avoid guessing
    whether strings are JSON. Containers are identified by contents, not suffix.
    """
    path = os.fspath(path)
    if os.path.isdir(path):
        manifest = _manifest(path)
        parts = list(_iter_pack(path, manifest, True))
        data = {}
        for field in (*_FIELDS, *_LENGTHS.values(), "valid", "expected_present"):
            arrays = [part[field] for part in parts if field in part]
            if arrays:
                data[field] = np.concatenate(arrays, axis=0)
            elif field in _FIELDS:
                data[field] = np.empty((0, 0), np.float32 if field == "traces" else np.uint8)
            elif field != "expected_present":
                data[field] = np.empty(0, np.int8 if field == "valid" else np.int64)
        data["metadata"] = manifest["metadata"]
        data["failures"] = manifest["failures"]
        short = np.flatnonzero(data["trace_lengths"] < manifest["widths"]["traces"])
        if short.size:
            data["metadata"]["ragged_trace_rows"] = short.tolist()
            data["metadata"]["trace_samples"] = manifest["widths"]["traces"]
        return data
    with open(path, "rb") as handle:
        header = handle.read(8)
    is_h5 = h5py.is_hdf5(path) if _HAVE_H5 else header == _H5_SIGNATURE
    if is_h5:
        if not _HAVE_H5:
            raise ImportError("Reading this HDF5 file requires the optional h5py dependency")
        with h5py.File(path, "r") as handle:
            data = {key: handle[key][()] for key in handle.keys()}
            encoded = handle.attrs.get("_proact_metadata_json")
            data["metadata"] = json.loads(encoded) if encoded is not None else dict(handle.attrs)
            data["failures"] = json.loads(handle.attrs.get("failures", "[]"))
        return data
    return _read_npz(path)
