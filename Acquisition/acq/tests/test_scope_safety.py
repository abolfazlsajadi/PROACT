"""Offline safety checks for record sizing and oscilloscope completion."""
from __future__ import annotations

import json
import gc
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

from acq.backend.scope import ScopeBackend, _acq_done_ks
from acq.backend.husky import HuskyBackend
from acq.analysis import _sample_coordinates, _sampling_report
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.store import _instrument_metadata_compatible
from acq.tests.scope_simulator import StrictScopeInstrument
from acq.validate import ValidationError, preflight


@pytest.mark.parametrize(
    "kwargs",
    ({"samples": -1}, {"samples": 1}, {"offset": -1}, {"aead_trigger": 128}),
)
def test_invalid_record_and_trigger_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        AcqConfig("aes1", 2, **kwargs).validate()


def test_preflight_refuses_instrument_record_length_substitution():
    class Target:
        def run(self, _key, inp):
            return inp

        def expected(self, _key, inp):
            return inp

    class Backend:
        calls = 0

        def arm(self):
            pass

        def capture(self, timeout=3.0):
            self.calls += 1
            wave = np.linspace(-0.01, 0.01, 128, dtype=np.float32)
            wave[0] += self.calls * 1e-5
            return wave

        @property
        def trig_count(self):
            return 20

    cfg = AcqConfig("aes1", 10, samples=100, seed=1).validate()
    with pytest.raises(ValidationError, match="returned 128 samples"):
        preflight(Target(), Backend(), InputGen(cfg), cfg, n=4)


def test_preflight_retains_at_most_one_large_reference_record():
    """A large scope record must not be retained once for every preflight shot."""
    samples = 250_000

    class Target:
        def run(self, _key, inp):
            return inp

        def expected(self, _key, inp):
            return inp

    class Backend:
        calls = 0
        refs = []
        max_live_records = 0
        last_capture_clipped = False

        def arm(self):
            pass

        def capture(self, timeout=3.0):
            del timeout
            gc.collect()
            self.max_live_records = max(
                self.max_live_records,
                sum(reference() is not None for reference in self.refs))
            self.calls += 1
            wave = np.full(samples, 0.01, dtype=np.float32)
            wave[self.calls % samples] += self.calls * 1e-6
            self.refs.append(weakref.ref(wave))
            return wave

        @property
        def trig_count(self):
            return 100

    cfg = AcqConfig(
        "aes1", 20, samples=samples, seed=1,
        key_policy="fixed", input_policy="random").validate()
    backend = Backend()
    result = preflight(Target(), backend, InputGen(cfg), cfg, n=20)
    assert result["length"] == samples
    # The caller's previous local can still exist while the next capture is
    # constructed. A list of all 20 records would make this grow toward 20.
    assert backend.max_live_records <= 1


def test_keysight_status_query_failure_cannot_certify_a_capture():
    class Instrument:
        timeout = 8_000

        binary_reads = 0

        def query(self, _command):
            raise OSError("SCPI link lost")

        def query_binary_values(self, *_args, **_kwargs):
            self.binary_reads += 1
            return np.zeros(100, dtype=np.uint8)

    backend = ScopeBackend.__new__(ScopeBackend)
    backend.dialect = "keysight"
    backend.inst = Instrument()
    backend._pre = (1.0, 0.0, 0.0)
    with pytest.raises(TimeoutError, match="no confirmed trigger"):
        backend.capture(timeout=0)
    assert backend.inst.binary_reads == 0
    assert _acq_done_ks(backend.inst) is False


def test_keysight_codes_are_normalized_and_keep_voltage_calibration():
    class Instrument:
        timeout = 8_000

        def query(self, _command):
            if _command == ":TER?":
                return "1"
            if _command == ":WAVeform:PREamble?":
                return "0,0,3,1,5e-9,-5e-9,0,0.01,0.2,128"
            return "0"

        def query_binary_values(self, *_args, **_kwargs):
            return np.array([0, 128, 255], dtype=np.uint8)

    backend = ScopeBackend.__new__(ScopeBackend)
    backend.dialect = "keysight"
    backend.inst = Instrument()
    backend._pre = (0.01, 0.2, 128.0)
    backend._idn = "KEYSIGHT,FAKE,0,1"
    backend._model = "DSOX3034T"
    backend._requested_transfer_timeout_ms = None
    backend._last_clip = False
    backend._npts = 3
    backend._requested_sample_interval_s = 5e-9
    backend._nominal_trigger_index_samples = 1.0
    wave = backend.capture(timeout=0.1)
    np.testing.assert_allclose(wave, [-0.5, 0.0, 127 / 256])
    assert backend.last_capture_clipped is True
    metadata = backend.storage_metadata
    assert metadata["waveform_unit"] == "normalized_scope_adc_code"
    assert metadata["scope_volts_per_capture_unit"] == pytest.approx(2.56)
    assert metadata["scope_volts_offset"] == pytest.approx(0.2)


