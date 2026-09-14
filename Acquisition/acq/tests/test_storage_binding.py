"""Hardware-free integrity tests for native checkpoints and CSV publication."""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pytest

import acq.config as config_module
import acq.paths as paths_module
from acq.config import AcqConfig
from acq.exporters import export_csv, export_h5, validate_csv_export
from acq.inputs import InputGen
from acq.native import (
    ROW_DIGEST_FIELDS, ROW_DIGEST_SCHEMA, capture_signature, open_native,
    verify_or_pin_firmware_identity,
)
from acq.store import TraceStore


def _rewrite_npz(path, *, remove=(), **updates):
    with np.load(path, allow_pickle=True) as old:
        values = {name: old[name].copy() for name in old.files if name not in remove}
    values.update(updates)
    tmp = path + ".rewrite.npz"
    np.savez(tmp, **values)
    os.replace(tmp, path)


def _dataset(monkeypatch, tmp_path, *, suffix, random_key=False, scope=False,
             rows=3, allocated=4):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        "aes1", allocated, suffix=suffix, seed=91,
        key_policy="random" if random_key else "fixed",
        backend="scope" if scope else "husky",
        scope_resource="TCPIP0::example::INSTR" if scope else "",
        scope_clock_source="external" if scope else "husky",
    ).validate()
    gen = InputGen(cfg)
    instrument = ({
        "waveform_unit": "volts", "scope_dialect": "keysight",
        "observed_sample_count": 4,
    } if scope else None)
    store = TraceStore(
        cfg, samples=4, out_len=16, key_varies=random_key,
        instrument_meta=instrument)
    for index in range(rows):
        store.append(
            index, np.asarray([index, -index, 0.01, -0.01], np.float32),
            gen.key(index), gen.inp(index), bytes([index]) * 16,
            group=index % 2, block=index // 2,
            scope_trigger_index=(1.25 + index if scope else None))
    store.checkpoint(rows)
    store.close()
    return cfg


