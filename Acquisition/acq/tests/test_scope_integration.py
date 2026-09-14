"""End-to-end, hardware-free acceptance tests for the oscilloscope path."""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

import acq.config as config_module
import acq.run as run_module
from acq.analysis import run_requested
from acq.backend.scope import ScopeBackend, _fractional_align_to_reference
from acq.config import AcqConfig
from acq.exporters import export_selected
from acq.inputs import InputGen
from acq.native import open_native
from acq.store import TraceStore, _instrument_metadata_compatible
from acq.validate import ValidationError
from acq.tests.scope_simulator import StrictScopeInstrument, simulated_pyvisa


class FakeTarget:
    out_len = 16

    def __init__(self, instrument):
        self.instrument = instrument
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True
        self.closed = False

    def close(self):
        self.closed = True

    def bringup_and_verify(self, _key):
        if not self.opened:
            raise RuntimeError("target not open")

    def select(self, _key):
        pass

    def configure_trigger(self):
        pass

    def reprogram(self):
        pass

    def expected(self, key, inp):
        from proact_host.validation import aes128_encrypt_block
        return aes128_encrypt_block(key, inp)

    def run(self, key, inp):
        out = self.expected(key, inp)
        self.instrument.trigger(key, inp)
        return out


class FakeUI:
    def __init__(self):
        self.notes = []
        self.summary_value = None
        self.closed = False

    def log(self, message):
        self.notes.append(str(message))

    def note(self, message):
        self.notes.append(str(message))

    def start(self, _total, _initial):
        pass

    def update(self, _advance, _stats):
        pass

    def summary(self, value):
        self.summary_value = dict(value)

    def close(self):
        self.closed = True


class FakeClockDriver:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def configure(self, clock_hz, adc_mul):
        self.events.append(("clock-configure", clock_hz, adc_mul))
        return {"locked": True, "clkgen_freq_MHz": clock_hz / 1e6,
                "target_clock_MHz": clock_hz / 1e6,
                "adc_freq_MHz": clock_hz * adc_mul / 1e6}

    def close(self):
        self.closed = True
        self.events.append(("clock-close",))


