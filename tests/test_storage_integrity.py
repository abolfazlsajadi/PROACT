"""Regression tests for owned records, checkpoint safety, and format detection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from proact_host import storage
from proact_host.storage import TraceStore, load

PT = bytes(range(16))


def append(store, value=1, **kwargs):
    store.append([value, value + 1], PT, PT, PT, **kwargs)


def test_append_owns_trace_and_mutable_payloads(tmp_path):
    store = TraceStore(tmp_path / "owned.npz")
    waveform = np.array([1, 2, 3], np.float32)
    values = [bytearray([i, i + 1]) for i in range(4)]
    store.append(waveform, *values, valid=True)
    waveform[:] = 99
    for value in values:
        value[:] = b"\xff\xff"
    data = load(store.close())
    assert data["traces"].tolist() == [[1, 2, 3]]
    for i, field in enumerate(("plaintext", "key", "output", "expected")):
        assert data[field].tolist() == [[i, i + 1]]


def test_nested_metadata_is_snapshot_of_supplied_config(tmp_path):
    metadata = {"clock": {"hz": 100}, "ports": [1, 2]}
    store = TraceStore(tmp_path / "owned.npz", metadata)
    metadata["clock"]["hz"] = 200
    metadata["ports"].append(3)
    data = load(store.close())
    assert data["metadata"]["clock"] == {"hz": 100}
    assert data["metadata"]["ports"] == [1, 2]


class BadBoolean:
    def __bool__(self):
        raise ValueError("invalid flag")


@pytest.mark.parametrize("field,bad", [
    ("trace", [[1, 2], [3, 4]]), ("trace", 3),
    ("plaintext", "text"), ("key", "text"), ("output", "text"),
    ("expected", "text"), ("valid", BadBoolean()),
])
def test_rejected_append_cannot_partially_extend_fields(tmp_path, field, bad):
    store = TraceStore(tmp_path / "atomic-row.npz")
    append(store, 1)
    row = dict(trace=[4, 5], plaintext=PT, key=PT, output=PT, expected=PT, valid=True)
    row[field] = bad
    with pytest.raises((ValueError, TypeError)):
        store.append(**row)
    assert store.count == 1
    append(store, 8)
    data = load(store.close())
    assert data["traces"].tolist() == [[1, 2], [8, 9]]
    assert data["plaintext"].shape == data["output"].shape == (2, 16)
    assert data["valid"].tolist() == [-1, -1]


def test_npz_format_metadata_independent_of_h5py(tmp_path):
    store = TraceStore(tmp_path / "explicit.NPZ")
    assert store.path.endswith(".NPZ")
    assert store.meta["format"] == store.meta["storage_format"] == "npz"
    assert load(store.close())["metadata"]["format"] == "npz"


def test_load_npz_with_other_suffix_and_path_objects(tmp_path):
    store = TraceStore(tmp_path / "source.npz")
    append(store)
    destination = tmp_path / "renamed.anything"
    Path(store.close()).rename(destination)
    assert load(destination)["traces"].shape == (1, 2)


def test_missing_h5py_fallback_reports_actual_container(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_HAVE_H5", False)
    store = TraceStore(tmp_path / "fallback.h5")
    append(store)
    assert load(store.close())["metadata"]["storage_format"] == "npz"


def test_hdf5_content_detection_and_missing_dependency(tmp_path, monkeypatch):
    pytest.importorskip("h5py")
    store = TraceStore(tmp_path / "source.h5", {"nested": {"flag": True}})
    append(store)
    destination = tmp_path / "renamed.npz"
    Path(store.close()).rename(destination)
    assert load(destination)["metadata"]["nested"] == {"flag": True}
    monkeypatch.setattr(storage, "_HAVE_H5", False)
    with pytest.raises(ImportError, match="h5py"):
        load(destination)


def test_hdf5_legacy_metadata_preserved_with_failures_added(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "legacy.h5"
    with h5py.File(path, "w") as handle:
        handle["traces"] = np.ones((1, 2), np.float32)
        handle.attrs["scope"] = '{"samples": 2}'
        handle.attrs["failures"] = '[{"index": 3, "reason": "timeout"}]'
    data = load(path)
    assert data["metadata"]["scope"] == '{"samples": 2}'
    assert data["failures"][0]["index"] == 3


def test_metadata_free_legacy_archive_is_not_relabelled_valid(tmp_path):
    path = tmp_path / "postprocessed.npz"
    np.savez(path, traces=np.zeros((3, 5), np.int16), key=np.arange(16, dtype=np.uint8))
    data = load(path)
    assert data["traces"].dtype == np.int16
    assert data["key"].shape == (16,)
    assert data["metadata"] == {} and data["failures"] == []
    assert "valid" not in data


@pytest.mark.parametrize("suffix", [".npz", ".h5"])
def test_failed_replacement_preserves_existing_checkpoint(tmp_path, monkeypatch, suffix):
    if suffix == ".h5":
        pytest.importorskip("h5py")
    store = TraceStore(tmp_path / ("checkpoint" + suffix))
    append(store, 1)
    store.flush()
    before = Path(store.path).read_bytes()
    append(store, 3)
    replace = storage.os.replace
    def fail(source, destination):
        raise OSError("simulated disk/rename failure")
    with monkeypatch.context() as context:
        context.setattr(storage.os, "replace", fail)
        with pytest.raises(OSError, match="simulated"):
            store.flush()
    assert Path(store.path).read_bytes() == before
    assert load(store.path)["traces"].tolist() == [[1, 2]]
    assert not list(tmp_path.glob(".*.tmp"))
    assert storage.os.replace is replace
    store.flush()
    assert load(store.path)["traces"].tolist() == [[1, 2], [3, 4]]


def test_interrupted_compression_preserves_prior_npz(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "checkpoint.npz")
    append(store)
    store.flush()
    before = Path(store.path).read_bytes()
    append(store, 3)
    def fail(handle, **arrays):
        handle.write(b"partial archive")
        raise KeyboardInterrupt("interrupted compression")
    with monkeypatch.context() as context:
        context.setattr(storage.np, "savez_compressed", fail)
        with pytest.raises(KeyboardInterrupt):
            store.flush()
    assert Path(store.path).read_bytes() == before
    assert load(store.path)["traces"].shape == (1, 2)
    store.flush()
    assert load(store.path)["traces"].shape == (2, 2)


def test_unserializable_metadata_cannot_destroy_checkpoint(tmp_path):
    store = TraceStore(tmp_path / "checkpoint.npz")
    append(store)
    store.flush()
    before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    store.meta["bad"] = object()
    with pytest.raises(TypeError):
        store.flush()
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest() == before


def test_hard_process_exit_mid_write_leaves_checkpoint_loadable(tmp_path):
    store = TraceStore(tmp_path / "checkpoint.npz")
    append(store)
    store.flush()
    script = '''
import os, sys
from proact_host.storage import _atomic_write
def interrupted(path):
    with open(path, "wb") as handle:
        handle.write(b"incomplete")
        handle.flush()
    os._exit(73)
_atomic_write(sys.argv[1], interrupted)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(storage.__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script, store.path], env=env, timeout=20)
    assert result.returncode == 73
    assert load(store.path)["traces"].tolist() == [[1, 2]]


def test_lengths_preserve_ragged_byte_payload_boundaries(tmp_path):
    store = TraceStore(tmp_path / "lengths.npz")
    store.append([1], b"\x00", b"\x01", b"\x02", b"\x03")
    store.append([2], b"\x00\x00", b"\x01\x00", b"\x02\x00", b"\x03\x00")
    data = load(store.close())
    for field in ("plaintext", "key", "output", "expected"):
        assert data[field + "_lengths"].tolist() == [1, 2]
    assert data["trace_lengths"].tolist() == [1, 1]


def test_context_manager_checkpoints_accepted_rows_after_exception(tmp_path):
    path = tmp_path / "context.npz"
    with pytest.raises(RuntimeError, match="workload failed"):
        with TraceStore(path) as store:
            append(store)
            raise RuntimeError("workload failed")
    assert load(path)["traces"].shape == (1, 2)


@pytest.mark.parametrize("suffix", [".npz", ".h5", ".tracepack"])
def test_unchanged_flush_and_close_do_not_rewrite_checkpoint(tmp_path, monkeypatch, suffix):
    if suffix == ".h5":
        pytest.importorskip("h5py")
    store = TraceStore(tmp_path / ("unchanged" + suffix))
    append(store)
    store.flush()
    calls = []
    replace = storage.os.replace
    def record(source, destination):
        calls.append(str(destination))
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", record)
    store.flush()
    store.close()
    assert calls == []
    store.meta["updated"] = True
    store.flush()
    assert len(calls) == 1
    assert load(store.path)["metadata"]["updated"] is True


def test_context_preserves_workload_exception_when_checkpoint_fails_under_werror(tmp_path, monkeypatch, capsys):
    import warnings
    store = TraceStore(tmp_path / "context-save-failed.npz")
    def fail_close():
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(store, "close", fail_close)
    workload_error = ValueError("original workload failed")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError) as raised:
            with store:
                raise workload_error
    assert raised.value is workload_error
    assert "could not checkpoint after workload failure: synthetic disk failure" in capsys.readouterr().err


def test_context_preserves_workload_exception_even_when_error_reporting_fails(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "context-report-failed.npz")
    def fail_close():
        raise OSError("synthetic disk failure")
    class BrokenStderr:
        def write(self, message):
            raise OSError("stderr unavailable")
    monkeypatch.setattr(store, "close", fail_close)
    monkeypatch.setattr(storage.sys, "stderr", BrokenStderr())
    workload_error = ValueError("original workload failed")
    with pytest.raises(ValueError) as raised:
        with store:
            raise workload_error
    assert raised.value is workload_error
