"""Disk-space estimate for a campaign (user request).

traces file : n_alloc * samples * 2 bytes (int16)
sidecars    : input + output + group + block + timestamp + optional varying key
Compares the requirement against free space on the data volume and reports both.
Sample counts are known exactly after preflight; before that we use a per-core
typical value (measured trigger widths) so the wizard/panel can show a number.
"""
from __future__ import annotations
import os
import shutil
from typing import Optional
from .config import DATA

# typical auto-sized sample counts per core (measured 2026-08-25), for the pre-run
# estimate before preflight fixes the exact value.
TYPICAL_SAMPLES = {"aes1": 596, "aes2": 616, "sw_rv": 946,
                   "sw_rv_masked": 946, "xoodyak": 276, "ascon": 386}


def _human(nbytes: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024 or u == "TB":
            return f"{nbytes:.1f} {u}"
        nbytes /= 1024


def estimate(cfg, samples: Optional[int] = None,
             out_len: Optional[int] = None) -> dict:
    n = cfg.traces
    S = samples or TYPICAL_SAMPLES.get(cfg.target, 600)
    ol = out_len if out_len is not None else (32 if cfg.is_aead else 16)
    key_per_row = 16 if cfg.key_policy == "random" else 0
    scope_phase_per_row = 8 if cfg.backend == "scope" else 0
    row_sidecars = 16 + ol + 1 + 8 + 8 + key_per_row + scope_phase_per_row
    fixed_overhead = 16 if cfg.key_policy != "random" else 0
    traces_b = n * S * 2
    meta_b = n * row_sidecars + fixed_overhead + 16_384
    total = traces_b + meta_b
    os.makedirs(DATA, exist_ok=True)
    free = shutil.disk_usage(DATA).free
    # subtract what an existing (resume) dataset already occupies, so the estimate is
    # the ADDITIONAL space still needed.
    existing = 0
    extensions = ["_traces.npy", "_meta.npz", "_input.npy", "_out.npy",
                  "_group.npy", "_block.npy", "_ts.npy", "_key.npy",
                  "_scope_trigger_index.npy"]
    for ext in extensions:
        p = cfg.base + ext
        if os.path.exists(p):
            st = os.stat(p)
            # NPY memmaps may be sparse. Count allocated filesystem blocks rather
            # than logical length so unwritten rows still reserve estimate budget.
            existing += getattr(st, "st_blocks", 0) * 512 or st.st_size
    remaining = max(0, total - existing)
    return dict(traces=n, samples=S, per_trace_b=S * 2 + row_sidecars,
                trace_samples_b=S * 2, row_sidecars_b=row_sidecars,
                traces_b=traces_b, meta_b=meta_b,
                total_b=total, already_b=existing, need_b=remaining, free_b=free,
                fits=remaining < free * 0.98, provisional=(samples is None),
                traces_h=_human(traces_b), total_h=_human(total), need_h=_human(remaining),
                free_h=_human(free))


def report_lines(e: dict) -> list[str]:
    tag = " (provisional; exact after preflight)" if e["provisional"] else ""
    lines = [
        f"per trace     : {e['per_trace_b']} B  ({e['samples']} samples){tag}",
        f"all {e['traces']:,} traces: {e['total_h']}  (traces {e['traces_h']} + metadata)",
    ]
    if e["already_b"]:
        lines.append(f"already on disk: {_human(e['already_b'])} (resume)")
        lines.append(f"still needed   : {e['need_h']}")
    lines.append(f"free on volume : {e['free_h']}")
    lines.append(("FITS" if e["fits"] else "*** DOES NOT FIT ***") +
                 f"  ({e['need_h']} needed vs {e['free_h']} free)")
    return lines
