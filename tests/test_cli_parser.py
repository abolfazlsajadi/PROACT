"""
Regression tests for ``proact_host.cli`` -- the argument parser and the
offline-only subcommands.

OFFLINE ONLY.  Nothing here opens a serial port, enumerates USB HID, talks to
an MCP2210/MCP2200 or a ChipWhisperer, or spawns a subprocess:

  * Every "bad arguments" case is chosen so argparse raises ``SystemExit``
    *before* ``args.func(args)`` is reached.  The ``no_dispatch`` fixture makes
    that a hard assertion: it replaces every ``cmd_*`` handler with a raiser, so
    a test that accidentally reaches dispatch fails loudly instead of touching
    the bench.
  * Default values are read by replacing the handler with a spy that records the
    parsed Namespace and exits immediately (``parse_only``), so the parser is
    exercised end to end while the command body never runs.
  * Only four handlers are ever really dispatched, and none of them touches
    hardware: ``version`` (prints a string), ``info`` (prints constants from
    regs/config), ``test`` (builds its own in-memory fake transport) and
    ``decrypt-soft --selftest`` (pure-Python ASCON/Xoodyak vectors).

The autouse ``_offline_guard`` fixture additionally neuters ``cli._target`` (the
single door to the UART) and ``subprocess.call`` for the whole module.

The parser lives inside ``main()``, so it cannot be imported directly; where a
test needs the parser object itself it is captured by intercepting
``parse_args`` -- the call ``main()`` makes immediately before dispatch.
"""
import argparse
import contextlib
import importlib
import io
import os
import re
import sys
import subprocess

import pytest

import proact_host
from proact_host import cli, regs


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def run_cli(argv):
    """Run ``cli.main(argv)`` with stdout/stderr captured.

    Returns ``(exit_code, stdout, stderr)``; ``exit_code`` is ``None`` when
    ``main()`` returned normally instead of raising ``SystemExit``.
    """
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


def top_level_parser():
    """Return the ``ArgumentParser`` that ``main()`` builds, without dispatching.

    ``main()`` builds the parser locally and calls ``ap.parse_args(argv)``
    immediately before ``args.func(args)``, so intercepting ``parse_args`` hands
    us the fully-populated parser and guarantees nothing is ever dispatched.
    """
    class _Captured(Exception):
        def __init__(self, parser):
            self.parser = parser

    original = argparse.ArgumentParser.parse_args

    def _capture(self, args=None, namespace=None):
        raise _Captured(self)

    argparse.ArgumentParser.parse_args = _capture
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cli.main([])
    except _Captured as captured:
        return captured.parser
    finally:
        argparse.ArgumentParser.parse_args = original
    raise AssertionError("cli.main() did not call parse_args()")


