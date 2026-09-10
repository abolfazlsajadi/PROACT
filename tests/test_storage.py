"""
Regression tests for proact_host.storage (TraceStore + load).

TraceStore is the only thing between a multi-hour capture campaign and lost
data, so the properties exercised here are the ones the module docstring
promises: incremental append/flush survives interruption, and everything
written comes back through load() with the right shapes, dtypes and bytes.

Pure numpy + json + filesystem -- no board, no network, no ChipWhisperer.
h5py is optional; NPZ is tested unconditionally and the HDF5 path is guarded
with importorskip. No installed or connected bench state is assumed.

Everything is written under tmp_path; nothing touches the repo.
"""
import json
import os
import re

import numpy as np
import pytest

from proact_host import storage
from proact_host.storage import TraceStore, load

PT = bytes(range(16))
KEY = bytes(range(0x10, 0x20))
OUT = bytes(range(0xF0, 0x100))


def _store(tmp_path, name="run.npz", metadata=None):
    return TraceStore(str(tmp_path / name), metadata)


# --------------------------------------------------------------- extensions
def test_missing_extension_gets_npz_when_h5py_absent(tmp_path):
    """Without h5py the inferred container must be .npz, not .h5."""
    store = TraceStore(str(tmp_path / "out"))
    expected_ext = ".h5" if storage._HAVE_H5 else ".npz"
    assert store.path == str(tmp_path / ("out" + expected_ext))


def test_explicit_npz_extension_is_left_alone(tmp_path):
    """A caller-supplied .npz must not be doubled up ("run.npz.npz")."""
    store = TraceStore(str(tmp_path / "run.npz"))
    assert store.path == str(tmp_path / "run.npz")


def test_explicit_h5_extension_is_left_alone(tmp_path):
    store = TraceStore(str(tmp_path / "run.h5"))
    assert store.path == str(tmp_path / "run.h5")


def test_parent_directory_is_created(tmp_path):
    """A capture into results/<date>/run must not fail on a missing dir."""
    target = tmp_path / "results" / "2026-08-07" / "run"
    store = TraceStore(str(target))
    assert os.path.isdir(str(target.parent))
    assert store.close() == store.path
    assert os.path.exists(store.path)


# ---------------------------------------------------------------- metadata
def test_metadata_gains_created_and_format_defaults(tmp_path):
    store = _store(tmp_path, metadata={"target": "aes1"})
    assert store.meta["target"] == "aes1"
    assert store.meta["format"] == "npz"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", store.meta["created"])


def test_metadata_defaults_do_not_clobber_caller_keys(tmp_path):
    supplied = {"created": "1999-01-01 00:00:00", "format": "custom"}
    store = _store(tmp_path, metadata=supplied)
    assert store.meta["created"] == "1999-01-01 00:00:00"
    assert store.meta["format"] == "custom"


def test_metadata_dict_is_copied_not_aliased(tmp_path):
    supplied = {"target": "aes1"}
    store = _store(tmp_path, metadata=supplied)
    assert supplied == {"target": "aes1"}, "caller's dict was mutated in place"
    assert store.meta is not supplied


def test_nested_metadata_survives_json_round_trip(tmp_path):
    meta = {
        "target": "ascon128",
        "scope": {"samples": 5000, "gain": 25, "decimate": 1},
        "trigger": ["tio4", "rising"],
        "firmware": {"sha": "deadbeef", "dirty": False, "ports": [1, 2, 3]},
    }
    store = _store(tmp_path, metadata=meta)
    store.append([0.0, 1.0], PT, KEY, OUT)
    path = store.close()

    loaded = load(path)["metadata"]
    for key, value in meta.items():
        assert loaded[key] == value
    assert loaded["format"] == "npz"


# ------------------------------------------------------------------ append
def test_append_increments_count(tmp_path):
    store = _store(tmp_path)
    assert store.count == 0
    store.append([0.0, 1.0], PT, KEY, OUT)
    assert store.count == 1
    store.append([2.0, 3.0], PT, KEY, OUT)
    assert store.count == 2


