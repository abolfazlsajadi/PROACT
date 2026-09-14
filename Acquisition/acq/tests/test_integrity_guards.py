"""Negative regressions for dataset identity, publication, and locking."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import acq.analysis as analysis_module
import acq.config as config_module
import acq.run as run_module
from acq.analysis import run_cpa, run_tvla
from acq.config import AcqConfig
from acq.exporters import export_h5
from acq.inputs import InputGen
from acq.locking import DatasetBusyError, bench_lock, dataset_lock
from acq.native import open_native
from acq.store import TraceStore


def _rewrite_npz(path, **updates):
    with np.load(path, allow_pickle=True) as old:
        values = {name: old[name].copy() for name in old.files}
    values.update(updates)
    tmp = path + ".rewrite.npz"
    np.savez(tmp, **values)
    os.replace(tmp, path)


def _dataset(monkeypatch, tmp_path, *, suffix="_integrity", tvla=False):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    traces = 8
    cfg = AcqConfig(
        "aes1", traces, suffix=suffix, seed=71, tvla=tvla,
        tvla_per_class=(traces // 2 if tvla else 0), tvla_block_size=4,
    ).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=8, out_len=16, key_varies=False)
    from proact_host.validation import aes128_encrypt_block
    rng = np.random.default_rng(27)
    for index in range(traces):
        inp = gen.inp(index)
        group = gen.tvla_group(index) if tvla else 255
        wave = rng.normal(group * 0.002 if tvla else 0, 0.01, 8).astype(np.float32)
        store.append(
            index, wave, gen.key(index), inp,
            aes128_encrypt_block(gen.key(index), inp),
            group=group, block=(gen.tvla_block(index) if tvla else -1))
    store.checkpoint(traces)
    store.close()
    return cfg


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_native_loader_rejects_same_shape_manifest_sidecar_swap(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_manifest_swap")
    meta_path = cfg.base + "_meta.npz"
    with np.load(meta_path, allow_pickle=True) as meta:
        manifest = json.loads(str(meta["row_manifest_json"].item()))
    manifest["input"]["file"], manifest["out"]["file"] = (
        manifest["out"]["file"], manifest["input"]["file"])
    _rewrite_npz(meta_path, row_manifest_json=json.dumps(manifest, sort_keys=True))

    with pytest.raises(ValueError, match="sidecar manifest names"):
        open_native(cfg.base)


def test_native_loader_rejects_scalar_signature_contradiction(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_scalar_native", tvla=True)
    _rewrite_npz(cfg.base + "_meta.npz", tvla=np.asarray(False))

    with pytest.raises(ValueError, match=(
            "metadata scalars disagree with capture signature: tvla")):
        open_native(cfg.base)


def test_native_loader_rejects_uart_divisor_signature_contradiction(
        monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_uart_divisor_scalar")
    _rewrite_npz(cfg.base + "_meta.npz", uart_fixed_divisor=np.asarray(99))

    with pytest.raises(ValueError, match=(
            "metadata scalars disagree with capture signature: uart_fixed_divisor")):
        open_native(cfg.base)


def test_native_loader_rejects_fixed_key_payload_contradiction(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_fixed_key_payload")
    _rewrite_npz(
        cfg.base + "_meta.npz", key=np.full(16, 0xA5, dtype=np.uint8))

    with pytest.raises(ValueError, match="stored fixed key disagrees"):
        open_native(cfg.base)


def test_resume_rejects_scalar_signature_contradiction(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_scalar_resume", tvla=True)
    _rewrite_npz(cfg.base + "_meta.npz", tvla=np.asarray(False))

    with pytest.raises(SystemExit, match=(
            "metadata scalars disagree with capture signature: tvla")):
        TraceStore(cfg, samples=8, out_len=16, key_varies=False)


def test_aes_cpa_rejects_dataset_for_different_aes_target(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_aes_target_guard")

    with pytest.raises(ValueError, match="AES metadata core 'aes1' != requested 'aes2'"):
        run_cpa(cfg.base, "aes2")


def test_aes_cpa_rejects_identical_nonconstant_zero_key_traces(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        "sw_rv", 20, fixed_key=bytes(16), suffix="_zero_variance_aes",
        seed=72).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=8, out_len=16, key_varies=False)
    from proact_host.validation import aes128_encrypt_block
    identical = np.asarray([0.0, 0.01, -0.02, 0.03, 0.0, -0.01, 0.02, 0.0],
                           dtype=np.float32)
    for index in range(cfg.traces):
        inp = gen.inp(index)
        store.append(index, identical, gen.key(index), inp,
                     aes128_encrypt_block(gen.key(index), inp))
    store.checkpoint(cfg.traces)
    store.close()

    with pytest.raises(ValueError, match="zero or numerically negligible"):
        run_cpa(cfg.base, "sw_rv")
    assert not os.path.exists(cfg.base + "_cpa.json")


@pytest.mark.parametrize("target", ("ascon", "xoodyak"))
def test_aead_cpa_rejects_identical_nonconstant_zero_key_traces(
        monkeypatch, tmp_path, target):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        target, 64, fixed_key=bytes(16), suffix=f"_zero_variance_{target}",
        seed=73).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=100, out_len=32, key_varies=False)
    from acq.targets.aead import AD_FIXED, PT_FIXED
    from proact_host import aead_soft
    encrypt = (aead_soft.ascon128_encrypt if target == "ascon"
               else aead_soft.xoodyak_encrypt)
    identical = np.sin(np.arange(100, dtype=np.float32) / 7.0) * 0.02
    for index in range(cfg.traces):
        nonce = gen.inp(index)
        ciphertext, tag = encrypt(
            gen.key(index), nonce, AD_FIXED, PT_FIXED)
        store.append(index, identical, gen.key(index), nonce, ciphertext + tag)
    store.checkpoint(cfg.traces)
    store.close()

    with pytest.raises(ValueError, match="zero or numerically negligible"):
        run_cpa(cfg.base, target)
    assert not os.path.exists(cfg.base + "_cpa.json")


@pytest.mark.parametrize("analysis", ("tvla", "cpa"))
def test_failed_analysis_generation_removes_stale_report_marker(
        monkeypatch, tmp_path, analysis):
    cfg = _dataset(monkeypatch, tmp_path, suffix=f"_stale_{analysis}", tvla=True)
    report_path = cfg.base + ("_tvla.json" if analysis == "tvla" else "_cpa.json")
    with open(report_path, "wb") as stream:
        stream.write(b"stale completion marker")
    original = analysis_module._write_npz

    def write_one_then_fail(path, **arrays):
        original(path, **arrays)
        raise RuntimeError("simulated publication failure")

    monkeypatch.setattr(analysis_module, "_write_npz", write_one_then_fail)
    operation = (lambda: run_tvla(cfg.base, chunk=3)) if analysis == "tvla" else (
        lambda: run_cpa(cfg.base, "aes1", random_group_only=True))
    with pytest.raises(RuntimeError, match="simulated publication failure"):
        operation()
    assert not os.path.exists(report_path)


def test_analysis_reports_bind_exact_generated_artifacts(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_bound_outputs", tvla=True)
    reports = (
        run_tvla(cfg.base, chunk=3),
        run_cpa(cfg.base, "aes1", random_group_only=True),
    )
    for report in reports:
        generation = report["publication"]
        assert len(generation["generation_id"]) == 32
        for identity in generation["artifacts"].values():
            assert identity["bytes"] == os.path.getsize(identity["path"])
            assert identity["sha256"] == _sha256(identity["path"])


def test_global_bench_contention_fails_before_target_or_backend_creation(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig("aes1", 1, samples=100, suffix="_bench_busy", seed=4).validate()
    touched = []
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: touched.append("target"))
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: touched.append("backend"))

    root = Path(__file__).resolve().parents[2]
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys\n"
            "from acq.locking import bench_lock\n"
            "with bench_lock('second process contention owner'):\n"
            " print('locked', flush=True)\n"
            " sys.stdin.readline()\n")],
        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(DatasetBusyError, match="acquisition bench is busy"):
            run_module.run(cfg)
    finally:
        _stdout, stderr = holder.communicate("\n", timeout=5)
        assert holder.returncode == 0, stderr
    assert touched == []
    assert not os.path.exists(cfg.base + "_meta.npz")


def test_dataset_lock_serializes_capture_analysis_and_export(monkeypatch, tmp_path):
    cfg = _dataset(monkeypatch, tmp_path, suffix="_dataset_busy", tvla=True)
    report_path = cfg.base + "_tvla.json"
    h5_path = cfg.base + ".h5"
    with open(report_path, "wb") as stream:
        stream.write(b"unchanged report")
    with open(h5_path, "wb") as stream:
        stream.write(b"unchanged h5")
    touched = []
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: touched.append("target"))

    with dataset_lock(cfg.base, "contention test owner"):
        with pytest.raises(DatasetBusyError, match="dataset .* is busy"):
            run_module.run(cfg)
        with pytest.raises(DatasetBusyError, match="dataset .* is busy"):
            run_tvla(cfg.base)
        with pytest.raises(DatasetBusyError, match="dataset .* is busy"):
            export_h5(cfg.base)
    assert touched == []
    assert Path(report_path).read_bytes() == b"unchanged report"
    assert Path(h5_path).read_bytes() == b"unchanged h5"
