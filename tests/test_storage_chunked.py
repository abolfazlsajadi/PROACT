"""Offline multi-checkpoint and corruption tests for the tracepack format."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from proact_host import storage
from proact_host.storage import TraceStore, iter_chunks, load


def append(store, value=1, *, expected=None):
    store.append([value, value + 1], bytes([value]), b"key", b"output", expected, True)


def manifest(path):
    return json.loads((Path(path) / "manifest.json").read_text())


def test_automatic_chunks_release_buffer_without_rewriting_committed_data(tmp_path):
    store = TraceStore(tmp_path / "bounded.tracepack", chunk_rows=2)
    append(store, 1)
    append(store, 2)
    assert store.count == 2 and store.buffered_count == 0
    first = next(Path(store.path).glob("chunk-*.npz"))
    fingerprint = (first.stat().st_mtime_ns, hashlib.sha256(first.read_bytes()).hexdigest())
    for value in range(3, 12):
        append(store, value)
        assert store.buffered_count < 2
    assert load(store.path)["traces"].shape == (10, 2)  # one pending record
    store.close()
    assert store.count == 11 and store.buffered_count == 0
    assert load(store.path)["traces"].shape == (11, 2)
    assert (first.stat().st_mtime_ns, hashlib.sha256(first.read_bytes()).hexdigest()) == fingerprint
    assert sum(part["traces"].shape[0] for part in iter_chunks(store.path)) == 11


def test_cross_chunk_padding_validity_and_expected_presence(tmp_path):
    store = TraceStore(tmp_path / "ragged.tracepack", chunk_rows=1)
    store.append([1, 2], b"p", b"k", b"o", valid=True)
    store.append([3, 4, 5], b"pp", b"kk", b"oo", b"e", True)
    store.append([6], b"p", b"k", b"o", b"ee", True)
    data = load(store.close())
    assert data["traces"].tolist() == [[1, 2, 0], [3, 4, 5], [6, 0, 0]]
    assert data["trace_lengths"].tolist() == [2, 3, 1]
    assert data["valid"].tolist() == [0, 1, 0]
    assert data["expected"].tolist() == [[0, 0], [101, 0], [101, 101]]
    assert data["expected_lengths"].tolist() == [0, 1, 2]
    assert data["expected_present"].tolist() == [0, 1, 1]
    assert data["metadata"]["ragged_trace_rows"] == [0, 2]
    chunks = list(iter_chunks(store.path))
    assert [part["metadata"]["row_start"] for part in chunks] == [0, 1, 2]
    assert [part["metadata"]["row_stop"] for part in chunks] == [1, 2, 3]
    assert all(part["traces"].shape == (1, 3) for part in chunks)
    assert chunks[2]["metadata"]["ragged_trace_rows"] == [2]


@pytest.mark.parametrize("expected", [None, b"expected"])
def test_uniform_expected_presence_omits_mask(tmp_path, expected):
    store = TraceStore(tmp_path / "presence.tracepack", chunk_rows=1)
    append(store, 1, expected=expected)
    append(store, 2, expected=expected)
    data = load(store.close())
    assert "expected_present" not in data
    assert data["expected"].shape == ((0, 0) if expected is None else (2, 8))


def test_empty_tracepack_has_readable_arrays_and_failure_log(tmp_path):
    store = TraceStore(tmp_path / "empty.tracepack")
    store.record_failure(0, "offline simulated timeout")
    data = load(store.close())
    assert data["traces"].shape == (0, 0)
    assert data["valid"].shape == (0,)
    assert data["failures"][0]["index"] == 0
    assert list(iter_chunks(store.path)) == []


def test_flush_without_rows_updates_metadata_without_new_chunk(tmp_path):
    store = TraceStore(tmp_path / "meta.tracepack", chunk_rows=1)
    append(store)
    chunks = list(Path(store.path).glob("chunk-*.npz"))
    store.meta["note"] = {"finished": True}
    store.record_failure(2, "timeout")
    store.flush()
    store.flush()
    assert list(Path(store.path).glob("chunk-*.npz")) == chunks
    assert load(store.path)["metadata"]["note"] == {"finished": True}
    assert load(store.path)["failures"][0]["index"] == 2


def test_existing_pack_cannot_be_accidentally_overwritten(tmp_path):
    path = tmp_path / "existing.tracepack"
    store = TraceStore(path)
    append(store)
    store.close()
    with pytest.raises(FileExistsError):
        TraceStore(path)
    assert load(path)["traces"].shape == (1, 2)


@pytest.mark.parametrize("rows", [0, -1, True, 1.5])
def test_chunk_limit_requires_positive_integer(tmp_path, rows):
    with pytest.raises(ValueError, match="chunk_rows"):
        TraceStore(tmp_path / "limit.tracepack", chunk_rows=rows)


def test_manifest_replacement_failure_preserves_previous_rows_and_retry(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "retry.tracepack", chunk_rows=10)
    append(store, 1)
    store.flush()
    first = (Path(store.path) / "manifest.json").read_bytes()
    append(store, 2)
    replace = storage.os.replace
    def interrupt(source, destination):
        if str(destination).endswith("manifest.json"):
            raise OSError("simulated manifest interruption")
        return replace(source, destination)
    with monkeypatch.context() as context:
        context.setattr(storage.os, "replace", interrupt)
        with pytest.raises(OSError, match="simulated"):
            store.flush()
    assert (Path(store.path) / "manifest.json").read_bytes() == first
    assert load(store.path)["traces"].tolist() == [[1, 2]]
    assert store.count == 2 and store.buffered_count == 1
    assert len(list(Path(store.path).glob("chunk-*.npz"))) == 2  # harmless orphan
    store.flush()
    assert load(store.path)["traces"].tolist() == [[1, 2], [2, 3]]
    assert manifest(store.path)["n_traces"] == 2


def test_interruption_after_manifest_commit_does_not_delete_referenced_chunk(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "after-commit.tracepack", chunk_rows=10)
    append(store, 1)
    store.flush()
    append(store, 2)
    replace = storage.os.replace
    def interrupt(source, destination):
        replace(source, destination)
        if str(destination).endswith("manifest.json"):
            raise KeyboardInterrupt("committed but caller interrupted")
    with monkeypatch.context() as context:
        context.setattr(storage.os, "replace", interrupt)
        with pytest.raises(KeyboardInterrupt):
            store.flush()
    assert load(store.path)["traces"].tolist() == [[1, 2], [2, 3]]
    store.flush()
    assert store.count == 2 and store.buffered_count == 0
    assert load(store.path)["traces"].tolist() == [[1, 2], [2, 3]]


def test_auto_flush_failure_accepts_record_once_and_flush_can_retry(tmp_path, monkeypatch):
    store = TraceStore(tmp_path / "auto-fail.tracepack", chunk_rows=2)
    append(store, 1)
    with monkeypatch.context() as context:
        context.setattr(storage, "_write_json", lambda *_: (_ for _ in ()).throw(OSError("full")))
        with pytest.raises(OSError, match="full"):
            append(store, 2)
    assert store.count == 2
    store.flush()
    assert load(store.path)["traces"].shape == (2, 2)


def test_reader_freezes_one_checkpoint_during_concurrent_append(tmp_path):
    store = TraceStore(tmp_path / "snapshot.tracepack", chunk_rows=1)
    append(store, 1)
    append(store, 2)
    reader = iter_chunks(store.path)
    first = next(reader)
    append(store, 3)
    assert first["traces"].tolist() == [[1, 2]]
    assert [part["traces"].tolist() for part in reader] == [[[2, 3]]]
    assert load(store.path)["traces"].shape[0] == 3


def test_chunk_checksum_detects_mutation_before_decoding(tmp_path):
    store = TraceStore(tmp_path / "corrupt.tracepack", chunk_rows=1)
    append(store)
    path = next(Path(store.path).glob("chunk-*.npz"))
    with path.open("ab") as handle:
        handle.write(b"unexpected bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load(store.path)


@pytest.mark.parametrize("change,match", [
    (lambda item: item["head"].update(file="../outside.npz"), "filename"),
    (lambda item: item["head"].update(start=3), "contiguous"),
    (lambda item: item.update(n_traces=90), "row count"),
    (lambda item: item.update(version=999), "version"),
])
def test_malformed_manifest_rejected(tmp_path, change, match):
    store = TraceStore(tmp_path / "bad.tracepack", chunk_rows=1)
    append(store)
    value = manifest(store.path)
    change(value)
    (Path(store.path) / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match=match):
        load(store.path)


def test_chunk_shape_validation_even_when_hash_verification_disabled(tmp_path):
    store = TraceStore(tmp_path / "shape.tracepack", chunk_rows=1)
    append(store)
    value = manifest(store.path)
    value["widths"]["traces"] = 1
    (Path(store.path) / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="global width"):
        list(iter_chunks(store.path, verify=False))


def test_snapshot_files_work_through_iterator_for_compatibility(tmp_path):
    store = TraceStore(tmp_path / "snapshot.npz")
    append(store)
    parts = list(iter_chunks(store.close()))
    assert len(parts) == 1 and parts[0]["traces"].shape == (1, 2)


def test_manifest_size_does_not_grow_with_the_full_chunk_index(tmp_path):
    store = TraceStore(tmp_path / "many-checkpoints.tracepack", chunk_rows=1)
    append(store)
    first_size = (Path(store.path) / "manifest.json").stat().st_size
    for value in range(2, 102):
        append(store, value)
    last_size = (Path(store.path) / "manifest.json").stat().st_size
    assert last_size < first_size + 64
    assert "chunks" not in manifest(store.path)
    assert load(store.path)["traces"].shape == (101, 2)


@pytest.mark.parametrize("claimed", [0, 2, 3, True, 1.0])
@pytest.mark.parametrize("verify", [True, False])
def test_incorrect_expected_count_is_rejected_before_first_chunk(tmp_path, claimed, verify):
    store = TraceStore(tmp_path / "expected-count.tracepack", chunk_rows=1)
    append(store, 1)
    append(store, 2, expected=b"expected")
    append(store, 3)
    value = manifest(store.path)
    assert value["expected_rows"] == 1
    value["expected_rows"] = claimed
    (Path(store.path) / "manifest.json").write_text(json.dumps(value))
    reader = iter_chunks(store.path, verify=verify)
    with pytest.raises(ValueError, match="expected row count"):
        next(reader)
    with pytest.raises(ValueError, match="expected row count"):
        load(store.path)


def test_empty_pack_cannot_claim_expected_rows(tmp_path):
    store = TraceStore(tmp_path / "empty-expected.tracepack")
    store.close()
    value = manifest(store.path)
    value["expected_rows"] = 1
    (Path(store.path) / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="expected row count"):
        load(store.path)