def test_flush_then_load_gives_expected_shapes_and_dtypes(tmp_path):
    store = _store(tmp_path)
    store.append(np.arange(8), PT, KEY, OUT, valid=True)
    store.append(np.arange(8) + 8, PT, KEY, OUT, valid=False)
    store.flush()

    data = load(store.path)
    assert data["traces"].shape == (2, 8)
    assert data["traces"].dtype == np.float32
    for field in ("plaintext", "key", "output"):
        assert data[field].shape == (2, 16), field
        assert data[field].dtype == np.uint8, field
    assert data["valid"].shape == (2,)
    assert data["valid"].dtype == np.int8
    np.testing.assert_array_equal(data["traces"][0], np.arange(8, dtype=np.float32))
    np.testing.assert_array_equal(data["traces"][1], np.arange(8, 16, dtype=np.float32))


def test_float64_traces_are_stored_as_float32(tmp_path):
    """Traces are downcast on append; load() must not silently widen them."""
    store = _store(tmp_path)
    store.append(np.array([0.5, -0.25, 0.125], dtype=np.float64), PT, KEY, OUT)
    data = load(store.close())
    assert data["traces"].dtype == np.float32
    np.testing.assert_array_equal(data["traces"][0],
                                  np.array([0.5, -0.25, 0.125], np.float32))


def test_byte_payloads_round_trip_exactly(tmp_path):
    """A 16-byte key/plaintext/ciphertext must come back byte-identical."""
    store = _store(tmp_path)
    store.append([1.0], PT, KEY, OUT)
    data = load(store.close())
    assert bytes(data["plaintext"][0]) == PT
    assert bytes(data["key"][0]) == KEY
    assert bytes(data["output"][0]) == OUT


def test_variable_length_byte_payload_round_trips(tmp_path):
    """AEAD outputs are longer than 16 bytes; they must survive unchanged."""
    tag_and_ct = bytes(range(32))
    store = _store(tmp_path)
    store.append([1.0], PT, KEY, tag_and_ct)
    data = load(store.close())
    assert data["output"].shape == (1, 32)
    assert bytes(data["output"][0]) == tag_and_ct


# ------------------------------------------------------------ valid flags
@pytest.mark.parametrize("flag,sentinel", [(None, -1), (True, 1), (False, 0)])
def test_valid_sentinel_encoding(tmp_path, flag, sentinel):
    store = _store(tmp_path)
    store.append([1.0], PT, KEY, OUT, valid=flag)
    data = load(store.close())
    assert data["valid"].tolist() == [sentinel]


def test_valid_flags_keep_append_order(tmp_path):
    store = _store(tmp_path)
    for flag in (True, None, False, True):
        store.append([1.0], PT, KEY, OUT, valid=flag)
    data = load(store.close())
    assert data["valid"].tolist() == [1, -1, 0, 1]


# --------------------------------------------------------------- raggedness
def test_ragged_traces_keep_the_good_rows_intact(tmp_path):
    """A short capture must never shrink the GOOD traces.

    _vstack used to narrow every row to the shortest, so one glitched capture
    silently destroyed the samples past the cut in the whole campaign. It now
    widens to the longest row and zero-pads the short one instead.
    """
    store = _store(tmp_path)
    store.append([1.0, 2.0, 3.0], PT, KEY, OUT)
    store.append([4.0, 5.0], PT, KEY, OUT)          # short / glitched
    with pytest.warns(RuntimeWarning, match="incomplete trace rows"):
        data = load(store.close())
    assert data["traces"].shape == (2, 3)
    np.testing.assert_array_equal(
        data["traces"], np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 0.0]], np.float32))