def subcommand_action(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("cli.main() built no subparsers")


def handler_names():
    return [n for n in dir(cli) if n.startswith("cmd_")]


def parse_only(monkeypatch, handler, argv):
    """Parse ``argv`` and return the resulting Namespace.

    Every ``cmd_*`` handler is replaced: ``handler`` by a spy that records the
    Namespace and exits 0, the rest by raisers.  So the real command body never
    runs, yet the whole parse -> Namespace -> dispatch path is exercised.
    """
    _install_raisers(monkeypatch)
    box = {}

    def _spy(args):
        box["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(cli, handler, _spy)
    code, _out, err = run_cli(argv)
    assert code == 0, f"argv {argv!r} exited {code!r}: {err}"
    assert "args" in box, f"argv {argv!r} never reached {handler}"
    return box["args"]


def _install_raisers(monkeypatch):
    def _raiser(name):
        def _boom(_args):
            raise AssertionError(
                f"cli.{name} was dispatched; the parser should have rejected "
                f"these arguments first")
        return _boom

    for name in handler_names():
        monkeypatch.setattr(cli, name, _raiser(name))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    """Belt and braces: make the two doors to the outside world explode."""
    def _no_target(_args):
        raise AssertionError("cli._target() tried to open the UART")

    def _no_subprocess(*_a, **_kw):
        raise AssertionError("cli tried to spawn a subprocess")

    monkeypatch.setattr(cli, "_target", _no_target)
    monkeypatch.setattr(subprocess, "call", _no_subprocess)


@pytest.fixture
def no_dispatch(monkeypatch):
    """Fail the test if argparse lets any handler run at all."""
    _install_raisers(monkeypatch)


# The subcommands the bench workflow and the module docstring depend on, in
# registration order.  Renaming, removing or reordering one is a breaking
# change for every script and wiki snippet that calls it.
EXPECTED_SUBCOMMANDS = [
    "info", "doctor", "devices", "status", "timer", "version",
    "build-controller", "build-target", "test",
    "run", "aead-kat", "decrypt-soft", "seed", "cpa", "capture",
    "program", "reset", "restart", "peek", "poke",
    "selfcheck", "selftest", "load-swrv", "monitor", "gui",
]


# --------------------------------------------------------------------------
# top-level parser
# --------------------------------------------------------------------------
def test_no_arguments_is_an_error(no_dispatch):
    code, _out, err = run_cli([])
    assert code == 2
    assert "cmd" in err


def test_unknown_subcommand_is_an_error(no_dispatch):
    code, _out, err = run_cli(["bogus-cmd"])
    assert code == 2
    assert "invalid choice" in err
    assert "bogus-cmd" in err


def test_top_level_help_exits_zero_with_usage_banner(no_dispatch):
    code, out, _err = run_cli(["--help"])
    assert code == 0
    assert out.startswith("usage: proact ")
    assert "--port" in out
    assert "--no-color" in out


def test_help_lists_every_subcommand(no_dispatch):
    _code, out, _err = run_cli(["--help"])
    missing = [name for name in EXPECTED_SUBCOMMANDS if name not in out]
    assert missing == []


def test_subcommand_registry_is_exactly_the_documented_set():
    action = subcommand_action(top_level_parser())
    assert list(action.choices) == EXPECTED_SUBCOMMANDS


def test_every_subcommand_has_a_handler():
    action = subcommand_action(top_level_parser())
    for name, parser in action.choices.items():
        func = parser.get_default("func")
        assert callable(func), f"subcommand {name!r} has no func default"


@pytest.mark.parametrize("name", EXPECTED_SUBCOMMANDS)
def test_subcommand_help_exits_zero(no_dispatch, name):
    # --help short-circuits inside argparse, so this never reaches a handler.
    code, out, err = run_cli([name, "--help"])
    assert code == 0, err
    assert out.startswith(f"usage: proact {name} ")


def test_global_options_must_precede_the_subcommand(no_dispatch):
    # --port belongs to the top-level parser only; putting it after the
    # subcommand is a classic bench typo and must fail loudly, not silently.
    code, _out, err = run_cli(["version", "--port", "/dev/null"])
    assert code == 2
    assert "unrecognized arguments" in err


def test_port_is_parsed_but_never_opened(monkeypatch):
    args = parse_only(monkeypatch, "cmd_status", ["--port", "/dev/null", "status"])
    assert args.port == "/dev/null"


# --------------------------------------------------------------------------
# required arguments
# --------------------------------------------------------------------------
@pytest.mark.parametrize("argv, missing", [
    (["run"], "--core"),
    (["capture"], "--core"),
    (["peek"], "--addr"),
    (["poke", "--addr", "0x4"], "--data"),
    (["program"], "--vmem"),
    (["load-swrv", "--imem", "x"], "--dmem"),
    (["seed"], "--value"),
])
def test_missing_required_argument_exits_two(no_dispatch, argv, missing):
    code, _out, err = run_cli(argv)
    assert code == 2
    assert "required" in err
    assert missing in err


# --------------------------------------------------------------------------
# choices
# --------------------------------------------------------------------------
@pytest.mark.parametrize("argv, option", [
    (["run", "--core", "bogus"], "--core"),
    (["run", "--core", "aes1", "--trig", "bogus"], "--trig"),
    (["capture", "--core", "aes1", "--platform", "bogus"], "--platform"),
    (["capture", "--core", "aes1", "--gain-mode", "sideways"], "--gain-mode"),
    (["selfcheck", "--platform", "bogus"], "--platform"),
    (["decrypt-soft", "--cipher", "des"], "--cipher"),
    (["reset", "--mode", "nuke"], "--mode"),
    (["cpa", "--core", "ascon"], "--core"),
])
def test_invalid_choice_exits_two(no_dispatch, argv, option):
    code, _out, err = run_cli(argv)
    assert code == 2
    assert "invalid choice" in err
    assert option in err


@pytest.mark.parametrize("subcmd, option, expected", [
    ("run", "core", ["aes1", "aes2", "ascon", "xoodyak", "swrv"]),
    ("run", "trig", ["auto", "software", "aes1", "aes2", "ascon",
                     "xoodyak", "swrv"]),
    ("capture", "core", ["aes1", "aes2", "ascon", "xoodyak", "swrv"]),
    ("capture", "platform", ["asic", "fpga"]),
    ("capture", "gain_mode", ["low", "high"]),
    ("selfcheck", "platform", ["asic", "fpga"]),
    ("decrypt-soft", "cipher", ["ascon", "xoodyak"]),
    ("reset", "mode", ["run", "controller", "global", "spi", "reset_all"]),
    ("cpa", "core", ["aes1", "aes2", "swrv"]),
])
def test_choice_lists_are_stable(subcmd, option, expected):
    parser = subcommand_action(top_level_parser()).choices[subcmd]
    action = next(a for a in parser._actions if a.dest == option)
    assert list(action.choices) == expected


# --------------------------------------------------------------------------
# argument types
# --------------------------------------------------------------------------
def test_hex16_accepts_exactly_sixteen_bytes():
    assert cli._hex16("000102030405060708090a0b0c0d0e0f") == bytes(range(16))


def test_hex16_ignores_spaces():
    assert cli._hex16("00 01 02 03 04 05 06 07 "
                      "08 09 0a 0b 0c 0d 0e 0f") == bytes(range(16))


@pytest.mark.parametrize("bad", ["", "00", "00" * 15, "00" * 17])
def test_hex16_rejects_wrong_length(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        cli._hex16(bad)


def test_hex16_rejects_non_hex():
    # bytes.fromhex raises ValueError, which argparse also turns into exit 2.
    with pytest.raises((argparse.ArgumentTypeError, ValueError)):
        cli._hex16("zz" * 16)


@pytest.mark.parametrize("argv", [
    ["run", "--core", "aes1", "--key", "00"],
    ["run", "--core", "aes1", "--key", "zz" * 16],
    ["run", "--core", "aes1", "--pt", "0011"],
    ["capture", "--core", "aes1", "--key", "00" * 15],
    ["load-swrv", "--imem", "i", "--dmem", "d", "--key", "00" * 8],
])
def test_bad_hex16_option_exits_two(no_dispatch, argv):
    code, _out, err = run_cli(argv)
    assert code == 2
    assert "--key" in err or "--pt" in err


def test_hex16_option_accepts_a_full_key(monkeypatch):
    args = parse_only(monkeypatch, "cmd_run",
                      ["run", "--core", "aes1",
                       "--key", "00112233445566778899aabbccddeeff"])
    assert args.key == bytes.fromhex("00112233445566778899aabbccddeeff")


@pytest.mark.parametrize("text, expected", [
    ("", b""),
    ("abcd", b"\xab\xcd"),
    ("00 11 22", b"\x00\x11\x22"),
    ("00112233445566778899aabbccddeeff0011", bytes.fromhex(
        "00112233445566778899aabbccddeeff0011")),
])
def test_hexbytes_accepts_any_even_length(text, expected):
    assert cli._hexbytes(text) == expected


@pytest.mark.parametrize("bad", ["abc", "zz", "0x11"])
def test_hexbytes_rejects_non_hex_and_odd_length(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        cli._hexbytes(bad)


def test_hexbytes_option_accepts_odd_sized_ciphertext(monkeypatch):
    args = parse_only(monkeypatch, "cmd_decrypt_soft",
                      ["decrypt-soft", "--ct", "0011223344"])
    assert args.ct == b"\x00\x11\x22\x33\x44"


def test_bad_hexbytes_option_exits_two(no_dispatch):
    code, _out, err = run_cli(["decrypt-soft", "--ct", "abc"])
    assert code == 2
    assert "--ct" in err


@pytest.mark.parametrize("text, expected", [
    ("0x20000000", 0x20000000),
    ("0o17", 15),
    ("0b1010", 10),
    ("17", 17),
    ("-1", -1),
])
def test_int0_accepts_every_python_literal_base(text, expected):
    assert cli._int0(text) == expected


def test_int0_rejects_garbage():
    with pytest.raises(ValueError):
        cli._int0("zz")


def test_int0_options_parse_through_the_cli(monkeypatch):
    args = parse_only(monkeypatch, "cmd_poke",
                      ["poke", "--addr", "0x20000000", "--data", "0x2", "3", "0o10"])
    assert args.addr == 0x20000000
    assert args.data == [2, 3, 8]


def test_bad_int0_option_exits_two(no_dispatch):
    code, _out, err = run_cli(["peek", "--addr", "not-a-number"])
    assert code == 2
    assert "--addr" in err


# --------------------------------------------------------------------------
# defaults (read through a spy handler, so nothing runs)
# --------------------------------------------------------------------------
def test_run_defaults(monkeypatch):
    args = parse_only(monkeypatch, "cmd_run", ["run", "--core", "aes1"])
    assert args.key == bytes(range(16))
    assert args.pt == bytes(range(16, 32))
    assert args.inttrig == 0x12
    assert args.trig == "auto"
    assert args.runs == 1
    assert args.nonce is None and args.ad is None
    assert not (args.decrypt or args.random or args.compare
                or args.timer or args.json)


def test_capture_defaults(monkeypatch):
    args = parse_only(monkeypatch, "cmd_capture", ["capture", "--core", "aes1"])
    assert args.traces == 100
    assert args.samples == 5000
    assert args.platform == "asic"
    assert args.clock == 50.0
    assert args.output == "results/run"
    assert args.key == bytes(range(16))
    assert args.gain is None and args.gain_mode is None
    assert not (args.fixed or args.no_scope or args.no_auto_samples)


def test_selfcheck_defaults(monkeypatch):
    args = parse_only(monkeypatch, "cmd_selfcheck", ["selfcheck"])
    assert args.platform == "fpga"          # deliberately NOT capture's 'asic'
    assert args.clock == 50.0
    assert args.samples == 5000
    assert not (args.capture or args.no_swrv)
    assert args.log is None and args.bitstream is None


def test_load_swrv_defaults(monkeypatch):
    args = parse_only(monkeypatch, "cmd_load_swrv",
                      ["load-swrv", "--imem", "i.vmem", "--dmem", "d.vmem"])
    assert args.imem == "i.vmem" and args.dmem == "d.vmem"
    assert args.key == bytes(16)
    assert args.pt == bytes(16)


def test_peek_and_status_and_monitor_defaults(monkeypatch):
    peek = parse_only(monkeypatch, "cmd_peek", ["peek", "--addr", "0x20000000"])
    assert peek.count == 1
    status = parse_only(monkeypatch, "cmd_status", ["status"])
    assert status.watch is None
    mon = parse_only(monkeypatch, "cmd_monitor", ["monitor"])
    assert mon.secs == 5.0


def test_cpa_and_decrypt_soft_defaults(monkeypatch):
    cpa = parse_only(monkeypatch, "cmd_cpa", ["cpa"])
    assert cpa.core == "aes1"
    assert cpa.capture is None and cpa.filter is None
    assert cpa.window is None and cpa.plot is None
    dec = parse_only(monkeypatch, "cmd_decrypt_soft", ["decrypt-soft"])
    assert dec.cipher == "ascon"
    assert dec.selftest is False
    assert dec.key is None and dec.nonce is None
    assert dec.ct is None and dec.tag is None and dec.ad is None


def test_reset_mode_defaults_to_none(monkeypatch):
    # `reset` with no --mode only reports line state; the preset must not be
    # applied implicitly.
    args = parse_only(monkeypatch, "cmd_reset", ["reset"])
    assert args.mode is None


# --------------------------------------------------------------------------
# offline dispatches
# --------------------------------------------------------------------------
def test_version_prints_the_package_version():
    code, out, _err = run_cli(["version"])
    assert code is None
    assert out.strip() == proact_host.__version__


def test_info_prints_the_address_map():
    code, out, _err = run_cli(["info"])
    assert code is None
    for name in ("AES1", "AES2", "XOODYAK", "ASCON", "UART", "TIMER", "RNG",
                 "SCREG", "RII_IMEM", "RII_DMEM"):
        assert f"0x{getattr(regs, name + '_BASE'):08X}" in out
    assert f"0x{regs.CTRL_TRIGGER:08X}" in out
    assert proact_host.__version__ in out


def test_test_subcommand_runs_the_offline_selfchecks():
    code, out, _err = run_cli(["test"])
    assert code is None
    assert "OK" in out


def test_decrypt_soft_selftest_passes():
    code, out, _err = run_cli(["decrypt-soft", "--selftest"])
    assert code == 0
    assert "FAIL" not in out
    assert "PASS" in out


def test_decrypt_soft_without_material_exits_two():
    # Dispatches into cmd_decrypt_soft, which only reaches the argument check.
    code, out, _err = run_cli(["decrypt-soft"])
    assert code == 2
    assert "--key" in out and "--selftest" in out


# --------------------------------------------------------------------------
# _status_bits
# --------------------------------------------------------------------------
def _all_status_constants():
    return {n: getattr(regs, n) for n in dir(regs) if n.startswith("STAT_")}


def test_status_bits_decodes_a_combination():
    value = regs.STAT_DONE_AES1 | regs.STAT_TARGET_DONE
    assert sorted(cli._status_bits(value)) == ["DONE_AES1", "TARGET_DONE"]


def test_status_bits_of_zero_is_empty():
    assert cli._status_bits(0) == []


def test_status_bits_ignores_bits_that_are_clear():
    bits = cli._status_bits(regs.STAT_UART_RVALID)
    assert bits == ["UART_RVALID"]


def test_status_bits_decodes_every_known_flag():
    consts = _all_status_constants()
    value = 0
    for bit in consts.values():
        value |= bit
    assert sorted(cli._status_bits(value)) == sorted(
        n[len("STAT_"):] for n in consts)


def test_status_bits_is_sorted_by_constant_name():
    consts = _all_status_constants()
    value = 0
    for bit in consts.values():
        value |= bit
    names = cli._status_bits(value)
    assert names == sorted(names)


# --------------------------------------------------------------------------
# color helpers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("isatty,no_color,term,expected", [
    (False, None, "xterm", False),    # not a tty -> off, whatever else says
    (True, None, "xterm", True),      # a real terminal -> on
    (True, "1", "xterm", False),      # NO_COLOR wins over a tty
    (True, None, "dumb", False),      # TERM=dumb wins over a tty
])
def test_color_detection_rule(monkeypatch, isatty, no_color, term, expected):
    """Re-evaluate the module's own detection rule against controlled inputs.

    Asserting cli._COLOR directly would depend on how pytest was invoked (under
    -s stdout IS a real terminal), and a single not-a-tty case short-circuits
    before the env vars, never exercising them. Drive all four branches instead.
    """
    class _Stream(io.StringIO):
        def isatty(self): return isatty

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color is not None:
        monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("TERM", term)
    on = (sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
          and os.environ.get("TERM", "") != "dumb")
    assert on is expected


def test_color_helpers_are_inert_and_stringify(monkeypatch):
    monkeypatch.setattr(cli, "_COLOR", False)   # not the ambient tty state
    assert cli._c("31", "hello") == "hello"
    assert cli._c("1", 42) == "42"
    for helper in (cli.bold, cli.dim, cli.red, cli.green, cli.yell,
                   cli.blue, cli.cyan):
        assert helper("plain") == "plain"


def test_no_color_flag_disables_color(monkeypatch):
    monkeypatch.setattr(cli, "_COLOR", True)
    code, out, _err = run_cli(["--no-color", "version"])
    assert code is None
    assert out.strip() == proact_host.__version__
    assert cli._COLOR is False


def test_color_stays_on_without_the_flag(monkeypatch):
    monkeypatch.setattr(cli, "_COLOR", True)
    run_cli(["version"])
    assert cli._COLOR is True


def test_no_color_env_var_disables_color_even_on_a_tty(monkeypatch):
    class _Tty(io.StringIO):
        def isatty(self):
            return True

    try:
        monkeypatch.setattr("sys.stdout", _Tty())
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.delenv("NO_COLOR", raising=False)
        importlib.reload(cli)
        assert cli._COLOR is True, "a real terminal should get color"

        monkeypatch.setenv("NO_COLOR", "1")
        importlib.reload(cli)
        assert cli._COLOR is False

        monkeypatch.delenv("NO_COLOR")
        monkeypatch.setenv("TERM", "dumb")
        importlib.reload(cli)
        assert cli._COLOR is False
    finally:
        monkeypatch.undo()
        importlib.reload(cli)   # restore the captured-output state


# --------------------------------------------------------------------------
# exit status
# --------------------------------------------------------------------------
def test_handler_return_value_becomes_the_exit_status(monkeypatch):
    # cmd_cpa is the handler written that way; demonstrated with cmd_version
    # so no subprocess is spawned.
    monkeypatch.setattr(cli, "cmd_version", lambda _a: 3)
    code, _out, _err = run_cli(["version"])
    assert code == 3


def test_handler_returning_none_is_success(monkeypatch):
    monkeypatch.setattr(cli, "cmd_version", lambda _a: None)
    code, _out, _err = run_cli(["version"])
    assert code is None


def test_handler_returning_zero_is_success(monkeypatch):
    monkeypatch.setattr(cli, "cmd_version", lambda _a: 0)
    code, _out, _err = run_cli(["version"])
    assert code in (None, 0)


def test_cpa_with_a_missing_capture_exits_one(tmp_path):
    # cmd_cpa returns 1 here, before it would spawn the attack script -- the
    # _offline_guard fixture makes spawning one an error.
    code, out, _err = run_cli(["cpa", "--capture", str(tmp_path / "nope.npz")])
    assert code == 1
    assert "capture not found" in out


# --------------------------------------------------------------------------
# unreachable board -> diagnostic, never a traceback
# --------------------------------------------------------------------------
def raise_from_handler(monkeypatch, exc):
    """Dispatch `status` into a handler that raises `exc`, as the real one
    would when the board does not answer.  No port is ever opened."""
    def _boom(_args):
        raise exc

    monkeypatch.setattr(cli, "cmd_status", _boom)
    return run_cli(["status"])


def test_no_frame_marker_prints_a_hint_instead_of_a_traceback(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    code, _out, err = raise_from_handler(monkeypatch, TimeoutError("no frame marker"))
    assert code == 1
    assert "Traceback" not in err
    assert err.startswith("error: ")
    assert "did not answer" in err and "no frame marker" in err
    assert "program --vmem Software/Controller/main.vmem" in err
    assert "docs/bringup_guide.md" in err
    assert "PROACT_DEBUG=1" in err


def test_missing_usb_device_points_at_the_udev_rules(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    code, _out, err = raise_from_handler(monkeypatch, RuntimeError(
        "No MCP2200 UART device found. Ensure the MCP2200 is connected via USB "
        "and that you have the necessary permissions (udev rules on Linux)."))
    assert code == 1
    assert "Traceback" not in err
    assert "check USB" in err
    assert "install_udev.sh" in err


def test_permission_error_gets_the_same_hint_as_the_gui(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    code, _out, err = raise_from_handler(monkeypatch, OSError(
        "[Errno 13] could not open port /dev/ttyUSB0: Permission denied"))
    assert code == 1
    assert "Traceback" not in err
    assert "sudo bash tools/install_udev.sh" in err


def test_missing_driver_package_keeps_its_own_install_line(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    code, _out, err = raise_from_handler(monkeypatch, ImportError(
        "The 'hidapi' library is missing. Install it with: pip install hidapi"))
    assert code == 1
    assert "Traceback" not in err
    assert "pip install hidapi" in err


def test_missing_chipwhisperer_import_gets_a_hint(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    code, _out, err = raise_from_handler(
        monkeypatch, ImportError("No module named 'chipwhisperer'"))
    assert code == 1
    assert "Traceback" not in err
    assert "pip install chipwhisperer" in err


def test_generic_import_error_points_at_setup_env(monkeypatch):
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    code, _out, err = raise_from_handler(
        monkeypatch, ImportError("No module named 'mcp2210'"))
    assert code == 1
    assert "setup_env.sh" in err


def test_proact_debug_keeps_the_full_traceback(monkeypatch):
    monkeypatch.setenv("PROACT_DEBUG", "1")
    with pytest.raises(TimeoutError, match="no frame marker"):
        raise_from_handler(monkeypatch, TimeoutError("no frame marker"))


def test_proact_debug_zero_is_not_debug(monkeypatch):
    monkeypatch.setenv("PROACT_DEBUG", "0")
    code, _out, err = raise_from_handler(monkeypatch, TimeoutError("no frame marker"))
    assert code == 1
    assert "Traceback" not in err


@pytest.mark.parametrize("exc", [
    ValueError("block must be 16 bytes"),
    KeyError("aes3"),
    AttributeError("'ProactTarget' object has no attribute 'nope'"),
])
def test_host_side_bugs_are_not_disguised_as_bench_errors(monkeypatch, exc):
    # The diagnostic covers the bench, not the host: a real defect must still
    # reach the user as a traceback.
    monkeypatch.delenv("PROACT_DEBUG", raising=False)
    with pytest.raises(type(exc)):
        raise_from_handler(monkeypatch, exc)


def test_handler_sys_exit_is_not_intercepted(monkeypatch):
    def _bye(_args):
        raise SystemExit(2)

    monkeypatch.setattr(cli, "cmd_status", _bye)
    code, _out, err = run_cli(["status"])
    assert code == 2
    assert err == ""


def test_module_docstring_banner_lists_every_subcommand():
    # The docstring is the description `proact --help` prints; every subcommand
    # must appear in it, or that command is invisible in the banner.
    banner = cli.__doc__.split("INFO", 1)[1].split("Examples:", 1)[0]
    words = set(re.findall(r"[a-z][a-z0-9-]*", banner))
    missing = [name for name in EXPECTED_SUBCOMMANDS if name not in words]
    assert missing == []