def make_backend(monkeypatch, instrument, *, dialect="auto",
                 clock_source="external", clock_driver=None,
                 transfer_timeout_ms=None):
    pyvisa, manager = simulated_pyvisa(instrument)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=instrument.resource, dialect=dialect, channel=1,
        trig_source="EXTernal", clock_source=clock_source,
        clock_driver_factory=((lambda: clock_driver) if clock_driver else None),
        transfer_timeout_ms=transfer_timeout_ms)
    return backend, manager


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_strict_single_shot_decode_timing_and_calibration(monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, sample_interval_s=4e-9)
    backend, manager = make_backend(monkeypatch, instrument)
    backend.configure(100, 0, 25, 50e6, 4)
    assert backend.dialect == dialect
    assert instrument.trigger_normal is True
    assert instrument.measure_channel == 1
    assert instrument.acquisition_mode == ("NORMAL" if dialect == "keysight" else "SAMPLE")
    assert instrument.data_start == 1 and instrument.data_stop == 100
    if dialect == "keysight":
        assert instrument.waveform_format == "BYTE"
        assert instrument.points_mode == "RAW"
    else:
        assert instrument.waveform_encoding == "SRIBINARY"
        assert instrument.waveform_width == 1
    backend.arm()
    instrument.trigger(bytes(range(16)), bytes(range(16)))
    expected_raw = instrument._wave.copy()
    wave = backend.capture(timeout=0.1)
    expected = ((expected_raw.astype(np.float32) - 128) / 256
                if dialect == "keysight" else expected_raw.astype(np.float32) / 256)
    np.testing.assert_allclose(wave, expected)
    metadata = backend.storage_metadata
    assert metadata["observed_sample_count"] == 100
    # Configure requested 200 MS/s; the strict simulator reports what it accepted.
    assert metadata["observed_sample_interval_s"] == pytest.approx(5e-9)
    assert metadata["scope_volts_per_capture_unit"] == pytest.approx(0.512)
    assert backend.last_capture_clipped is False
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_clipping_and_missed_trigger_fail_closed(monkeypatch, dialect):
    clipped = StrictScopeInstrument(dialect, clip=True)
    backend, _ = make_backend(monkeypatch, clipped, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    clipped.trigger(bytes(16), bytes(16))
    backend.capture(timeout=0.1)
    assert backend.last_capture_clipped is True
    backend.close()

    waiting = StrictScopeInstrument(dialect, never_complete=True)
    backend, _ = make_backend(monkeypatch, waiting, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    waiting.trigger(bytes(16), bytes(16))
    with pytest.raises(TimeoutError, match="no trigger|no confirmed trigger"):
        backend.capture(timeout=0)
    assert waiting.binary_reads == 0
    backend.close()


def test_status_errors_never_authorize_binary_read(monkeypatch):
    keysight = StrictScopeInstrument("keysight")
    backend, _ = make_backend(monkeypatch, keysight)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    keysight.trigger(bytes(16), bytes(16))
    keysight.status_error = OSError("status transport failed")
    with pytest.raises(TimeoutError, match="status transport failed"):
        backend.capture(timeout=0)
    assert keysight.binary_reads == 0
    backend.close()

    tek = StrictScopeInstrument("tek")
    backend, _ = make_backend(monkeypatch, tek)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    tek.trigger(bytes(16), bytes(16))
    tek.status_error = OSError("status transport failed")
    with pytest.raises(OSError, match="status transport failed"):
        backend.capture(timeout=0)
    assert tek.binary_reads == 0
    backend.close()


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_target_cannot_run_until_scope_reports_trigger_ready(monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, never_arm=True)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    backend._arm_timeout_s = 0
    with pytest.raises(TimeoutError, match="trigger-ready"):
        backend.arm()
    assert instrument.binary_reads == 0
    backend.close()
    assert instrument.closed and manager.closed


def test_tek_armed_state_is_not_treated_as_trigger_ready(monkeypatch):
    instrument = StrictScopeInstrument("tek", ready_after_queries=3)
    backend, _ = make_backend(monkeypatch, instrument, dialect="tek")
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    assert instrument.trigger_state_queries == 4
    assert instrument.armed and instrument.binary_reads == 0
    backend.close()


@pytest.mark.parametrize(
    "dialect,requested,applied,level_command",
    [("keysight", "EXTernal", "EXTernal", ":TRIGger:EDGE:LEVel 1.5"),
     ("keysight", "CHANnel2", "CHANnel2", ":TRIGger:EDGE:LEVel 1.5"),
     ("tek", "EXTernal", "AUXiliary", "TRIGger:AUXLevel 1.5"),
     ("tek", "CHANnel2", "CH2", "TRIGger:A:LEVel:CH2 1.5")])
def test_trigger_source_is_mapped_and_read_back(
        monkeypatch, dialect, requested, applied, level_command):
    instrument = StrictScopeInstrument(dialect)
    pyvisa, manager = simulated_pyvisa(instrument)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=instrument.resource, dialect=dialect, channel=1,
        trig_source=requested, clock_source="external")
    backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.trigger_source == applied
    assert ("write", level_command) in instrument.events
    metadata = backend.storage_metadata
    assert metadata["scope_trigger_source_requested"] == requested
    assert metadata["scope_trigger_source_applied"] == applied
    assert metadata["scope_trigger_level_observed_v"] == pytest.approx(1.5)
    assert metadata["scope_trigger_path_coupling"] == "DC"
    if requested == "EXTernal" and dialect == "keysight":
        assert metadata["scope_trigger_external_input_impedance_ohm"] == pytest.approx(1e6)
        assert metadata["scope_trigger_external_probe_attenuation"] == pytest.approx(1.0)
        assert metadata["scope_trigger_external_range_v"] == pytest.approx(8.0)
    if requested == "EXTernal" and dialect == "tek":
        assert metadata["scope_trigger_aux_attached_probe_gain"] == pytest.approx(1.0)
        assert metadata["scope_trigger_aux_probe_resistance_ohm"] == pytest.approx(1e6)
        assert not any("AUXIn:PROBEFunc:EXTAtten" in event[1]
                       for event in instrument.events)
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "dialect,source,level_command",
    [("keysight", "CHANnel2", ":TRIGger:EDGE:LEVel 0.8"),
     ("tek", "EXTernal", "TRIGger:AUXLevel 0.8")],
)
def test_custom_trigger_level_is_applied_read_back_and_recorded(
        monkeypatch, dialect, source, level_command):
    instrument = StrictScopeInstrument(dialect)
    pyvisa, manager = simulated_pyvisa(instrument)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=instrument.resource, dialect=dialect, channel=1,
        trig_source=source, trig_level=0.8, clock_source="external")
    backend.configure(100, 0, 25, 50e6, 4)
    assert ("write", level_command) in instrument.events
    assert backend.storage_metadata["scope_trigger_level_v"] == pytest.approx(0.8)
    assert backend.storage_metadata["scope_trigger_level_observed_v"] == pytest.approx(0.8)
    backend.close()
    assert instrument.closed and manager.closed


