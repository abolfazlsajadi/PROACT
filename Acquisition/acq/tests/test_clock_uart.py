"""Hardware-free tests for live clock, UART, port and trigger integration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

import acquire
from acq.backend.husky import validate_clock_status
from acq.config import AcqConfig, UART_DIVISOR, auto_uart_baud
from acq.inputs import InputGen
from acq.native import capture_signature, normalized_signature
from acq.run import _prepare_capture
from acq.serial_ports import SerialPort, discover_serial_ports, recommended_port
from acq.targets.aes1 import AES1Target
from acq.targets.aes2 import AES2Target
from acq.targets.sw_rv import SwRVTarget


def test_proven_clock_uart_baselines():
    cfg = AcqConfig("aes1", 1).validate()
    assert UART_DIVISOR == 27
    assert auto_uart_baud(50) == cfg.uart_host_baud == 115200
    assert cfg.uart_actual_baud == pytest.approx(115740.74074074074)
    assert cfg.uart_baud_error_percent == pytest.approx(0.4672)
    assert auto_uart_baud(25) == 57600


def test_explicit_uart_override_and_validation():
    cfg = AcqConfig("aes1", 1, baud=115741).validate()
    assert cfg.uart_host_baud == 115741
    assert cfg.uart_baud_error_percent < 0.001
    for kwargs in ({"clock_mhz": 0}, {"clock_mhz": float("nan")},
                   {"clock_mhz": float("inf")}, {"baud": 0}, {"baud": 9600}):
        with pytest.raises(ValueError):
            AcqConfig("aes1", 1, **kwargs).validate()


def test_target_open_uses_resolved_port_and_baud(monkeypatch):
    seen = {}

    class FakeUART:
        def __init__(self, port, baud):
            seen.update(port=port, baud=baud)

        def open(self):
            return self

        def close(self):
            pass

    class FakeProact:
        def __init__(self, uart):
            seen["uart"] = uart

        def enable_sendback(self):
            seen["sendback"] = True

    import acq.targets.base as target_base
    monkeypatch.setattr(target_base, "UartTransport", FakeUART)
    monkeypatch.setattr(target_base, "ProactTarget", FakeProact)
    cfg = AcqConfig("aes1", 1, clock_mhz=25, port="/dev/serial/by-id/proact").validate()
    target = target_base.Target(cfg)
    target.open()
    assert seen["port"] == "/dev/serial/by-id/proact"
    assert seen["baud"] == 57600
    assert seen["sendback"] is True


@pytest.mark.parametrize(
    "target_cls,target_name,core_source",
    [(AES1Target, "aes1", "aes1"), (AES2Target, "aes2", "aes2"),
     (SwRVTarget, "sw_rv", "swrv")],
)
def test_trigger_modes_have_distinct_cfgsel_meanings(target_cls, target_name, core_source):
    for mode, expected in (("auto", None), ("core", core_source),
                           ("firmware", "software")):
        target = target_cls(AcqConfig(target_name, 1, trigger_mode=mode).validate())
        calls = []
        target.t = SimpleNamespace(set_cfgsel=calls.append)
        target.configure_trigger()
        assert calls == [expected]


def test_clock_status_validation_is_defensive():
    validate_clock_status({}, 50e6, 4)
    validate_clock_status({"locked": True, "clkgen_freq_MHz": 50,
                           "adc_freq_MHz": 200}, 50e6, 4)
    with pytest.raises(RuntimeError, match="did not lock"):
        validate_clock_status({"locked": False}, 50e6, 4)
    with pytest.raises(RuntimeError, match="adc_freq_MHz"):
        validate_clock_status({"locked": True, "adc_freq_MHz": 100}, 50e6, 4)


def test_prepare_applies_clock_before_uart_and_preflight(monkeypatch):
    events = []

    class Backend:
        clock_status = {"locked": True, "clkgen_freq_MHz": 25,
                        "adc_freq_MHz": 100}

        def configure(self, **kwargs):
            events.append(("clock", kwargs["clock_hz"], kwargs["adc_mul"]))

        def close(self):
            events.append(("backend-close",))

    class Target:
        def open(self):
            events.append(("uart-open",))

        def bringup_and_verify(self, key):
            events.append(("kat",))

        def close(self):
            events.append(("target-close",))

    class UI:
        def note(self, message):
            events.append(("note", message))

    cfg = AcqConfig("aes1", 2, clock_mhz=25).validate()
    gen = InputGen(cfg)
    monkeypatch.setattr("acq.run.preflight",
                        lambda *_args: events.append(("preflight",)) or
                        {"samples": 100, "trig_count": 40, "clip": 0})
    result = _prepare_capture(cfg, Target(), Backend(), gen, gen.key(0), UI())
    kinds = [event[0] for event in events]
    assert kinds.index("clock") < kinds.index("uart-open") < kinds.index("kat")
    assert kinds.index("kat") < kinds.index("preflight")
    assert result["samples"] == 100


def test_prepare_closes_instruments_on_uart_failure():
    events = []

    class Backend:
        clock_status = {}
        def configure(self, **_kwargs): events.append("clock")
        def close(self): events.append("backend-close")

    class Target:
        def open(self): raise RuntimeError("UART failed")
        def close(self): events.append("target-close")

    cfg = AcqConfig("aes1", 1).validate()
    with pytest.raises(RuntimeError, match="UART failed"):
        _prepare_capture(cfg, Target(), Backend(), InputGen(cfg), bytes(16),
                         SimpleNamespace(note=lambda _message: None))
    assert events == ["clock", "backend-close", "target-close"]


def test_signature_records_uart_and_reads_legacy_v2():
    cfg = AcqConfig("aes1", 10).validate()
    current = capture_signature(cfg, 100, 16)
    assert current["uart_fixed_divisor"] == 27
    assert current["uart_host_baud"] == 115200
    legacy = dict(current)
    legacy.pop("uart_fixed_divisor")
    legacy.pop("uart_host_baud")
    assert normalized_signature(json.dumps(legacy)) == current


def test_husky_signature_ignores_scope_only_ui_settings():
    historical = AcqConfig("aes1", 10, scope_dialect="keysight").validate()
    current = AcqConfig(
        "aes1", 10, scope_dialect="auto", scope_channel=2,
        scope_trig_source="CHANnel2",
        scope_resource="TCPIP0::192.0.2.1::hislip0::INSTR").validate()
    assert capture_signature(historical, 100, 16) == \
        capture_signature(current, 100, 16)

    scope_a = AcqConfig("aes1", 10, backend="scope",
                        scope_dialect="keysight").validate()
    scope_b = AcqConfig("aes1", 10, backend="scope",
                        scope_dialect="tek").validate()
    assert capture_signature(scope_a, 100, 16) != \
        capture_signature(scope_b, 100, 16)


def test_metadata_only_port_discovery_prefers_stable_mcp2200(tmp_path):
    sys_root = tmp_path / "sys"
    sys_tty = sys_root / "class" / "tty"
    real = sys_root / "devices" / "usb1" / "1-1" / "ttyACM0"
    entry = sys_tty / "ttyACM0"
    dev = tmp_path / "dev"
    real.mkdir(parents=True)
    entry.mkdir(parents=True)
    dev.mkdir()
    (entry / "device").symlink_to(real, target_is_directory=True)
    (dev / "ttyACM0").touch()
    parent = real.parent
    (parent / "idVendor").write_text("04d8")
    (parent / "product").write_text("MCP2200 USB Serial Port")
    (parent / "serial").write_text("ABC123")
    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    alias = by_id / "usb-Microchip_MCP2200_ABC123-if00"
    alias.symlink_to(Path("../..") / "ttyACM0")
    ports = discover_serial_ports(sys_tty=sys_tty, dev_root=dev)
    assert len(ports) == 1
    assert ports[0].path == str(alias)
    assert ports[0].serial_number == "ABC123"
    assert recommended_port(ports) == ports[0]


def test_wizard_asks_frequency_first(monkeypatch):
    prompts = []

    class Reply:
        def ask(self):
            return None

    def text(message, **kwargs):
        prompts.append((message, kwargs.get("default")))
        return Reply()

    monkeypatch.setitem(sys.modules, "questionary", SimpleNamespace(text=text))
    assert acquire.wizard() is None
    assert prompts == [("Target clock frequency (MHz):", "50")]
