"""Offline checks for responsive progress, colour policy and stable log output."""
from __future__ import annotations

import io

import pytest
from click.testing import CliRunner

import acq.console as console_module
import acq.progress as progress_module
import acquire
from acq.config import AcqConfig


pytest.importorskip("rich")
from rich.console import Console
from rich.text import Text


class _TTY:
    def isatty(self):
        return True


def _cfg():
    return AcqConfig("aes1", 10)


def _render_progress(width, *, no_color=True):
    stream = io.StringIO()
    console = Console(
        file=stream, width=width, force_terminal=False, no_color=no_color,
        highlight=False,
    )
    ui = progress_module.RichUI(_cfg(), console=console)
    ui.start(10, 0)
    ui.update(10, {
        "done": 10, "fail": 3, "rec": 2, "quality": 4, "trig": 596,
    })
    ui.close()
    return stream.getvalue()


def test_auto_ui_uses_plain_for_dumb_terminal(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert not console_module.interactive_rich(_TTY())
    monkeypatch.setattr(progress_module, "interactive_rich", lambda: False)
    assert isinstance(progress_module.make_ui(_cfg()), progress_module.PlainUI)


def test_plain_redirect_has_stable_final_status_and_health(monkeypatch, capsys):
    monkeypatch.setattr(progress_module, "is_tty", lambda: False)
    ui = progress_module.PlainUI(_cfg())
    ui.start(10, 0)
    ui.update(10, {
        "done": 10, "fail": 3, "rec": 2, "quality": 4, "trig": 596,
    })
    ui.close()
    output = capsys.readouterr().out
    assert "CAPTURE start target=aes1 done=0 total=10" in output
    assert "CAPTURE 10/10 (100.0%)" in output
    assert "errors=3 recoveries=2 quality=4" in output
    assert "\x1b" not in output


def test_rich_progress_is_width_bounded_and_responsive():
    narrow = _render_progress(46)
    wide = _render_progress(140)
    assert max(map(len, narrow.splitlines())) <= 46
    assert max(map(len, wide.splitlines())) <= 140
    assert "trig 596" not in narrow
    assert "err 3" not in narrow
    assert "err 3" in wide
    assert "rec 2" in wide
    assert "quality 4" in wide
    assert "trig 596" in wide


def test_no_color_render_has_no_ansi_and_notes_are_literal():
    stream = io.StringIO()
    console = Console(file=stream, width=80, force_terminal=False,
                      no_color=True, highlight=False)
    ui = progress_module.RichUI(_cfg(), console=console)
    ui.log("=== ACQUIRE AES1 ===")
    ui.note("[red]literal instrument text[/red] preflight OK")
    ui.summary({"target": "aes1", "valid traces": "10 / 10"})
    output = stream.getvalue()
    assert "\x1b" not in output
    assert "[red]literal instrument text[/red]" in output
    assert "CAPTURE COMPLETE" in output


def test_no_color_environment_disables_colour_codes(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    console = console_module.make_console(file=stream, width=40,
                                          force_terminal=True)
    console.print(Text("colour probe", style="bold bright_red"))
    assert stream.getvalue() == "colour probe\n"


def test_rich_progress_owns_one_live_surface():
    stream = io.StringIO()
    console = Console(file=stream, width=100, force_terminal=False,
                      no_color=True, highlight=False)
    ui = progress_module.RichUI(_cfg(), console=console)
    ui.start(10, 0)
    progress_identity = id(ui.prog)
    for done in range(1, 11):
        ui.update(1, {"done": done, "fail": 0, "rec": 0,
                      "quality": 0, "trig": 596})
        assert id(ui.prog) == progress_identity
    ui.close()
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert "100.0%" in lines[0]


def test_configuration_plan_wraps_to_terminal_and_explains_scope_gain(monkeypatch):
    stream = io.StringIO()
    console = Console(file=stream, width=52, force_terminal=False,
                      no_color=True, highlight=False)
    monkeypatch.setattr(acquire, "CONSOLE", console)
    monkeypatch.setattr(acquire, "interactive_rich", lambda: True)
    cfg = AcqConfig("aes1", 10, backend="scope", samples=100).validate()
    acquire._panel(cfg, resume_n=0, formats=("native",))
    lines = stream.getvalue().splitlines()
    normalized = " ".join(stream.getvalue().split())
    assert max(map(len, lines)) <= 52
    assert "scope-controlled" in normalized
    assert "validate calibration" in normalized
    assert "and clipping" in normalized


def test_estimate_explains_backend_specific_gain_without_devices():
    scope = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "10", "--samples", "100",
        "--backend", "scope", "--scope-clock-source", "external", "--estimate",
    ])
    assert scope.exit_code == 0, scope.output
    assert "gain/range    : scope-controlled" in scope.output
    assert "calibration/clipping validated" in scope.output

    husky = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "10", "--samples", "100", "--estimate",
    ])
    assert husky.exit_code == 0, husky.output
    assert "gain          : 25 dB on Husky" in husky.output


def test_plain_scope_estimate_wraps_at_narrow_terminal(monkeypatch):
    monkeypatch.setenv("COLUMNS", "52")
    result = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "10", "--samples", "100",
        "--backend", "scope", "--scope-transfer-timeout-ms", "17000",
        "--format", "h5", "--format", "csv", "--estimate",
    ])
    assert result.exit_code == 0, result.output
    assert max(map(len, result.output.splitlines())) <= 52
    assert "transfer wait : 17,000 ms" in result.output