def test_trigger_source_readback_mismatch_fails_setup_closed(monkeypatch):
    instrument = StrictScopeInstrument("keysight")
    original_query = instrument.query

    def wrong_source(command):
        if command == ":TRIGger:EDGE:SOURce?":
            return "CHAN1"
        return original_query(command)

    instrument.query = wrong_source
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    with pytest.raises(RuntimeError, match="trigger source readback mismatch"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


def test_trigger_level_readback_mismatch_fails_setup_closed(monkeypatch):
    instrument = StrictScopeInstrument("tek")
    original_query = instrument.query

    def wrong_level(command):
        if command == "TRIGger:AUXLevel?":
            return "0.0"
        return original_query(command)

    instrument.query = wrong_level
    backend, manager = make_backend(monkeypatch, instrument, dialect="tek")
    with pytest.raises(RuntimeError, match="trigger level readback mismatch"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "dialect,query,bad_value,field",
    [("keysight", ":TRIGger:MODE?", "PULSE", "trigger mode"),
     ("keysight", ":TRIGger:EDGE:SLOPe?", "NEGATIVE", "trigger slope"),
     ("tek", "TRIGger:A:TYPe?", "WIDTH", "trigger type"),
     ("tek", "TRIGger:A:EDGE:SLOpe?", "FALL", "trigger slope")],
)
def test_trigger_type_and_slope_are_read_back_fail_closed(
        monkeypatch, dialect, query, bad_value, field):
    instrument = StrictScopeInstrument(dialect)
    original_query = instrument.query

    def wrong_setting(command):
        return bad_value if command == query else original_query(command)

    instrument.query = wrong_setting
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    with pytest.raises(RuntimeError, match=field + " readback mismatch"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "resource,expect_read_termination",
    [("TCPIP0::192.0.2.10::hislip0::INSTR", None),
     ("TCPIP0::192.0.2.10::5025::SOCKET", "\n")],
)
def test_auto_detection_and_visa_termination(monkeypatch, resource,
                                             expect_read_termination):
    instrument = StrictScopeInstrument("keysight", resource=resource)
    backend, manager = make_backend(monkeypatch, instrument)
    backend.configure(100, 0, 25, 50e6, 4)
    assert backend.dialect == "keysight"
    assert manager.backend_argument == "@py"
    assert manager.opened_resource == resource
    assert instrument.read_termination == expect_read_termination
    assert instrument.write_termination == "\n"
    backend.close()


def test_auto_detection_refuses_unknown_instrument(monkeypatch):
    instrument = StrictScopeInstrument(
        "tek", idn="ACME,DMM-1000,SIM,1.0")
    backend, manager = make_backend(monkeypatch, instrument)
    with pytest.raises(RuntimeError, match="cannot auto-detect"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "dialect,idn",
    [("keysight", "KEYSIGHT TECHNOLOGIES,MSOX6004A,SIM,1.0"),
     ("tek", "TEKTRONIX,DPO7254C,SIM,1.0")],
)
def test_vendor_match_still_refuses_unsupported_scope_model(
        monkeypatch, dialect, idn):
    instrument = StrictScopeInstrument(dialect, idn=idn)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    with pytest.raises(RuntimeError, match="unsupported instrument model"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


def test_tek_aux_requires_a_model_with_an_aux_input(monkeypatch):
    unsupported = StrictScopeInstrument(
        "tek", idn="TEKTRONIX,MSO54,SIM,1.0")
    backend, manager = make_backend(monkeypatch, unsupported, dialect="tek")
    with pytest.raises(RuntimeError, match="no supported AUX trigger input"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert not any(
        event == ("write", "AUXIn:PROBEFunc:EXTAtten 1")
        for event in unsupported.events)
    assert unsupported.closed and manager.closed

    channel_scope = StrictScopeInstrument(
        "tek", idn="TEKTRONIX,MSO54,SIM,1.0")
    pyvisa, manager = simulated_pyvisa(channel_scope)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=channel_scope.resource, dialect="tek", channel=1,
        trig_source="CH2", clock_source="external")
    backend.configure(100, 0, 25, 50e6, 4)
    assert backend.storage_metadata["scope_model"] == "MSO54"
    backend.close()
    assert channel_scope.closed and manager.closed


def test_tek_aux_requires_direct_one_to_one_probe_gain(monkeypatch):
    instrument = StrictScopeInstrument("tek")
    instrument.aux_probe_gain = 0.1
    backend, manager = make_backend(monkeypatch, instrument, dialect="tek")
    with pytest.raises(RuntimeError, match="AUX attached-probe gain readback mismatch"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "dialect,clock_source", [("keysight", "husky"), ("tek", "external")])
def test_end_to_end_scope_capture_storage_export_tvla_cpa(
        monkeypatch, tmp_path, dialect, clock_source):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    events = []
    instrument = StrictScopeInstrument(dialect)
    clock = FakeClockDriver(events) if clock_source == "husky" else None
    backend, manager = make_backend(
        monkeypatch, instrument, clock_source=clock_source, clock_driver=clock)
    target = FakeTarget(instrument)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        target="aes1", traces=32, samples=100, backend="scope",
        scope_resource=instrument.resource, scope_clock_source=clock_source,
        scope_dialect="auto", tvla=True, tvla_per_class=16,
        tvla_block_size=8, auto_cpa=True, chunk=8, seed=20260911,
        suffix=f"_{dialect}_{clock_source}_scope_e2e")
    ui = FakeUI()
    result = run_module.run(
        cfg, ui=ui, selected_formats=("native", "h5", "csv"))
    assert result["done"] == 32
    assert any("trigger fired; width unavailable from instrument" in note
               for note in ui.notes)
    assert target.closed and instrument.closed and manager.closed and ui.closed
    if clock is not None:
        assert events[0][0] == "clock-configure"
        assert events[-1] == ("clock-close",)

    with open_native(cfg.base) as native:
        assert native.done == 32 and native.traces.shape == (32, 100)
        metadata = json.loads(str(native["instrument_metadata_json"].item()))
        assert metadata["scope_dialect"] == dialect
        assert metadata["observed_sample_count"] == 100
        assert metadata["board_clock_source"] == clock_source
        assert metadata["scope_raw_encoding"] == (
            "unsigned_uint8_centered_at_128" if dialect == "keysight"
            else "signed_int8")
        assert metadata["scope_fractional_phase_alignment_applied"] is False
        assert metadata["scope_native_waveform_processing"] == \
            "raw_normalized_adc_codes_unaligned"
        phase = np.asarray(native["scope_trigger_index"][:native.done])
        assert phase.shape == (32,) and np.isfinite(phase).all()

    analysis_outputs = run_requested(cfg)
    assert analysis_outputs and all(os.path.exists(path) for path in analysis_outputs)
    export_outputs = export_selected(cfg.base, ("h5", "csv"))
    assert len(export_outputs) == 3 and all(os.path.exists(path) for path in export_outputs)
    with open(cfg.base + "_csv_meta.json", encoding="utf-8") as stream:
        csv_metadata = json.load(stream)
    assert csv_metadata["instrument_metadata"]["scope_dialect"] == dialect
    assert "scope_trigger_index" in csv_metadata["optional_row_columns"]
    import h5py
    with h5py.File(cfg.base + ".h5", "r") as h5:
        h5_metadata = json.loads(h5.attrs["instrument_metadata_json"])
        assert h5_metadata["observed_sample_count"] == 100
        assert h5["scope_trigger_index"].shape == (32,)


def test_scope_checkpoint_state_drift_withholds_unverified_rows(
        monkeypatch, tmp_path):
    """A read-to-clear verifier failure must never be retried into a commit."""
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument)
    target = FakeTarget(instrument)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        target="aes1", traces=4, samples=100, backend="scope",
        scope_clock_source="external", scope_resource=instrument.resource,
        chunk=2, seed=20260912, suffix="_scope_checkpoint_drift")

    original_verifier = backend.verify_capture_state
    verification_count = 0

    def drift_on_first_periodic_checkpoint():
        nonlocal verification_count
        verification_count += 1
        # Call 1 is the pre-storage gate. Call 2 follows two appended rows.
        if verification_count == 2:
            instrument.channel_scale_v[1] *= 2
        return original_verifier()

    backend.verify_capture_state = drift_on_first_periodic_checkpoint
    with pytest.raises(RuntimeError, match="immutable capture settings changed"):
        run_module.run(cfg, ui=FakeUI())
    with np.load(cfg.base + "_meta.npz") as metadata:
        assert int(metadata["done"]) == 0
    assert verification_count == 2
    assert target.closed and instrument.closed and manager.closed


