"""ChipWhisperer-Husky backend (L1.2) -- wraps the proven ChipWhispererCapture.

Hardware-verified path (this is the instrument physically connected). All the tricky
bits (low gain to avoid clipping the leading samples, adc_mul=4, extclk x4) live in
ChipWhispererCapture; this adapter just exposes the Backend contract.
"""
from __future__ import annotations
import os, sys
import numpy as np
from .base import Backend

from ..paths import REPO, SWRV_FW  # noqa: F401
from proact_host.capture import ChipWhispererCapture   # noqa: E402


def validate_clock_status(status, requested_hz, adc_mul, tolerance_percent=2.0):
    """Validate only clock fields the installed ChipWhisperer API reports."""
    if status.get("locked") is False:
        raise RuntimeError("Husky target/ADC clock did not lock")
    expected = {
        "clkgen_freq_MHz": float(requested_hz) / 1e6,
        "target_clock_MHz": float(requested_hz) / 1e6,
        "adc_freq_MHz": float(requested_hz) * int(adc_mul) / 1e6,
    }
    for field, want in expected.items():
        if field not in status or status[field] is None:
            continue
        try:
            observed = float(status[field])
        except (TypeError, ValueError):
            continue
        error = abs(observed - want) / max(abs(want), 1e-12) * 100
        if error > tolerance_percent:
            raise RuntimeError(
                f"Husky {field} is {observed:g} MHz, expected {want:g} MHz "
                f"({error:.2f}% error)")


class HuskyBackend(Backend):
    name = "husky"

    def __init__(self):
        self._cap = None
        self._s = None
        self._clock_status = {}
        self._requested_samples = None
        self._observed_samples = None
        self._observed_offset = None
        self._last_clip = False

    def configure(self, samples, offset, gain_db, clock_hz, adc_mul):
        self._cap = ChipWhispererCapture(samples=max(samples, 100), clock_hz=clock_hz,
                                         platform="asic", gain_db=gain_db,
                                         gain_mode="low", adc_mul=adc_mul)
        self._cap.connect(clock_hz=clock_hz, platform="asic")
        self._s = self._cap.scope
        self._s.gain.mode = "low"
        self._s.gain.db = gain_db
        requested_samples = max(samples, 100)
        self._requested_samples = requested_samples
        try:
            self._s.adc.samples = requested_samples
            self._observed_samples = int(self._s.adc.samples)
            self._s.adc.offset = int(offset)
            self._observed_offset = int(self._s.adc.offset)
        except Exception as exc:
            self.close()
            raise RuntimeError(
                "Husky ADC samples/offset could not be applied and read back") from exc
        if self._observed_samples != requested_samples:
            self.close()
            raise RuntimeError(
                f"Husky ADC samples read back as {self._observed_samples}, "
                f"requested {requested_samples}")
        if self._observed_offset != int(offset):
            self.close()
            raise RuntimeError(
                f"Husky ADC offset read back as {self._observed_offset}, "
                f"requested {int(offset)}")
        self._clock_status = self._cap.clock_status()
        try:
            validate_clock_status(self._clock_status, clock_hz, adc_mul)
        except Exception:
            self.close()
            raise

    @property
    def clock_status(self):
        return dict(self._clock_status)

    def set_samples(self, samples):
        requested = max(samples, 100)
        self._cap.samples = requested
        try:
            self._s.adc.samples = requested
            observed = int(self._s.adc.samples)
        except Exception as exc:
            raise RuntimeError(
                "Husky ADC sample count could not be applied and read back") from exc
        if observed != requested:
            raise RuntimeError(
                f"Husky ADC samples read back as {observed}, requested {requested}")
        self._requested_samples = requested
        self._observed_samples = observed

    def arm(self):
        self._cap.arm()

    def capture(self, timeout=5.0):
        tr = self._cap.capture(timeout=timeout)     # raises TimeoutError on no trigger
        value = np.asarray(tr, dtype=np.float32)
        self._last_clip = bool(np.any(np.abs(value) > 0.499))
        return value

    @property
    def last_capture_clipped(self):
        return self._last_clip

    @property
    def storage_metadata(self):
        metadata = {
            "waveform_unit": "chipwhisperer_normalized_adc_code",
            "physical_voltage_conversion_available": False,
        }
        if self._requested_samples is not None:
            metadata["requested_sample_count"] = int(self._requested_samples)
        if self._observed_samples is not None:
            metadata["observed_sample_count"] = int(self._observed_samples)
        if self._observed_offset is not None:
            metadata["observed_offset_samples"] = int(self._observed_offset)
        adc_mhz = self._clock_status.get("adc_freq_MHz")
        try:
            adc_mhz = float(adc_mhz)
        except (TypeError, ValueError):
            adc_mhz = None
        if adc_mhz is not None and np.isfinite(adc_mhz) and adc_mhz > 0:
            metadata["observed_sample_interval_s"] = float(1e-6 / adc_mhz)
        if any(name in metadata for name in (
                "observed_sample_count", "observed_offset_samples",
                "observed_sample_interval_s")):
            metadata["timing_observation_source"] = "chipwhisperer_readback"
        return metadata

    @property
    def trig_count(self):
        try:
            return int(self._s.adc.trig_count)
        except Exception:
            return 0

    def close(self):
        try:
            self._cap.disconnect()
        except Exception:
            pass