def test_new_checkpoint_binds_rows_and_done(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_digest")
    with np.load(cfg.base + "_meta.npz", allow_pickle=True) as meta:
        assert set(ROW_DIGEST_FIELDS) <= set(meta.files)
        assert bool(meta["row_integrity_required"]) is True
        assert str(meta["row_digest_schema"].item()) == ROW_DIGEST_SCHEMA
        assert int(meta["row_digest_done"]) == 3
        assert int(meta["row_digest_unverified_prefix_rows"]) == 0
    with open_native(cfg.base) as native:
        assert native.row_integrity["digest_verified"] is True
        assert native.row_integrity["checkpoint_rows"] == 3
        assert native.row_integrity["unverified_prefix_rows"] == 0


@pytest.mark.parametrize(
    "field,random_key",
    (("trace", False), ("input", False), ("out", False),
     ("group", False), ("block", False), ("ts", False), ("key", True)),
)
def test_native_loader_rejects_authoritative_row_mutation(
        monkeypatch, tmp_path, field, random_key):
    cfg = _dataset(
        monkeypatch, tmp_path, suffix=f"_mutate_{field}", random_key=random_key)
    path = (cfg.base + "_traces.npy" if field == "trace"
            else cfg.base + f"_{field}.npy")
    value = np.load(path, mmap_mode="r+")
    target = (0, 0) if value.ndim == 2 else (0,)
    if value.dtype.kind == "u":
        value[target] ^= np.asarray(1, dtype=value.dtype)
    else:
        value[target] = value[target] + 1
    value.flush()
    with pytest.raises(ValueError, match="native row digest mismatch"):
        open_native(cfg.base)


def test_scope_trigger_phase_is_covered_by_native_digest(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_scope_phase", scope=True)
    phase = np.load(cfg.base + "_scope_trigger_index.npy", mmap_mode="r+")
    phase[1] += 0.25
    phase.flush()
    with pytest.raises(ValueError, match="native row digest mismatch"):
        open_native(cfg.base)


def test_done_marker_cannot_authorize_uncheckpointed_tail(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_done_forge", rows=2)
    _rewrite_npz(cfg.base + "_meta.npz", done=np.asarray(4, dtype=np.int64))
    with pytest.raises(ValueError, match="row digest marker 2 disagrees with done 4"):
        open_native(cfg.base)


def test_uncheckpointed_tail_is_ignored_and_can_be_overwritten_after_resume(
        monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_crash_tail", rows=2)
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    store.append(
        2, np.full(4, 0.123, np.float32), gen.key(2), gen.inp(2), bytes([2]) * 16)
    store.close()  # Models a crash after row pages reach disk but before metadata.

    with open_native(cfg.base) as native:
        assert native.done == 2
        assert native.row_integrity["digest_verified"] is True

    resumed = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    assert resumed.done == 2
    resumed.append(
        2, np.full(4, -0.123, np.float32), gen.key(2), gen.inp(2), bytes([2]) * 16)
    resumed.checkpoint(3)
    resumed.close()
    with open_native(cfg.base) as native:
        assert native.done == 3
        assert native.row_integrity["digest_verified"] is True


def test_predigest_dataset_is_read_only_unverified_then_upgraded_on_checkpoint(
        monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_predigest", rows=2)
    meta_path = cfg.base + "_meta.npz"
    _rewrite_npz(meta_path, remove=ROW_DIGEST_FIELDS)
    with open(meta_path, "rb") as stream:
        before = hashlib.sha256(stream.read()).hexdigest()

    with open_native(cfg.base) as native:
        assert native.row_integrity == {
            "status": "unverified_legacy_baseline",
            "digest_verified": False,
            "digest_schema": None,
            "checkpoint_rows": 2,
            "unverified_prefix_rows": 2,
            "sha256": None,
        }
    with open(meta_path, "rb") as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == before

    resumed = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    assert resumed.row_integrity["digest_verified"] is False
    resumed.checkpoint(2)
    resumed.close()
    with open_native(cfg.base) as native:
        assert native.row_integrity["digest_verified"] is True
        assert native.row_integrity["unverified_prefix_rows"] == 2
        assert native.row_integrity["status"].endswith("unverified_legacy_prefix")


@pytest.mark.parametrize(
    "field,value,phrase",
    (("done", np.asarray(2.5), "an integer"),
     ("n_alloc", np.asarray(4.0), "an integer"),
     ("samples", np.asarray("4"), "an integer"),
     ("out_len", np.asarray(True), "an integer"),
     ("fails", np.asarray(2.5), "an integer"),
     ("recoveries", np.asarray("2"), "an integer"),
     ("quality_rejects", np.asarray(True), "an integer"),
     ("scope_transfer_timeout_ms", np.asarray(8.5), "an integer"),
     ("key_varies", np.asarray(0, dtype=np.int64), "a boolean"),
     ("tvla", np.asarray(0, dtype=np.int64), "a boolean")),
)
def test_native_metadata_refuses_lossy_integral_and_boolean_coercion(
        monkeypatch, tmp_path, field, value, phrase):
    cfg = _dataset(monkeypatch, tmp_path, suffix=f"_type_{field}")
    _rewrite_npz(cfg.base + "_meta.npz", **{field: value})
    with pytest.raises(ValueError, match=rf"{field!r} must be {phrase} scalar"):
        open_native(cfg.base)


def test_resume_refuses_fractional_done_metadata(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_resume_fractional_done")
    _rewrite_npz(cfg.base + "_meta.npz", done=np.asarray(3.75))
    with pytest.raises(SystemExit, match="'done' must be an integer scalar"):
        TraceStore(cfg, samples=4, out_len=16, key_varies=False)


def test_firmware_identity_is_pinned_and_change_is_rejected(
        monkeypatch, tmp_path):
    firmware = tmp_path / "main.vmem"
    firmware.write_bytes(b"first firmware image")
    monkeypatch.setattr(paths_module, "CONTROLLER_VMEM", str(firmware))
    cfg = AcqConfig("aes1", 1, seed=1).validate()

    pinned = verify_or_pin_firmware_identity(cfg)
    assert pinned["controller_vmem"]["host_artifact_sha256"] == hashlib.sha256(
        b"first firmware image").hexdigest()
    assert capture_signature(cfg, samples=100, out_len=16)["firmware"] == pinned

    # The same path now names different bytes.  Hashing must not reuse the first
    # result, and a running campaign must not silently span both host images.
    firmware.write_bytes(b"second, different firmware image")
    with pytest.raises(
            RuntimeError,
            match=r"firmware host artifact changed.*controller_vmem"):
        verify_or_pin_firmware_identity(cfg)


def test_changed_firmware_withholds_checkpoint_and_blocks_resume(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    firmware = tmp_path / "main.vmem"
    firmware.write_bytes(b"checkpoint firmware image")
    monkeypatch.setattr(paths_module, "CONTROLLER_VMEM", str(firmware))
    cfg = AcqConfig(
        "aes1", 1, suffix="_firmware_checkpoint", seed=17,
    ).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    store.append(
        0, np.asarray([0.1, -0.1, 0.2, -0.2], dtype=np.float32),
        gen.key(0), gen.inp(0), bytes(16),
    )

    firmware.write_bytes(b"replacement firmware image with different bytes")
    with pytest.raises(RuntimeError, match="firmware host artifact changed"):
        store.checkpoint(1)
    store.close()

    # The row reached sidecar pages, but the last trusted metadata remains at
    # zero rows.  A new config re-hashes the replacement and refuses the old
    # dataset signature instead of inheriting a process-global cached digest.
    with np.load(cfg.base + "_meta.npz", allow_pickle=True) as meta:
        assert int(meta["done"]) == 0
    resumed_cfg = AcqConfig(
        "aes1", 1, suffix="_firmware_checkpoint", seed=17,
    ).validate()
    with pytest.raises(SystemExit, match=r"resume mismatch.*firmware"):
        TraceStore(resumed_cfg, samples=4, out_len=16, key_varies=False)


def test_csv_marker_binds_filename_bytes_hash_generation_and_completion(
        monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_csv_binding", rows=3)
    csv_path, sidecar_path = export_csv(cfg.base)
    summary = validate_csv_export(cfg.base)
    assert summary["csv_file"] == os.path.basename(csv_path)
    assert summary["csv_file_bytes"] == os.path.getsize(csv_path)
    with open(csv_path, "rb") as stream:
        assert summary["csv_file_sha256"] == hashlib.sha256(stream.read()).hexdigest()
    assert len(summary["generation_id"]) == 32
    assert summary["complete"] is False
    assert summary["native_row_integrity"]["digest_verified"] is True

    with open(csv_path, "ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="byte count mismatch"):
        validate_csv_export(cfg.base)

    export_csv(cfg.base)
    with open(sidecar_path, encoding="utf-8") as stream:
        changed = json.load(stream)
    changed["generation_id"] = "0" * 32
    with open(sidecar_path, "w", encoding="utf-8") as stream:
        json.dump(changed, stream)
    with pytest.raises(ValueError, match="binding digest mismatch"):
        validate_csv_export(cfg.base)


def test_h5_records_native_row_integrity(monkeypatch, tmp_path):
    h5py = pytest.importorskip("h5py")
    cfg = _dataset(monkeypatch, tmp_path, suffix="_h5_integrity")
    path = export_h5(cfg.base)
    with h5py.File(path, "r") as h5:
        integrity = json.loads(h5.attrs["native_row_integrity_json"])
    assert integrity["digest_verified"] is True
    assert integrity["checkpoint_rows"] == 3