def _unopened_scope(dialect, instrument):
    backend = ScopeBackend.__new__(ScopeBackend)
    backend.resource = "FAKE::INSTR"
    backend.dialect = dialect
    backend.ch = 1
    backend.trig_source = "EXTernal"
    backend.trig_level = 1.5
    backend.inst = instrument
    backend.rm = None
    backend._pre = None
    backend._npts = None
    backend._requested_sample_interval_s = None
    backend._observed_sample_interval_s = None
    backend._observed_sample_count = None
    backend._last_clip = False
    backend._idn = ""
    backend._model = "DSOX3034T" if dialect == "keysight" else "MSO54B"
    backend._requested_transfer_timeout_ms = None
    backend._immutable_capture_state = None
    backend._open = lambda: (
        "KEYSIGHT TECHNOLOGIES,DSOX3034T,0,1" if dialect == "keysight"
        else "TEKTRONIX,MSO54B,0,1")
    return backend


def test_keysight_preamble_timing_is_recorded_and_refreshed():
    backend = _unopened_scope("keysight", StrictScopeInstrument("keysight"))
    backend.configure(250, 0, 25, 50e6, 4)
    metadata = backend.storage_metadata
    assert metadata["observed_sample_count"] == 250
    assert metadata["observed_sample_interval_s"] == pytest.approx(5e-9)
    assert metadata["requested_sample_interval_s"] == pytest.approx(5e-9)
    assert metadata["scope_horizontal_reference_observed"] == "LEFT"
    assert metadata["scope_horizontal_position_s_observed"] == 0.0
    backend.set_samples(500)
    assert backend.storage_metadata["observed_sample_count"] == 500


def test_tek_waveform_timing_queries_are_recorded_when_available():
    backend = _unopened_scope("tek", StrictScopeInstrument("tek"))
    backend.configure(300, 0, 25, 50e6, 4)
    metadata = backend.storage_metadata
    assert metadata["observed_sample_count"] == 300
    assert metadata["observed_sample_interval_s"] == pytest.approx(5e-9)
    assert metadata["scope_horizontal_position_percent_observed"] == 10.0
    assert metadata["scope_preamble_pt_off_index"] == 30.0


def test_husky_adc_timing_readback_is_fail_closed(monkeypatch):
    class ADC:
        samples = 100
        offset = 0

    class Capture:
        def __init__(self, **_kwargs):
            self.scope = SimpleNamespace(
                gain=SimpleNamespace(mode=None, db=None), adc=ADC())
            self.samples = None
            self.disconnected = False

        def connect(self, **_kwargs):
            pass

        def clock_status(self):
            return {"locked": True, "clkgen_freq_MHz": 50.0,
                    "adc_freq_MHz": 200.0}

        def disconnect(self):
            self.disconnected = True

    import acq.backend.husky as husky_module
    monkeypatch.setattr(husky_module, "ChipWhispererCapture", Capture)
    backend = HuskyBackend()
    backend.configure(120, 7, 25, 50e6, 4)
    metadata = backend.storage_metadata
    assert metadata["observed_sample_count"] == 120
    assert metadata["observed_offset_samples"] == 7
    assert metadata["observed_sample_interval_s"] == pytest.approx(5e-9)
    backend.set_samples(144)
    assert backend.storage_metadata["observed_sample_count"] == 144

    class BadADC:
        samples = 100

        @property
        def offset(self):
            return self._offset + 1

        @offset.setter
        def offset(self, value):
            self._offset = value

    class BadCapture(Capture):
        def __init__(self, **_kwargs):
            super().__init__()
            self.scope.adc = BadADC()

    monkeypatch.setattr(husky_module, "ChipWhispererCapture", BadCapture)
    with pytest.raises(RuntimeError, match="offset read back"):
        HuskyBackend().configure(120, 7, 25, 50e6, 4)


def test_analysis_prefers_observed_interval_and_husky_offset(tmp_path):
    path = tmp_path / "timing.npz"
    instrument = {
        "observed_sample_interval_s": 2e-9,
        "observed_sample_count": 4,
        "observed_offset_samples": 7,
    }
    np.savez(path, clock_mhz=50.0, adc_mul=4, offset=3, backend="husky",
             instrument_metadata_json=json.dumps(instrument))
    with np.load(path) as metadata:
        coords = _sample_coordinates(metadata, 4)
        report = _sampling_report(coords)
    assert coords["time_from_capture_start_ns"][1] == pytest.approx(2.0)
    assert coords["cycle_from_capture_start"][1] == pytest.approx(0.1)
    assert coords["time_from_requested_trigger_ns"][0] == pytest.approx(14.0)
    assert report["sample_interval_source"] == "instrument_readback"
    assert report["effective_offset_samples"] == 7
    assert report["sample_count_matches_native"] is True


def test_old_husky_instrument_metadata_can_gain_timing_once():
    old = {"waveform_unit": "chipwhisperer_normalized_adc_code",
           "physical_voltage_conversion_available": False}
    new = dict(old, requested_sample_count=100, observed_sample_count=100,
               observed_offset_samples=0, observed_sample_interval_s=5e-9,
               timing_observation_source="chipwhisperer_readback")
    assert _instrument_metadata_compatible(old, new, "husky")
    assert not _instrument_metadata_compatible(old, new, "scope")
    changed = dict(new, observed_offset_samples=1)
    assert not _instrument_metadata_compatible(new, changed, "husky")
