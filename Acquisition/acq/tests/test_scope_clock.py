"""Hardware-free checks for the oscilloscope board-clock selection."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import acquire
from acq.backend.clock import HuskyClockOutput
from acq.backend.scope import ScopeBackend
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.native import capture_signature, normalized_signature
from acq.run import _backend, _prepare_capture
from acq.store import TraceStore


def test_husky_clock_output_is_held_until_close():
    events = []

    class FakeCapture:
        def __init__(self, **kwargs):
            events.append(("create", kwargs))

        def connect(self, **kwargs):
            events.append(("connect", kwargs))

        def clock_status(self):
            return {"locked": True, "target_clock_MHz": 50.0,
                    "clkgen_freq_MHz": 50.0, "adc_freq_MHz": 200.0}

        def disconnect(self):
            events.append(("disconnect",))

    output = HuskyClockOutput(capture_factory=FakeCapture)
    status = output.configure(50e6, 4)
    assert status["locked"] is True
    assert [event[0] for event in events] == ["create", "connect"]
    assert events[0][1]["platform"] == "asic"
    output.close()
    assert [event[0] for event in events] == ["create", "connect", "disconnect"]


def test_scope_backend_owns_and_closes_husky_clock_driver():
    events = []

    class Driver:
        def configure(self, clock_hz, adc_mul):
            events.append(("configure-clock", clock_hz, adc_mul))
            return {"locked": True, "clkgen_freq_MHz": clock_hz / 1e6}

        def close(self):
            events.append(("close-clock",))

    backend = ScopeBackend.__new__(ScopeBackend)
    backend.clock_source = "husky"
    backend._clock_driver_factory = Driver
    backend._clock_driver = None
    backend._clock_status = {}
    backend.inst = None
    backend.rm = None
    backend._configure_board_clock(25e6, 4)
    assert backend.clock_status["clkgen_freq_MHz"] == 25.0
    assert events == [("configure-clock", 25e6, 4)]
    backend.close()
    assert events[-1] == ("close-clock",)


def test_scope_setup_failure_releases_husky_clock():
    events = []

    class Driver:
        def configure(self, _clock_hz, _adc_mul):
            events.append("clock-open")
            return {"locked": True, "clkgen_freq_MHz": 50.0}

        def close(self):
            events.append("clock-close")

    backend = ScopeBackend.__new__(ScopeBackend)
    backend.clock_source = "husky"
    backend._clock_driver_factory = Driver
    backend._clock_driver = None
    backend._clock_status = {}
    backend.inst = None
    backend.rm = None
    backend._configure_scope = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("scope setup failed"))
    with pytest.raises(RuntimeError, match="scope setup failed"):
        backend.configure(100, 0, 25, 50e6, 4)
    assert events == ["clock-open", "clock-close"]


def test_scope_clock_source_defaults_to_husky_and_is_validated():
    cfg = AcqConfig("aes1", 2, backend="scope").validate()
    assert cfg.scope_clock_source == "husky"
    assert cfg.scope_trig_level == 1.5
    assert cfg.scope_transfer_timeout_ms is None
    assert cfg.to_meta()["scope_clock_source"] == "husky"
    assert cfg.to_meta()["scope_trig_level"] == 1.5
    assert AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external"
    ).validate().scope_clock_source == "external"
    with pytest.raises(ValueError, match="scope_clock_source"):
        AcqConfig("aes1", 2, backend="scope", scope_clock_source="scope").validate()
    with pytest.raises(ValueError, match="finite voltage"):
        AcqConfig("aes1", 2, backend="scope", scope_trig_level=float("nan")).validate()
    assert AcqConfig(
        "aes1", 2, backend="scope", scope_transfer_timeout_ms=17_000
    ).validate().scope_transfer_timeout_ms == 17_000
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="transfer timeout"):
            AcqConfig(
                "aes1", 2, backend="scope",
                scope_transfer_timeout_ms=invalid).validate()


def test_scope_clock_source_is_passed_to_scope_backend(monkeypatch):
    seen = {}

    class FakeScope:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("acq.backend.scope.ScopeBackend", FakeScope)
    cfg = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external",
        scope_resource="TCPIP::scope", scope_dialect="tek", scope_channel=2,
        scope_trig_source="AUX", scope_transfer_timeout_ms=17_000,
    ).validate()
    assert isinstance(_backend(cfg), FakeScope)
    assert seen == {
        "resource": "TCPIP::scope", "dialect": "tek", "channel": 2,
        "trig_source": "AUX", "trig_level": 1.5, "clock_source": "external",
        "transfer_timeout_ms": 17_000,
    }


@pytest.mark.parametrize(
    "source,status,expected_note",
    [
        ("husky", {"clkgen_freq_MHz": 50.0},
         "Husky board clock applied: 50 MHz; Husky remains open"),
        ("external", {}, "external board clock declared: 50 MHz"),
    ],
)
def test_prepare_capture_reports_scope_clock_truthfully(
        monkeypatch, source, status, expected_note):
    events = []

    class Backend:
        clock_status = status

        def configure(self, **kwargs):
            events.append(("configure", kwargs["clock_hz"]))

        def close(self):
            events.append(("backend-close",))

    class Target:
        def open(self):
            events.append(("uart-open",))

        def bringup_and_verify(self, _key):
            events.append(("kat",))

        def close(self):
            events.append(("target-close",))

    class UI:
        def note(self, message):
            events.append(("note", message))

    cfg = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source=source
    ).validate()
    gen = InputGen(cfg)
    monkeypatch.setattr(
        "acq.run.preflight",
        lambda *_args: {"samples": 100, "trig_count": 0, "clip": 0},
    )
    _prepare_capture(cfg, Target(), Backend(), gen, gen.key(0), UI())
    notes = [event[1] for event in events if event[0] == "note"]
    assert any(expected_note in note for note in notes)
    kinds = [event[0] for event in events]
    assert kinds.index("configure") < kinds.index("uart-open") < kinds.index("kat")


def test_scope_clock_source_is_immutable_and_legacy_scope_means_external():
    husky_clock = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="husky", seed=1
    ).validate()
    external_clock = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external", seed=1
    ).validate()
    hs = capture_signature(husky_clock, 100, 16)
    ext = capture_signature(external_clock, 100, 16)
    assert hs["scope_clock_source"] == "husky"
    assert ext["scope_clock_source"] == "external"
    assert hs != ext

    legacy = dict(ext)
    legacy.pop("scope_clock_source")
    assert normalized_signature(json.dumps(legacy)) == ext

    old_husky_capture = capture_signature(
        AcqConfig("aes1", 2, backend="husky", seed=1).validate(), 100, 16
    )
    old_husky_capture.pop("scope_clock_source")
    assert normalized_signature(old_husky_capture)["scope_clock_source"] == "husky"

    legacy_level = dict(ext)
    legacy_level.pop("scope_trig_level")
    assert normalized_signature(legacy_level) == ext


def test_resume_refuses_mixed_scope_clock_sources(monkeypatch, tmp_path):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))
    external = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external",
        suffix="_scope_clock_identity", seed=1,
    ).validate()
    store = TraceStore(external, samples=100, out_len=16, key_varies=False)
    store.close()
    husky = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="husky",
        suffix="_scope_clock_identity", seed=1,
    ).validate()
    with pytest.raises(SystemExit, match="scope_clock_source"):
        TraceStore(husky, samples=100, out_len=16, key_varies=False)


def test_resume_refuses_changed_scope_trigger_level(monkeypatch, tmp_path):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))
    original = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external",
        scope_trig_level=0.8, suffix="_scope_level_identity", seed=1,
    ).validate()
    TraceStore(original, samples=100, out_len=16, key_varies=False).close()
    changed = AcqConfig(
        "aes1", 2, backend="scope", scope_clock_source="external",
        scope_trig_level=1.2, suffix="_scope_level_identity", seed=1,
    ).validate()
    with pytest.raises(SystemExit, match="scope_trig_level"):
        TraceStore(changed, samples=100, out_len=16, key_varies=False)


def test_scope_estimate_cli_exposes_clock_source_without_opening_devices():
    result = CliRunner().invoke(
        acquire.main,
        ["--target", "aes1", "--traces", "2", "--backend", "scope",
         "--scope-clock-source", "external", "--scope-trigger-source", "CHANnel2",
         "--scope-trigger-level", "0.8", "--samples", "100", "--estimate"],
    )
    assert result.exit_code == 0, result.output
    assert "board clock   : external (declared; connect and verify it yourself)" in result.output
    assert "scope trigger : CHANnel2 at 0.8 V (applied and read back)" in result.output


def test_wizard_asks_scope_clock_source(monkeypatch):
    prompts = []

    answers = {
        "Target clock frequency (MHz):": "50",
        "Host UART speed:": "auto",
        "PROACT UART port:": "",
        "Core to attack:": "aes1",
        "Post-capture analysis:": [],
        "Key policy:": "fixed",
        "Plaintext policy:": "random",
        "How many traces?": "10",
        "Measurement backend:": "scope (oscilloscope)",
        "Warm up the core before recording each session?": False,
        "Trigger source:": "auto",
        "Board clock during oscilloscope capture:": "external",
        "VISA resource (blank = auto-detect):": "",
        "Oscilloscope SCPI family:": "auto",
        "Oscilloscope measurement channel (1-8):": "1",
        "Oscilloscope trigger input:": "EXTernal",
        "Oscilloscope trigger level (V):": "0.8",
        "Additional save formats (native resumable files are always kept):": [],
    }

    class Prompt:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    def ask(kind):
        def build(message, **kwargs):
            prompts.append((kind, message, kwargs.get("default")))
            return Prompt(answers[message])
        return build

    fake = SimpleNamespace(
        text=ask("text"), select=ask("select"), checkbox=ask("checkbox"),
        confirm=ask("confirm"),
    )
    monkeypatch.setitem(sys.modules, "questionary", fake)
    monkeypatch.setattr("acq.serial_ports.discover_serial_ports", lambda: [])
    cfg, formats = acquire.wizard()
    assert cfg.backend == "scope"
    assert cfg.scope_clock_source == "external"
    assert cfg.scope_trig_level == 0.8
    assert formats == ("native",)
    clock_prompt = next(p for p in prompts if p[1].startswith("Board clock during"))
    assert clock_prompt[2] == "husky"
    level_prompt = next(p for p in prompts if p[1].startswith("Oscilloscope trigger level"))
    assert level_prompt[2] == "1.5"
