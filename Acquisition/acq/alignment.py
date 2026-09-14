"""Read-only, reproducible trace preprocessing for scope analyses.

The authoritative native trace array contains the native int16 encoding of the
normalized instrument waveform, without phase correction.  This module exposes
an array-like view which applies the recorded sub-sample trigger correction only
while an analysis reads a block.  It never writes an aligned trace back to
native storage.
"""
from __future__ import annotations

import json

import numpy as np


RAW_VARIANT = "raw"
SCOPE_ALIGNED_VARIANT = "scope_trigger_aligned"
TRACE_VARIANTS = (RAW_VARIANT, SCOPE_ALIGNED_VARIANT)


def normalize_trace_variant(value):
    variant = str(value or RAW_VARIANT).strip().lower()
    aliases = {
        "native": RAW_VARIANT,
        "unaligned": RAW_VARIANT,
        "aligned": SCOPE_ALIGNED_VARIANT,
        "scope_aligned": SCOPE_ALIGNED_VARIANT,
    }
    variant = aliases.get(variant, variant)
    if variant not in TRACE_VARIANTS:
        raise ValueError(
            f"trace variant must be one of {', '.join(TRACE_VARIANTS)}")
    return variant


def _instrument_metadata(meta):
    if "instrument_metadata_json" not in meta.files:
        return {}
    try:
        value = json.loads(str(meta["instrument_metadata_json"].item()))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("native instrument metadata is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("native instrument metadata must be a JSON object")
    return value


def scope_alignment_context(meta, done, *, required):
    """Validate and describe per-row trigger positions for one scope dataset."""
    backend = str(meta["backend"].item()) if "backend" in meta.files else "unknown"
    if backend != "scope":
        if required:
            raise ValueError(
                "scope-trigger alignment is available only for scope datasets")
        return None
    instrument = _instrument_metadata(meta)
    required_keys = (
        "scope_alignment_reference_trigger_index_samples",
        "scope_per_row_trigger_index_recorded",
        "scope_fractional_phase_alignment_applied",
    )
    missing = [name for name in required_keys if name not in instrument]
    if "scope_trigger_index" not in meta.files:
        missing.append("scope_trigger_index sidecar")
    if missing:
        if required:
            raise ValueError(
                "scope-trigger alignment metadata is incomplete: " +
                ", ".join(missing))
        return None
    if instrument["scope_per_row_trigger_index_recorded"] is not True:
        raise ValueError(
            "scope metadata does not attest per-row trigger-index recording")
    if instrument["scope_fractional_phase_alignment_applied"] is not False:
        raise ValueError(
            "native scope waveforms are declared pre-aligned; raw-versus-aligned "
            "analysis requires instrument waveforms stored without preprocessing")
    try:
        reference = float(
            instrument["scope_alignment_reference_trigger_index_samples"])
    except (TypeError, ValueError) as exc:
        raise ValueError("scope alignment reference is not numeric") from exc
    if not np.isfinite(reference):
        raise ValueError("scope alignment reference must be finite")
    phase = meta["scope_trigger_index"]
    if getattr(phase, "ndim", None) != 1 or len(phase) < int(done):
        raise ValueError("scope trigger-index sidecar has an invalid shape")
    observed = np.asarray(phase[:int(done)], dtype=np.float64)
    if not np.isfinite(observed).all():
        raise ValueError("scope trigger-index sidecar contains non-finite rows")
    delta = observed - reference
    if np.any(np.abs(delta) > 1.0 + 1e-9):
        raise ValueError(
            "scope trigger-index correction exceeds the validated one-sample bound")
    return {
        "reference_trigger_index_samples": reference,
        "observed_trigger_index_min_samples": (
            float(observed.min()) if len(observed) else None),
        "observed_trigger_index_max_samples": (
            float(observed.max()) if len(observed) else None),
        "phase_delta_min_samples": float(delta.min()) if len(delta) else None,
        "phase_delta_max_samples": float(delta.max()) if len(delta) else None,
        "rows": int(done),
        "phase": phase,
    }


def fractional_align_rows(rows, phase_delta):
    """Apply bounded linear sub-sample shifts with edge extension, never wrap.

    Positive delta means the observed trigger was later than the durable
    reference, so each output sample interpolates toward its right neighbour.
    Negative delta uses the left neighbour.  The outside edge repeats its
    original value rather than wrapping the opposite edge into the record.
    """
    values = np.asarray(rows, dtype=np.float32)
    was_vector = values.ndim == 1
    if was_vector:
        values = values[None, :]
    if values.ndim != 2:
        raise ValueError("trace block must be one- or two-dimensional")
    delta = np.asarray(phase_delta, dtype=np.float64)
    if delta.ndim == 0:
        delta = delta.reshape(1)
    if delta.shape != (values.shape[0],):
        raise ValueError("one trigger-index delta is required per trace row")
    if not np.isfinite(delta).all() or np.any(np.abs(delta) > 1.0 + 1e-9):
        raise ValueError("fractional alignment deltas must be finite and within one sample")
    aligned = values.copy()
    if values.shape[1] >= 2:
        positive = delta > 1e-12
        if positive.any():
            amount = delta[positive].astype(np.float32)[:, None]
            aligned[positive, :-1] = (
                (np.float32(1.0) - amount) * values[positive, :-1] +
                amount * values[positive, 1:])
            aligned[positive, -1] = values[positive, -1]
        negative = delta < -1e-12
        if negative.any():
            amount = (-delta[negative]).astype(np.float32)[:, None]
            aligned[negative, 1:] = (
                (np.float32(1.0) - amount) * values[negative, 1:] +
                amount * values[negative, :-1])
            aligned[negative, 0] = values[negative, 0]
    return aligned[0] if was_vector else aligned


class ScopeAlignedTraceView:
    """Array-like read-only scope trace view used by streaming analyses."""

    def __init__(self, traces, phase, reference):
        self._traces = traces
        self._phase = phase
        self._reference = float(reference)
        self.shape = traces.shape
        self.dtype = np.dtype(np.float32)

    def __getitem__(self, index):
        columns = None
        rows = index
        if isinstance(index, tuple):
            if len(index) != 2:
                raise IndexError("aligned trace view accepts row and sample indices")
            rows, columns = index
        raw = np.asarray(self._traces[rows], dtype=np.float32)
        delta = np.asarray(self._phase[rows], dtype=np.float64) - self._reference
        aligned = fractional_align_rows(raw, delta)
        return aligned if columns is None else aligned[..., columns]


def trace_processing(meta, traces, done, variant):
    """Return the analysis view and complete preprocessing provenance."""
    variant = normalize_trace_variant(variant)
    backend = str(meta["backend"].item()) if "backend" in meta.files else "unknown"
    if variant == RAW_VARIANT:
        context = scope_alignment_context(meta, done, required=False)
        report = {
            "variant": RAW_VARIANT,
            "source": "authoritative unaligned native waveform (int16 encoded)",
            "method": "none",
            "native_waveforms_modified": False,
            "backend": backend,
            "scope_alignment_available": context is not None,
        }
        if context is not None:
            report.update({key: value for key, value in context.items()
                           if key != "phase"})
        return traces, report
    context = scope_alignment_context(meta, done, required=True)
    view = ScopeAlignedTraceView(
        traces, context["phase"], context["reference_trigger_index_samples"])
    report = {
        "variant": SCOPE_ALIGNED_VARIANT,
        "source": ("authoritative unaligned native waveform plus recorded "
                   "per-row trigger index"),
        "method": "bounded linear interpolation with nearest-edge extension",
        "wraparound": False,
        "native_waveforms_modified": False,
        "backend": backend,
        **{key: value for key, value in context.items() if key != "phase"},
    }
    return view, report


def analysis_base(base, variant):
    variant = normalize_trace_variant(variant)
    return base if variant == RAW_VARIANT else base + "_scope_aligned"