def test_wrong_record_length_aborts_preflight_and_closes_everything(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    events = []
    instrument = StrictScopeInstrument("keysight", waveform_length_override=96)
    clock = FakeClockDriver(events)
    backend, manager = make_backend(
        monkeypatch, instrument, clock_source="husky", clock_driver=clock)
    target = FakeTarget(instrument)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        "aes1", 4, samples=100, backend="scope", scope_clock_source="husky",
        scope_resource=instrument.resource, seed=1, suffix="_bad_scope_length")
    with pytest.raises(ValidationError, match="96 samples"):
        run_module.run(cfg, ui=FakeUI())
    assert target.closed and instrument.closed and manager.closed and clock.closed
    assert not os.path.exists(cfg.base + "_meta.npz")


def test_scope_zero_samples_uses_and_records_2000_point_pilot(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument)
    target = FakeTarget(instrument)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        "aes1", 2, samples=0, backend="scope", scope_clock_source="external",
        scope_resource=instrument.resource, seed=2, suffix="_scope_auto_pilot")
    result = run_module.run(cfg, ui=FakeUI())
    assert result["done"] == 2 and result["samples"] == 2000
    with open_native(cfg.base) as native:
        assert native.traces.shape == (2, 2000)
        metadata = json.loads(str(native["instrument_metadata_json"].item()))
        assert metadata["requested_sample_count"] == 2000
        assert metadata["observed_sample_count"] == 2000
    assert target.closed and instrument.closed and manager.closed