def test_ragged_traces_are_marked_invalid_and_recorded(tmp_path):
    """The incomplete row must be flagged, not silently pass as a good trace."""
    store = _store(tmp_path)
    store.append([1.0, 2.0, 3.0], PT, KEY, OUT, valid=True)
    store.append([4.0, 5.0], PT, KEY, OUT, valid=True)   # short -> forced invalid
    with pytest.warns(RuntimeWarning, match="incomplete trace rows"):
        data = load(store.close())
    assert list(np.asarray(data["valid"])) == [1, 0]
    meta = data["metadata"]
    meta = meta.item() if hasattr(meta, "item") else meta
    if isinstance(meta, (str, bytes)):
        meta = json.loads(meta)
    assert meta["ragged_trace_rows"] == [1]
    assert meta["trace_samples"] == 3


def test_equal_length_traces_record_no_raggedness(tmp_path):
    """The normal case must be untouched -- no extra metadata, nothing invalid."""
    store = _store(tmp_path)
    store.append([1.0, 2.0], PT, KEY, OUT)
    store.append([3.0, 4.0], PT, KEY, OUT)
    data = load(store.close())
    assert data["traces"].shape == (2, 2)
    meta = data["metadata"]
    meta = meta.item() if hasattr(meta, "item") else meta
    if isinstance(meta, (str, bytes)):
        meta = json.loads(meta)
    assert "ragged_trace_rows" not in meta


def test_ragged_byte_fields_widen_without_raising(tmp_path):
    store = _store(tmp_path)
    store.append([1.0], PT, KEY, bytes(range(32)))
    store.append([2.0], PT, KEY, bytes(range(16)))
    data = load(store.close())
    assert data["output"].shape == (2, 32)          # widened, not narrowed
    assert bytes(data["output"][0]) == bytes(range(32))
    assert bytes(data["output"][1][:16]) == bytes(range(16))


# -------------------------------------------------------------- empty store
def test_empty_store_writes_a_loadable_file(tmp_path):
    """close() on a run that captured nothing must still produce a readable
    file rather than blowing up on np.vstack of an empty list."""
    store = _store(tmp_path, name="empty.npz")
    store.flush()
    path = store.close()

    assert os.path.exists(path)
    data = load(path)
    for field in ("traces", "plaintext", "key", "output", "expected"):
        assert data[field].shape == (0, 0), field
    assert data["traces"].dtype == np.float32
    assert data["valid"].shape == (0,)
    assert data["valid"].dtype == np.int8
    assert data["failures"] == []


# ---------------------------------------------------- incremental persistence
def test_incremental_flush_keeps_all_rows(tmp_path):
    """Repeated snapshot checkpoints retain every row, not just the tail."""
    store = _store(tmp_path)
    store.append([0.0, 1.0], PT, KEY, OUT, valid=True)
    store.flush()
    assert load(store.path)["traces"].shape == (1, 2)

    store.append([2.0, 3.0], PT, KEY, OUT, valid=False)
    store.append([4.0, 5.0], PT, KEY, OUT, valid=None)
    store.flush()

    data = load(store.path)
    assert data["traces"].shape == (3, 2)
    np.testing.assert_array_equal(
        data["traces"], np.array([[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]], np.float32))
    assert data["valid"].tolist() == [1, 0, -1]


def test_repeated_flush_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.append([0.0, 1.0], PT, KEY, OUT)
    store.flush()
    first = load(store.path)
    store.flush()
    store.flush()
    second = load(store.path)
    np.testing.assert_array_equal(first["traces"], second["traces"])
    assert first["metadata"] == second["metadata"]


def test_close_returns_an_existing_loadable_path(tmp_path):
    store = _store(tmp_path, name="campaign")
    store.append([0.0, 1.0], PT, KEY, OUT)
    path = store.close()

    assert os.path.isabs(path) or os.path.exists(path)
    assert os.path.exists(path), f"close() returned a non-existent path: {path}"
    assert load(path)["traces"].shape == (1, 2)


