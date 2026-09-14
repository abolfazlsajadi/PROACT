"""Regression tests for paired raw-versus-aligned scope analysis."""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pytest

import acq.config as config_module
from acq.alignment import fractional_align_rows
from acq.analysis import _write_variant_comparison, run_cpa, run_requested, run_tvla
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.native import open_native
from acq.store import TraceStore


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scope_dataset(monkeypatch, tmp_path, *, prealigned=False):
    from proact_host.validation import aes128_encrypt_block

    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        target="aes1", traces=48, samples=100, backend="scope",
        scope_resource="TCPIP0::192.0.2.10::INSTR", scope_dialect="keysight",
        scope_clock_source="external", tvla=True, tvla_per_class=24,
        tvla_order="block", tvla_block_size=8, auto_cpa=True, chunk=7,
        seed=20260911, suffix="_scope_raw_aligned_analysis").validate()
    reference = 40.0
    instrument = {
        "waveform_unit": "normalized_scope_adc_code",
        "scope_dialect": "keysight",
        "scope_per_row_trigger_index_recorded": True,
        "scope_fractional_phase_alignment_applied": bool(prealigned),
        "scope_fractional_phase_alignment_method": "none",
        "scope_alignment_reference_trigger_index_samples": reference,
        "observed_sample_count": cfg.samples,
        "observed_sample_interval_s": 5e-9,
    }
    gen = InputGen(cfg)
    store = TraceStore(
        cfg, samples=cfg.samples, out_len=16, key_varies=False,
        instrument_meta=instrument)
    rng = np.random.default_rng(1205)
    phases = np.resize(np.asarray([-0.65, -0.2, 0.0, 0.35, 0.7]), cfg.traces)
    for i, phase in enumerate(phases):
        group = gen.tvla_group(i)
        inp = gen.inp(i)
        # This is deliberately the instrument-frame waveform.  Its feature is
        # shifted with the observed trigger phase and is never corrected before
        # append.  Independent noise keeps every Welch denominator defined.
        x = np.arange(cfg.samples, dtype=np.float64)
        centre = reference + phase
        pulse = np.exp(-0.5 * ((x - centre) / 0.7) ** 2)
        wave = rng.normal(0.0, 0.003, cfg.samples) + (0.025 + 0.02 * group) * pulse
        store.append(
            i, wave.astype(np.float32), gen.key(i), inp,
            aes128_encrypt_block(gen.key(i), inp), group=group,
            block=gen.tvla_block(i), scope_trigger_index=reference + phase)
    store.checkpoint(cfg.traces)
    store.close()
    return cfg


def test_fractional_alignment_extends_edges_without_wraparound():
    rows = np.asarray([[0, 2, 6, 20], [1, 5, 13, 29]], dtype=np.float32)
    aligned = fractional_align_rows(rows, np.asarray([0.5, -0.25]))
    np.testing.assert_array_equal(aligned[0], [1, 4, 13, 20])
    np.testing.assert_array_equal(aligned[1], [1, 4, 11, 25])
    assert aligned[0, -1] == rows[0, -1]
    assert aligned[1, 0] == rows[1, 0]