def test_stale_scope_buffer_aborts_and_checkpoints_without_reprogramming(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    # Preflight consumes 26 records. Freeze at the first stored record afterward.
    instrument = StrictScopeInstrument("keysight", freeze_after=26)
    backend, manager = make_backend(monkeypatch, instrument)
    target = FakeTarget(instrument)
    reprogrammed = []
    target.reprogram = lambda: reprogrammed.append(True)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        "aes1", 30, samples=100, backend="scope", scope_clock_source="external",
        scope_resource=instrument.resource, seed=3, suffix="_stale_scope")
    with pytest.raises(RuntimeError, match="waveform-quality rejects"):
        run_module.run(cfg, ui=FakeUI())
    with np.load(cfg.base + "_meta.npz") as metadata:
        assert int(metadata["done"]) == 2
        assert int(metadata["stale_rejects"]) == 20
    assert not reprogrammed
    assert target.closed and instrument.closed and manager.closed


def test_scope_status_faults_abort_as_measurement_errors_without_target_recovery(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument)
    capture = backend.capture
    backend.capture = lambda timeout=5.0: capture(timeout=0)
    target = FakeTarget(instrument)
    normal_run = target.run

    def run_then_break_status(key, inp):
        out = normal_run(key, inp)
        if instrument.capture_count > 26:  # after six warm-up + twenty preflight rows
            instrument.status_error = OSError("simulated scope status loss")
        return out

    target.run = run_then_break_status
    reprogrammed = []
    target.reprogram = lambda: reprogrammed.append(True)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    cfg = AcqConfig(
        "aes1", 4, samples=100, backend="scope", scope_clock_source="external",
        scope_resource=instrument.resource, seed=4, suffix="_scope_status_fault")
    with pytest.raises(RuntimeError, match="waveform-quality rejects"):
        run_module.run(cfg, ui=FakeUI())
    with np.load(cfg.base + "_meta.npz") as metadata:
        assert int(metadata["done"]) == 0
        assert int(metadata["instrument_rejects"]) == 20
    assert not reprogrammed
    assert target.closed and instrument.closed and manager.closed