# ----------------------------------------------------------------- failures
def test_record_failure_entries_survive_the_round_trip(tmp_path):
    store = _store(tmp_path)
    store.append([0.0], PT, KEY, OUT)
    store.record_failure(3, "scope timeout")
    store.record_failure(7, "bad UART frame")

    failures = load(store.close())["failures"]
    assert [f["index"] for f in failures] == [3, 7]
    assert [f["reason"] for f in failures] == ["scope timeout", "bad UART frame"]
    assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", f["time"]) for f in failures)


def test_failures_do_not_affect_trace_count(tmp_path):
    store = _store(tmp_path)
    store.append([0.0], PT, KEY, OUT)
    store.record_failure(1, "dropped")
    assert store.count == 1
    assert load(store.close())["traces"].shape == (1, 1)


def test_metadata_and_failures_are_not_exposed_as_arrays(tmp_path):
    """load() must decode the two JSON blobs, not hand back numpy scalars."""
    store = _store(tmp_path, metadata={"target": "aes1"})
    store.record_failure(0, "boom")
    data = load(store.close())
    assert isinstance(data["metadata"], dict)
    assert isinstance(data["failures"], list)
    assert not any(isinstance(v, np.ndarray)
                   for v in (data["metadata"], data["failures"]))


# ---------------------------------------------------------------- expected
def test_expected_ciphertexts_round_trip_when_always_supplied(tmp_path):
    store = _store(tmp_path)
    store.append([0.0], PT, KEY, OUT, expected=OUT)
    store.append([1.0], PT, KEY, OUT, expected=PT)
    data = load(store.close())
    assert data["expected"].shape == (2, 16)
    assert bytes(data["expected"][0]) == OUT
    assert bytes(data["expected"][1]) == PT


def test_no_expected_supplied_gives_an_empty_expected_array(tmp_path):
    store = _store(tmp_path)
    store.append([0.0], PT, KEY, OUT)
    assert load(store.close())["expected"].shape == (0, 0)


def test_expected_rows_stay_aligned_with_trace_rows(tmp_path):
    store = _store(tmp_path)
    store.append([0.0], PT, KEY, OUT)                    # no expected
    store.append([1.0], PT, KEY, OUT, expected=KEY)      # expected supplied
    data = load(store.close())

    assert data["expected"].shape[0] == data["traces"].shape[0], (
        "expected rows must stay index-aligned with traces")
    assert bytes(data["expected"][1]) == KEY


# ------------------------------------------------------------ h5 fall-back
@pytest.mark.skipif(storage._HAVE_H5,
                    reason="exercises the npz fall-back taken when h5py is missing")
def test_h5_output_without_h5py_yields_a_usable_path(tmp_path):
    store = TraceStore(str(tmp_path / "x.h5"))
    store.append([0.0, 1.0], PT, KEY, OUT)
    path = store.close()

    assert os.path.exists(path), f"close() returned a non-existent path: {path}"
    assert load(path)["traces"].shape == (1, 2)


# ------------------------------------------------------------------- hdf5
def test_hdf5_round_trip_when_h5py_is_installed(tmp_path):
    pytest.importorskip("h5py")
    store = TraceStore(str(tmp_path / "run.h5"), {"target": "aes1",
                                                  "scope": {"samples": 4}})
    store.append([0.0, 1.0, 2.0, 3.0], PT, KEY, OUT, valid=True)
    store.record_failure(2, "scope timeout")
    path = store.close()

    assert path.endswith(".h5")
    assert os.path.exists(path)
    data = load(path)
    assert data["traces"].shape == (1, 4)
    assert data["traces"].dtype == np.float32
    assert bytes(data["key"][0]) == KEY
    assert data["valid"].tolist() == [1]
    assert data["metadata"]["target"] == "aes1"
    assert data["metadata"]["n_traces"] == 1
    assert data["metadata"]["scope"] == {"samples": 4}
    assert data["failures"][0]["index"] == 2
