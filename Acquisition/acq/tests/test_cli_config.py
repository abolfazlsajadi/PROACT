"""Offline CLI/configuration contracts; no capture runtime or hardware imports."""
import builtins
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import click
from click.testing import CliRunner

ACQUISITION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ACQUISITION))
from acq.config import AcqConfig, parse_count
from acq import console
from acq.progress import PlainUI, RichUI, make_ui

spec = importlib.util.spec_from_file_location("acquisition_offline_cli", ACQUISITION / "acquire.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture(autouse=True)
def forbid_hardware_and_capture_imports(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("acq.run", "acq.backend", "acq.targets", "proact_host",
                            "chipwhisperer", "serial", "pyvisa", "hid")):
            raise AssertionError(f"offline CLI imported {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("text, expected", [("100", 100), ("1.5k", 1500), ("2M", 2000000),
                                             ("1,024", 1024), (7, 7)])
def test_count_accepts_only_integral_totals(text, expected):
    assert parse_count(text) == expected


@pytest.mark.parametrize("value", [0, -1, True, "nan", "inf", "0.01", "1.0001k", "1e6", "bad"])
def test_count_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_count(value)


@pytest.mark.parametrize("field, value", [
    ("clock_mhz", float("nan")), ("clock_mhz", float("inf")), ("clock_mhz", 0),
    ("gain_db", float("nan")), ("samples", -1), ("offset", -1), ("chunk", 0),
    ("adc_mul", 0), ("firmware_uart_divisor", 0), ("seed", -1),
    ("scope_trig_level", float("inf")), ("suffix", "../other"), ("port", "tty\nACM"),
    ("scope_trig_source", "CH1;*RST"), ("trigger_mode", "firmware"),
    ("aead_trigger", 128), ("aead_trigger", 256), ("scope_channel", 0),
])
def test_config_rejects_invalid_requests(field, value):
    cfg = AcqConfig("aes1", 10)
    setattr(cfg, field, value)
    with pytest.raises(ValueError):
        cfg.validate()


def test_uart_math_is_declared_and_host_auto_is_rounded():
    cfg = AcqConfig("aes1", 10).validate()
    assert cfg.uart_actual_baud == pytest.approx(50e6 / (16 * 27))
    assert cfg.uart_host_baud == 115741
    assert cfg.uart_baud_error_percent < 0.001
    assert cfg.baud is None
    assert cfg.clock_source == "husky"


def test_explicit_legacy_baud_is_preserved_within_tolerance():
    cfg = AcqConfig("aes1", 10, baud=115200).validate()
    assert cfg.uart_host_baud == 115200
    assert cfg.uart_baud_error_percent == pytest.approx(0.4672)
    with pytest.raises(ValueError, match="maximum 2%"):
        AcqConfig("aes1", 10, clock_mhz=25, baud=115200).validate()


def test_live_prerequisites_are_distinct_from_offline_review():
    cfg = AcqConfig("aes1", 10, backend="scope").validate()
    assert cfg.clock_source == "external"
    with pytest.raises(ValueError, match="UART resource"):
        cfg.validate(require_devices=True)
    cfg.port = "/dev/serial/by-id/example"
    with pytest.raises(ValueError, match="VISA resource"):
        cfg.validate(require_devices=True)
    cfg.scope_resource = "TCPIP0::scope.example::inst0::INSTR"
    assert cfg.validate(require_devices=True) is cfg
    cfg.clock_source = "husky"
    with pytest.raises(ValueError, match="external board clock"):
        cfg.validate()


def test_default_cli_reviews_without_any_device_or_capture_import():
    result = CliRunner().invoke(cli.main, ["--target", "aes1", "--traces", "10", "--plain"])
    assert result.exit_code == 0, result.output
    assert "Offline configuration; live integration pending" in result.output
    assert "115741 baud" in result.output
    assert "not measured" in result.output
    assert "none — no instrument accessed" in result.output
    assert "\x1b[" not in result.output


def test_configuration_export_labels_requests_and_never_overwrites(tmp_path):
    path = tmp_path / "request.json"
    args = ["--target", "aes1", "--traces", "10", "--clock-mhz", "25",
            "--save-config", str(path)]
    first = CliRunner().invoke(cli.main, args)
    assert first.exit_code == 0, first.output
    before = path.read_bytes()
    data = json.loads(before)
    assert data["mode"] == "offline-configuration"
    assert data["live_integration"] == "pending"
    assert data["observed_instrument"] is None
    assert data["calculated"]["host_uart_baud"] == 57870
    second = CliRunner().invoke(cli.main, args)
    assert second.exit_code != 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("samples, byte_count", [("0", None), ("100", 2000)])
