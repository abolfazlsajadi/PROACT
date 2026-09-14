"""Resume + integrity (L1.4).

Resume is implicit in TraceStore (it reopens the memmap at `done`). This module adds
the integrity checks that make resume SAFE:
  * the input generator is deterministic in the trace index, so regenerating input[i]
    from the stored seed must reproduce the stored input -- if it doesn't, the seed or
    policy changed and continuing would mix two experiments. We verify a sample of
    already-stored rows before appending new ones.
  * report what will happen (fresh vs resume-from-N) up front.
"""
from __future__ import annotations
import numpy as np


def describe(store) -> str:
    if store.done == 0:
        return f"fresh campaign -> {store.N:,} traces x {store.S} samples"
    return (f"RESUME at {store.done:,}/{store.N:,} "
            f"({100*store.done/store.N:.1f}%) x {store.S} samples")


def verify_resume(store, gen, n_check: int = 64) -> None:
    """On resume, confirm the stored inputs match what the (seeded) generator would
    produce, so trace i keeps the identity it was captured with."""
    if store.done == 0:
        return
    if gen.key_varies:
        pass
    elif bytes(np.asarray(store.KEY, dtype=np.uint8)) != gen.key(0):
        raise SystemExit(
            "resume integrity FAILED: stored fixed key differs from the requested "
            "fixed key. Use the original key or a new --suffix.")
    if gen.is_tvla:
        groups = np.asarray(store.GRP[:store.done], dtype=np.uint8)
        blocks = np.asarray(store.BLK[:store.done], dtype=np.int64)
        if not np.isin(groups, (0, 1)).all():
            raise SystemExit(
                "resume integrity FAILED: stored TVLA group labels below done "
                "must contain only 0/1")
        if (blocks < 0).any():
            raise SystemExit(
                "resume integrity FAILED: stored TVLA block labels below done "
                "must be non-negative")
    idx = np.unique(np.linspace(0, store.done - 1, min(n_check, store.done)).astype(int))
    for i in idx:
        want = list(gen.inp(int(i)))
        got = list(store.INP[int(i)])
        if want != got:
            raise SystemExit(
                f"resume integrity FAILED at trace {i}: stored input != regenerated "
                f"input. The seed or input policy differs from the original run. "
                f"Use a new --suffix rather than corrupting this dataset.")
        if gen.key_varies:
            if list(gen.key(int(i))) != list(store.KEY[int(i)]):
                raise SystemExit(f"resume integrity FAILED: key mismatch at {i}")
        if gen.is_tvla:
            expected_group = gen.tvla_group(int(i))
            expected_block = gen.tvla_block(int(i))
            if int(store.GRP[int(i)]) != expected_group:
                raise SystemExit(
                    f"resume integrity FAILED: TVLA group mismatch at trace {i}")
            if int(store.BLK[int(i)]) != expected_block:
                raise SystemExit(
                    f"resume integrity FAILED: TVLA block mismatch at trace {i}")