def test_scope_raw_and_aligned_outputs_are_paired_reproducible_and_read_only(
        monkeypatch, tmp_path):
    cfg = _scope_dataset(monkeypatch, tmp_path)
    trace_path = cfg.base + "_traces.npy"
    before_digest = _sha256(trace_path)
    with open_native(cfg.base) as native:
        before_rows = np.asarray(native.traces).copy()

    outputs = run_requested(cfg)

    assert outputs and all(os.path.exists(path) for path in outputs)
    assert cfg.base + "_tvla.json" in outputs
    assert cfg.base + "_scope_aligned_tvla.json" in outputs
    assert cfg.base + "_cpa.json" in outputs
    assert cfg.base + "_scope_aligned_cpa.json" in outputs
    assert cfg.base + "_tvla_raw_vs_scope_aligned.json" in outputs
    assert cfg.base + "_cpa_raw_vs_scope_aligned.json" in outputs

    with open(cfg.base + "_tvla.json", encoding="utf-8") as stream:
        raw_report = json.load(stream)
    with open(cfg.base + "_scope_aligned_tvla.json", encoding="utf-8") as stream:
        aligned_report = json.load(stream)
    assert raw_report["trace_preprocessing"]["variant"] == "raw"
    assert raw_report["trace_preprocessing"]["method"] == "none"
    assert aligned_report["trace_preprocessing"]["variant"] == \
        "scope_trigger_aligned"
    assert aligned_report["trace_preprocessing"]["wraparound"] is False
    assert aligned_report["trace_preprocessing"][
        "reference_trigger_index_samples"] == 40.0
    assert raw_report["source"]["done"] == aligned_report["source"]["done"] == 48
    raw_integrity = raw_report["source"]["native_row_integrity"]
    aligned_integrity = aligned_report["source"]["native_row_integrity"]
    assert raw_integrity == aligned_integrity
    assert raw_integrity["digest_verified"] is True
    assert len(raw_integrity["sha256"]) == 64
    with open(cfg.base + "_tvla_raw_vs_scope_aligned.json",
              encoding="utf-8") as stream:
        comparison = json.load(stream)
    assert comparison["source"]["same_native_rows"] is True
    assert comparison["source"]["native_row_integrity"] == raw_integrity
    assert comparison["raw"]["generation_id"] == \
        raw_report["publication"]["generation_id"]
    assert comparison["scope_trigger_aligned"]["generation_id"] == \
        aligned_report["publication"]["generation_id"]

    with np.load(cfg.base + "_tvla_arrays.npz") as raw_arrays, np.load(
            cfg.base + "_scope_aligned_tvla_arrays.npz") as aligned_arrays:
        assert not np.array_equal(
            raw_arrays["mean_fixed"], aligned_arrays["mean_fixed"])
        first_aligned_t = aligned_arrays["t"].copy()
        first_aligned_fixed = aligned_arrays["mean_fixed"].copy()

    # Re-running the named derived analysis must reproduce its numeric result.
    rerun = run_tvla(cfg.base, chunk=5, trace_variant="scope_trigger_aligned")
    assert not os.path.exists(
        cfg.base + "_tvla_raw_vs_scope_aligned.json")
    assert rerun["trace_preprocessing"] == aligned_report["trace_preprocessing"]
    with np.load(rerun["outputs"]["arrays"]) as repeated:
        # Streaming reduction order changes with chunk size. IEEE-754 rounding
        # may differ at the last bit while the scientific result is unchanged.
        np.testing.assert_allclose(
            repeated["t"], first_aligned_t, rtol=0.0, atol=1e-15)
        np.testing.assert_allclose(
            repeated["mean_fixed"], first_aligned_fixed,
            rtol=0.0, atol=1e-15)

    run_cpa(
        cfg.base, "aes1", random_group_only=True,
        trace_variant="scope_trigger_aligned")
    assert not os.path.exists(
        cfg.base + "_cpa_raw_vs_scope_aligned.json")

    assert _sha256(trace_path) == before_digest
    with open_native(cfg.base) as native:
        np.testing.assert_array_equal(native.traces, before_rows)


def test_aligned_analysis_rejects_native_rows_declared_prealigned(
        monkeypatch, tmp_path):
    cfg = _scope_dataset(monkeypatch, tmp_path, prealigned=True)
    with pytest.raises(ValueError, match="declared pre-aligned"):
        run_tvla(cfg.base, trace_variant="scope_trigger_aligned")


def test_paired_comparison_rejects_changed_report_generation(monkeypatch, tmp_path):
    cfg = _scope_dataset(monkeypatch, tmp_path)
    raw = run_tvla(cfg.base, trace_variant="raw")
    aligned = run_tvla(cfg.base, trace_variant="scope_trigger_aligned")
    path = aligned["outputs"]["report"]
    with open(path, encoding="utf-8") as stream:
        changed = json.load(stream)
    changed["publication"]["generation_id"] = "changed-after-analysis"
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(changed, stream)
    with pytest.raises(ValueError, match="generation changed"):
        _write_variant_comparison(cfg.base, "tvla", raw, aligned)