def test_estimate_is_payload_arithmetic_without_dataset_reads(samples, byte_count):
    result = CliRunner().invoke(cli.main, ["--target", "aes1", "--traces", "10",
                                         "--samples", samples, "--estimate", "--show-config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["waveform_payload_estimate"]["bytes"] == byte_count
    assert "excludes metadata" in data["waveform_payload_estimate"]["scope"]


def test_scope_review_records_model_and_raw_input_only():
    result = CliRunner().invoke(cli.main, ["--target", "aes1", "--traces", "10",
        "--backend", "scope", "--scope-model", "example model", "--scope-trigger-source", "CH2",
        "--scope-channel", "1", "--scope-resource", "TCPIP0::scope.example::inst0::INSTR",
        "--show-config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["requested"]["scope_model"] == "example model"
    assert data["requested"]["clock_source"] == "external"
    assert data["requested"]["scope_trig_source"] == "CH2"
    assert data["observed_instrument"] is None


@pytest.mark.parametrize("options", [[], ["--target", "aes1", "--traces", "0"],
    ["--target", "aes1", "--traces", "10", "--clock-mhz", "nan"],
    ["--target", "aes1", "--traces", "10", "--trigger", "firmware"],
    ["--wizard"]])
def test_bad_or_noninteractive_inputs_fail_as_usage_errors(options):
    result = CliRunner().invoke(cli.main, options)
    assert result.exit_code == 2, result.output
    assert "Error:" in result.output


def test_demo_is_labelled_synthetic_and_writes_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.main, ["--demo", "--plain", "--no-color"])
    assert result.exit_code == 0, result.output
    assert "SYNTHETIC UI DEMO" in result.output
    assert "no instrument, waveform, dataset or performance measurement" in result.output
    assert "checkpoint=12" in result.output
    assert "measured records: 0" in result.output
    assert list(tmp_path.iterdir()) == []
    assert "\x1b[" not in result.output


def test_redirected_progress_uses_plain_output(monkeypatch):
    monkeypatch.setattr(console, "is_tty", lambda: False)
    assert isinstance(make_ui(AcqConfig("aes1", 10), fancy=True), PlainUI)


def test_wizard_asks_frequency_first_and_cancels_before_device_access(monkeypatch):
    prompts = []

    class Cancel:
        def ask(self):
            return None

    def text(message, **kwargs):
        prompts.append(message)
        assert kwargs["default"] == "50"
        return Cancel()

    monkeypatch.setitem(sys.modules, "questionary", SimpleNamespace(text=text))
    with pytest.raises(click.Abort):
        cli.wizard()
    assert prompts == ["Requested target clock (MHz):"]


def test_scope_wizard_preserves_complete_requested_configuration(monkeypatch):
    prompts = []
    answers = iter(["40", "27", "auto", "/dev/serial/by-id/example", "scope", "external",
                    "aes1", "fixed", "random", "10", "4", "120", "5", "core",
                    "TCPIP0::scope.example::inst0::INSTR", "example model", "tek",
                    "2", "CH2", "1.2"])

    class Reply:
        def ask(self):
            return next(answers)

    def prompt(message, **kwargs):
        prompts.append(message)
        return Reply()

    monkeypatch.setitem(sys.modules, "questionary", SimpleNamespace(text=prompt, select=prompt))
    cfg = cli.wizard().validate()
    assert prompts[0] == "Requested target clock (MHz):"
    assert (cfg.clock_mhz, cfg.adc_mul, cfg.samples, cfg.offset) == (40, 4, 120, 5)
    assert (cfg.scope_channel, cfg.scope_trig_source, cfg.scope_trig_level) == (2, "CH2", 1.2)
    assert cfg.scope_model == "example model"
    assert cfg.clock_source == "external"
    assert cfg.uart_host_baud == 92593


def test_wizard_refuses_silently_ignored_command_line_values(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    context = cli.main.make_context("acquire", ["--wizard", "--samples", "120"])
    with context, pytest.raises(click.UsageError, match="conflicting options: samples"):
        cli.main.invoke(context)


def test_rich_progress_keeps_status_readable_at_80_columns(monkeypatch):
    rich_console = pytest.importorskip("rich.console")
    output = io.StringIO()
    display = rich_console.Console(file=output, width=80, force_terminal=True,
                                   color_system=None, record=True)
    monkeypatch.setattr(console, "CONSOLE", display)
    ui = RichUI(SimpleNamespace(target="SYNTHETIC UI DEMO"))
    ui.start(12, 0)
    try:
        ui.update(12, {"done": 12, "fail": 1, "retry": 1, "checkpoint": 12})
    finally:
        ui.close()
    text = display.export_text()
    assert "12/12" in text
    assert "elapsed=" in text and "ETA=" in text and "rate=" in text
    assert "fail=1  retry=1  checkpoint=12" in text
    assert "…" not in text
