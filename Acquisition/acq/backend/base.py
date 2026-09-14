"""Measurement-backend abstraction (L1.2).

The acquisition loop talks only to this interface, never to a specific instrument.
A backend digitises a power trace, triggered by the target. Concrete backends:
  * HuskyBackend  -- the connected ChipWhisperer-Husky (fully implemented, hw-tested)
  * ScopeBackend  -- a generic oscilloscope (documented stub; not hardware-verified)

Contract:
  configure(samples, offset, gain_db, clock_hz, adc_mul)  # once, expensive
  arm()                    # before each target run
  capture(timeout)         # -> np.float32 waveform in volts-ish [-0.5, 0.5]
  trig_count -> int        # measured trigger width (backend units); 0 if unknown
  close()
capture() MUST raise TimeoutError if no trigger arrived, so the loop can retry/recover
rather than store an invalid trace.
"""
from __future__ import annotations
import numpy as np


class Backend:
    name = "base"

    def configure(self, samples: int, offset: int, gain_db: float,
                  clock_hz: float, adc_mul: int) -> None:
        raise NotImplementedError

    def arm(self) -> None:
        raise NotImplementedError

    def capture(self, timeout: float = 5.0) -> np.ndarray:
        raise NotImplementedError

    @property
    def trig_count(self) -> int:
        return 0

    @property
    def last_capture_clipped(self) -> bool:
        """Whether the instrument reported ADC-rail contact on the last record."""
        return False

    @property
    def last_trigger_index_samples(self):
        """Per-row trigger location, or None when the backend cannot observe it."""
        return None

    @property
    def storage_metadata(self) -> dict:
        """Affine/unit information needed to interpret the normalized waveform."""
        return {"waveform_unit": "normalized_adc_code"}

    def set_samples(self, samples: int) -> None:
        """Change the capture length without a full reconfigure (used for auto-size)."""
        raise NotImplementedError

    def close(self) -> None:
        pass
