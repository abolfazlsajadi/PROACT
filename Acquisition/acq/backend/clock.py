"""Board-clock providers used when another instrument records the waveform."""
from __future__ import annotations

from .husky import ChipWhispererCapture, validate_clock_status


class HuskyClockOutput:
    """Configure Husky HS2 as the ASIC clock and keep the connection alive."""

    def __init__(self, capture_factory=None):
        self._capture_factory = capture_factory
        self._capture = None
        self._status = {}

    def configure(self, clock_hz, adc_mul):
        factory = self._capture_factory or ChipWhispererCapture
        capture = factory(samples=100, clock_hz=clock_hz, platform="asic",
                          adc_mul=adc_mul)
        self._capture = capture
        try:
            capture.connect(clock_hz=clock_hz, platform="asic")
            status = dict(capture.clock_status())
            validate_clock_status(status, clock_hz, adc_mul)
        except BaseException:
            self.close()
            raise
        self._status = status
        return dict(status)

    @property
    def clock_status(self):
        return dict(self._status)

    def close(self):
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.disconnect()
            except Exception:
                pass
