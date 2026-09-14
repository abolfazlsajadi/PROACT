"""Integrity gates for completed fixed-vs-random TVLA datasets."""
from __future__ import annotations

import numpy as np
import pytest

import acq.config as config_module
from acq.analysis import run_tvla
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.store import TraceStore


def _completed_tvla(tmp_path):
    cfg = AcqConfig("aes1", 8, tvla=True, tvla_per_class=4,
                    tvla_block_size=4, suffix="_integrity", seed=17).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    for index in range(cfg.traces):
        group = gen.tvla_group(index)
        trace = np.full(4, group * 0.01 + index * 1e-5, np.float32)
        store.append(index, trace, gen.key(index), gen.inp(index), bytes(16),
                     group=group, block=gen.tvla_block(index))
    store.checkpoint(cfg.traces)
    store.close()
    return cfg


def test_completed_tvla_refuses_corrupted_group_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = _completed_tvla(tmp_path)
    group = np.load(cfg.base + "_group.npy", mmap_mode="r+")
    group[0] = 1 - group[0]
    group.flush()
    with pytest.raises(ValueError, match="native row digest mismatch"):
        run_tvla(cfg.base, chunk=3)


def test_completed_tvla_refuses_wrong_fixed_input(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = _completed_tvla(tmp_path)
    group = np.load(cfg.base + "_group.npy", mmap_mode="r")
    fixed_index = int(np.flatnonzero(group == 0)[0])
    inputs = np.load(cfg.base + "_input.npy", mmap_mode="r+")
    inputs[fixed_index, 0] ^= 1
    inputs.flush()
    with pytest.raises(ValueError, match="native row digest mismatch"):
        run_tvla(cfg.base, chunk=3)


def test_tvla_refuses_all_undefined_zero_within_group_variance(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        "aes1", 8, tvla=True, tvla_per_class=4, tvla_block_size=4,
        suffix="_undefined_tvla", seed=29).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
    for index in range(cfg.traces):
        group = gen.tvla_group(index)
        trace = np.zeros(4, dtype=np.float32)
        store.append(index, trace, gen.key(index), gen.inp(index), bytes(16),
                     group=group, block=gen.tvla_block(index))
    store.checkpoint(cfg.traces)
    store.close()

    with pytest.raises(ValueError, match="undefined at every sample"):
        run_tvla(cfg.base, chunk=3)


def test_tvla_counts_mixed_infinite_statistic_as_threshold_crossing(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        "aes1", 8, tvla=True, tvla_per_class=4, tvla_block_size=4,
        suffix="_infinite_tvla", seed=31).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=2, out_len=16, key_varies=False)
    for index in range(cfg.traces):
        group = gen.tvla_group(index)
        # Sample 0 is constant within each group but differs between groups;
        # sample 1 has ordinary within-group variation.
        trace = np.asarray([0.02 * group, index * 1e-5], dtype=np.float32)
        store.append(index, trace, gen.key(index), gen.inp(index), bytes(16),
                     group=group, block=gen.tvla_block(index))
    store.checkpoint(cfg.traces)
    store.close()

    report = run_tvla(cfg.base, chunk=3)
    assert report["max_abs_t_is_infinite"] is True
    assert report["infinite_t_samples"] == 1
    assert report["samples_over_threshold"] >= 1
    assert np.isinf(report["max_abs_t"])