def test_scope_resume_refuses_changed_observed_calibration(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    instrument = StrictScopeInstrument("keysight")
    backend, _ = make_backend(monkeypatch, instrument)
    backend.configure(100, 0, 25, 50e6, 4)
    cfg = AcqConfig(
        "aes1", 2, samples=100, backend="scope", scope_clock_source="external",
        scope_resource=instrument.resource, seed=8, suffix="_scope_resume_meta")
    original = backend.storage_metadata
    store = TraceStore(cfg, 100, 16, False, instrument_meta=original)
    store.close()
    changed = dict(original, scope_volts_per_capture_unit=9.99)
    with pytest.raises(SystemExit, match="waveform calibration changed"):
        TraceStore(cfg, 100, 16, False, instrument_meta=changed)
    backend.close()


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_scope_resume_uses_one_durable_alignment_reference_across_sessions(
        monkeypatch, tmp_path, dialect):
    """A new asynchronous preflight phase must not split one dataset's frame."""
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig(
        "aes1", 2, samples=100, backend="scope",
        scope_clock_source="external", scope_dialect=dialect,
        scope_resource="TCPIP0::192.0.2.10::hislip0::INSTR",
        seed=81, suffix=f"_{dialect}_scope_resume_phase")

    first_instrument = StrictScopeInstrument(dialect)
    first_instrument.current_trigger_phase = 0.35
    first, _ = make_backend(monkeypatch, first_instrument, dialect=dialect)
    first.configure(100, 0, 25, 50e6, 4)
    first_meta = first.storage_metadata
    assert first_meta["scope_alignment_reference_trigger_index_samples"] == 10.0
    store = TraceStore(cfg, 100, 16, False, instrument_meta=first_meta)
    store.append(
        0, np.zeros(100, dtype=np.float32), bytes(16), bytes(16), bytes(16),
        scope_trigger_index=10.35)
    store.checkpoint(1)
    store.close()
    first.close()

    second_instrument = StrictScopeInstrument(dialect)
    second_instrument.current_trigger_phase = -0.4
    second, _ = make_backend(monkeypatch, second_instrument, dialect=dialect)
    second.configure(100, 0, 25, 50e6, 4)
    second_meta = second.storage_metadata
    assert second_meta["scope_alignment_reference_trigger_index_samples"] == 10.0
    resumed = TraceStore(cfg, 100, 16, False, instrument_meta=second_meta)
    assert resumed.done == 1
    resumed.close()
    second.close()


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_invalid_scope_calibration_is_rejected_during_setup(monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, bad_calibration=True)
    backend, manager = make_backend(monkeypatch, instrument)
    with pytest.raises(RuntimeError, match="calibration"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_stopped_without_trigger_is_rejected_before_waveform_read(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, stop_without_trigger=True)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    instrument.trigger(bytes(16), bytes(16))
    with pytest.raises(TimeoutError, match="stopped without"):
        backend.capture(timeout=0.1)
    assert instrument.binary_reads == 0
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_measurement_and_analog_trigger_channels_are_enabled_and_recorded(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect)
    instrument.channel_coupling[2] = "AC"
    instrument.channel_impedance_ohm[2] = 50.0
    pyvisa, manager = simulated_pyvisa(instrument)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=instrument.resource, dialect=dialect, channel=1,
        trig_source="CHANnel2", clock_source="external")
    backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.channel_display[1] is True
    assert instrument.channel_display[2] is True
    assert instrument.channel_coupling[2] == "DC"
    assert instrument.channel_impedance_ohm[2] == pytest.approx(1e6)
    metadata = backend.storage_metadata
    for role, channel in (("measurement", 1), ("trigger", 2)):
        prefix = f"scope_{role}_channel"
        assert metadata[f"{prefix}_number"] == channel
        assert metadata[f"{prefix}_displayed"] is True
        assert metadata[f"{prefix}_coupling"] == "DC"
        assert metadata[f"{prefix}_input_impedance_ohm"] == pytest.approx(1e6)
        if dialect == "keysight":
            assert metadata[f"{prefix}_probe_attenuation"] == pytest.approx(1.0)
        else:
            assert metadata[f"{prefix}_external_attenuation_multiplier"] == \
                pytest.approx(1.0)
            assert metadata[f"{prefix}_attached_probe_gain"] == pytest.approx(1.0)
        assert metadata[f"{prefix}_vertical_scale_v_per_div"] == pytest.approx(0.1)
        assert metadata[f"{prefix}_offset_v"] == pytest.approx(0.0)
    if dialect == "keysight":
        assert metadata["scope_measurement_channel_vertical_range_v"] == pytest.approx(0.8)
        assert metadata["scope_measurement_channel_bandwidth_limit_enabled"] is False
    else:
        assert metadata["scope_measurement_channel_bandwidth_hz"] == pytest.approx(1e9)
        assert instrument.device_event_status_enable == 255
        assert ("write", "DESE 255") in instrument.events
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect,display_query", [
    ("keysight", ":CHANnel1:DISPlay?"),
    ("tek", "DISPlay:WAVEView1:CH1:STATE?"),
])
def test_disabled_measurement_channel_readback_fails_setup_closed(
        monkeypatch, dialect, display_query):
    instrument = StrictScopeInstrument(dialect)
    original_query = instrument.query

    def disabled(command):
        return "0" if command == display_query else original_query(command)

    instrument.query = disabled
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    with pytest.raises(RuntimeError, match="channel display state readback mismatch"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_scope_setup_error_queue_fails_closed(monkeypatch, dialect):
    instrument = StrictScopeInstrument(
        dialect, setup_error='-113,"Undefined header"')
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    with pytest.raises(RuntimeError, match="rejected SCPI setup"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


def test_tek_dese_mask_is_verified_before_setup(monkeypatch):
    instrument = StrictScopeInstrument("tek")
    original_query = instrument.query

    def stale_mask(command):
        return "0" if command == "DESE?" else original_query(command)

    instrument.query = stale_mask
    backend, manager = make_backend(monkeypatch, instrument, dialect="tek")
    with pytest.raises(RuntimeError, match="device-event status enable mask"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("points", [596, 616, 999, 5_000_000, 8_000_001])
def test_keysight_unsupported_raw_record_lengths_fail_before_arm(
        monkeypatch, points):
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    with pytest.raises(ValueError, match="RAW waveform points"):
        backend.configure(points, 0, 25, 50e6, 4)
    assert instrument.arm_count == 0 and instrument.binary_reads == 0
    assert instrument.closed and manager.closed


@pytest.mark.parametrize(
    "points", [100, 250, 500, 1_000, 2_000, 5_000, 4_000_000, 8_000_000])
def test_keysight_documented_raw_record_lengths_are_accepted(
        monkeypatch, points):
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    backend.configure(points, 0, 25, 50e6, 4)
    assert backend.storage_metadata["observed_sample_count"] == points
    assert not any(
        event[0] == "write" and event[1].startswith(":ACQuire:SRATe ")
        for event in instrument.events)
    backend.close()
    assert instrument.closed and manager.closed


def test_strict_keysight_simulator_rejects_query_only_sample_rate_setter():
    instrument = StrictScopeInstrument("keysight")
    with pytest.raises(AssertionError, match="unexpected Keysight write"):
        instrument.write(":ACQuire:SRATe 200000000")


def test_keysight_stale_arm_event_cannot_authorize_new_capture(monkeypatch):
    instrument = StrictScopeInstrument(
        "keysight", never_arm=True, stale_arm_event=True)
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    backend.configure(100, 0, 25, 50e6, 4)
    backend._arm_timeout_s = 0
    with pytest.raises(TimeoutError, match="trigger-ready"):
        backend.arm()
    assert instrument.arm_count == 1 and instrument.binary_reads == 0
    backend.close()
    assert instrument.closed and manager.closed


def test_keysight_pre_target_trigger_is_rejected_before_target_runs(monkeypatch):
    instrument = StrictScopeInstrument("keysight", spurious_trigger_on_arm=True)
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    backend.configure(100, 0, 25, 50e6, 4)
    with pytest.raises(TimeoutError, match="before target execution"):
        backend.arm()
    assert instrument.capture_count == 0 and instrument.binary_reads == 0
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_per_capture_trigger_phase_is_observed_and_wrong_window_is_rejected(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, trigger_phases=(0.25, -0.4, 80.0))
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    observed = []
    for _ in range(2):
        backend.arm()
        instrument.trigger(bytes(range(16)), bytes(range(16)))
        backend.capture(timeout=0.1)
        observed.append(backend.last_trigger_index_samples)
    assert observed == pytest.approx([10.25, 9.6])

    backend.arm()
    instrument.trigger(bytes(range(16)), bytes(range(16)))
    with pytest.raises(RuntimeError, match="trigger position moved"):
        backend.capture(timeout=0.1)
    assert instrument.binary_reads == 2
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_scope_returns_raw_unaligned_codes_while_recording_phase(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, trigger_phases=(0.5,))
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    instrument.trigger(bytes(range(16)), bytes(range(16)))
    raw = instrument._wave.copy()
    observed = backend.capture(timeout=0.1)
    expected = ((raw.astype(np.float32) - 128.0) / 256.0
                if dialect == "keysight" else raw.astype(np.float32) / 256.0)
    np.testing.assert_array_equal(observed, expected)
    assert backend.last_trigger_index_samples == pytest.approx(10.5)
    assert backend.storage_metadata["scope_fractional_phase_alignment_applied"] is False
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_binary_transfer_uses_explicit_timeout_and_restores_control_timeout(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect)
    backend, manager = make_backend(
        monkeypatch, instrument, dialect=dialect, transfer_timeout_ms=17_000)
    backend.configure(100, 0, 25, 50e6, 4)
    backend.arm()
    instrument.trigger(bytes(16), bytes(16))
    backend.capture(timeout=0.1)
    assert instrument.binary_timeouts_ms == [17_000]
    assert instrument.timeout == 8_000
    assert backend.storage_metadata["scope_transfer_timeout_ms"] == 17_000
    backend.close()
    assert instrument.closed and manager.closed


def test_large_record_gets_scaled_transfer_timeout_without_allocating_waveform(
        monkeypatch):
    instrument = StrictScopeInstrument("keysight")
    backend, manager = make_backend(monkeypatch, instrument, dialect="keysight")
    backend.configure(4_000_000, 0, 25, 50e6, 4)
    assert backend.storage_metadata["scope_transfer_timeout_ms"] == 18_000
    assert instrument.timeout == 8_000
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_checkpoint_verifier_is_query_only_and_detects_vertical_drift(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    event_count = len(instrument.events)
    state = backend.verify_capture_state()
    assert state["scope_measurement_channel_vertical_scale_v_per_div"] == \
        pytest.approx(0.1)
    assert all(event[0] == "query" for event in instrument.events[event_count:])

    instrument.channel_scale_v[1] = 0.2
    with pytest.raises(RuntimeError, match="immutable capture settings changed") as exc:
        backend.verify_capture_state()
    assert "scope_measurement_channel_vertical_scale_v_per_div" in str(exc.value)
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_checkpoint_verifier_reports_post_setup_error_queue(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    instrument.setup_error = '-222,"Data out of range"'
    with pytest.raises(RuntimeError, match="capture/checkpoint verification"):
        backend.verify_capture_state()
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_scope_is_stopped_before_setup_and_on_close(monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect, initial_running=True)
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    assert not instrument.armed
    backend.arm()
    assert instrument.armed
    backend.close()
    assert not instrument.armed
    assert instrument.closed and manager.closed


def test_fractional_phase_alignment_moves_features_without_wraparound():
    trace = np.asarray([0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    later_trigger = _fractional_align_to_reference(trace, 0.5)
    earlier_trigger = _fractional_align_to_reference(trace, -0.5)
    np.testing.assert_allclose(later_trigger, [0.0, 0.5, 0.5, 0.0, 0.0])
    np.testing.assert_allclose(earlier_trigger, [0.0, 0.0, 0.5, 0.5, 0.0])


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_persistent_multi_acquisition_modes_are_forced_to_single_record(
        monkeypatch, dialect):
    instrument = StrictScopeInstrument(dialect)
    assert instrument.keysight_realtime_mode == "SEGMENTED"
    assert instrument.tek_sequence_count == 7
    assert instrument.tek_fastframe and instrument.tek_fastacq
    backend, manager = make_backend(monkeypatch, instrument, dialect=dialect)
    backend.configure(100, 0, 25, 50e6, 4)
    if dialect == "keysight":
        assert instrument.keysight_realtime_mode == "RTIME"
    else:
        assert instrument.tek_sequence_count == 1
        assert not instrument.tek_fastframe and not instrument.tek_fastacq
    backend.close()
    assert instrument.closed and manager.closed


@pytest.mark.parametrize("dialect", ["keysight", "tek"])
def test_same_measurement_and_trigger_channel_is_refused_by_config_and_backend(
        monkeypatch, dialect):
    with pytest.raises(ValueError, match="must be different"):
        AcqConfig(
            "aes1", 2, backend="scope", samples=100,
            scope_channel=1, scope_trig_source="CHANnel1").validate()
    instrument = StrictScopeInstrument(dialect)
    pyvisa, manager = simulated_pyvisa(instrument)
    monkeypatch.setitem(sys.modules, "pyvisa", pyvisa)
    backend = ScopeBackend(
        resource=instrument.resource, dialect=dialect, channel=1,
        trig_source="CH1", clock_source="external")
    with pytest.raises(ValueError, match="must be different"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert instrument.arm_count == 0 and instrument.binary_reads == 0
    assert instrument.closed and manager.closed


def test_line_trigger_is_refused_by_config():
    with pytest.raises(ValueError, match="scope trigger source must be"):
        AcqConfig(
            "aes1", 2, backend="scope", samples=100,
            scope_trig_source="LINE").validate()


def test_scope_resume_metadata_pins_vertical_and_horizontal_setup_but_not_phase():
    original = {
        "scope_horizontal_reference_observed": "LEFT",
        "scope_horizontal_position_s_observed": 0.0,
        "scope_measurement_channel_coupling": "DC",
        "scope_measurement_channel_input_impedance_ohm": 1e6,
        "scope_alignment_reference_trigger_index_samples": 10.0,
        "scope_preamble_xorigin_s": -50e-9,
        "scope_trigger_index_from_preamble": 10.0,
    }
    phase_shifted = dict(
        original, scope_preamble_xorigin_s=-49e-9,
        scope_trigger_index_from_preamble=9.8)
    assert _instrument_metadata_compatible(original, phase_shifted, "scope")
    changed_vertical = dict(
        original, scope_measurement_channel_input_impedance_ohm=50.0)
    assert not _instrument_metadata_compatible(original, changed_vertical, "scope")
    changed_position = dict(original, scope_horizontal_position_s_observed=1e-9)
    assert not _instrument_metadata_compatible(original, changed_position, "scope")
    changed_reference = dict(
        original, scope_alignment_reference_trigger_index_samples=11.0)
    assert not _instrument_metadata_compatible(original, changed_reference, "scope")
