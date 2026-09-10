"""
proact -- unified command-line interface (installed as the `proact` entry
point via pyproject.toml; on the bench use ./run_cli.sh). Same backend as
the GUI, so everything the GUI does is scriptable here.

INFO         info | doctor [--json] | devices | status [--watch] | timer | version
BUILD/LOAD   build-controller | build-target | program | load-swrv
RUN          run | capture | cpa | aead-kat | decrypt-soft | seed
CONTROL      reset | restart | peek | poke
CHECK        test | selfcheck | selftest
MISC         monitor | gui

Examples:
  ./run_cli.sh info
  ./run_cli.sh run --core aes1 --compare --timer
  ./run_cli.sh run --core ascon --key 00112233445566778899aabbccddeeff
  ./run_cli.sh decrypt-soft --cipher ascon --selftest
  ./run_cli.sh selfcheck --capture --platform fpga
  ./run_cli.sh peek --addr 0x20000000 --count 4
  ./run_cli.sh reset --mode run
Colors: automatic on a terminal; disable with --no-color or NO_COLOR=1.
Errors: a bench failure prints a short hint; PROACT_DEBUG=1 keeps the traceback.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

from . import config, regs

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# ------------------------------------------------------------------ colors
_COLOR = (sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
          and os.environ.get("TERM", "") != "dumb")


def _c(code, txt):
    return f"\033[{code}m{txt}\033[0m" if _COLOR else str(txt)


def bold(t):  return _c("1", t)
def dim(t):   return _c("2", t)
def red(t):   return _c("31", t)
def green(t): return _c("32", t)
def yell(t):  return _c("33", t)
def blue(t):  return _c("34", t)
def cyan(t):  return _c("36", t)


def head(t):
    print(bold(blue(f"\n== {t} ==")))


def ok(t):    print(f"  {green('✔')} {t}")
def bad(t):   print(f"  {red('✘')} {t}")
def note(t):  print(f"  {yell('•')} {t}")
def kv(k, v): print(f"  {cyan(f'{k:<14}')} {v}")


# ------------------------------------------------------------------ helpers
def _hex16(s):
    b = _hexbytes(s)
    if len(b) != 16:
        raise argparse.ArgumentTypeError("expected 16 bytes (32 hex chars)")
    return b


def _hexbytes(s):
    try:
        return bytes.fromhex(s.replace(" ", ""))
    except ValueError:
        raise argparse.ArgumentTypeError("expected hex bytes")


def _int0(s):
    return int(s, 0)


def _positive_int(s):
    value = int(s)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _finite_float(s):
    value = float(s)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("must be a finite number")
    return value


def _positive_float(s):
    value = _finite_float(s)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _u32(s):
    value = _int0(s)
    if not 0 <= value <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("must be an unsigned 32-bit value (0..0xffffffff)")
    return value


def _address(s):
    value = _u32(s)
    if value % 4:
        raise argparse.ArgumentTypeError("address must be aligned to a 4-byte word")
    return value


def _trigger_phase(s):
    value = _int0(s)
    if not 0 <= value <= 127:
        raise argparse.ArgumentTypeError("trigger phase must be in 0..127")
    return value


class InputError(ValueError):
    """A user-supplied file or option is invalid before any device action."""


def _firmware_words(path, *, allow_empty=False):
    from .vmem import parse_vmem
    try:
        words = parse_vmem(path)
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot use firmware {path!r}: {exc}") from exc
    if not words and not allow_empty:
        raise InputError(f"firmware {path!r} contains no data words")
    return words


def _target(a):
    """Open the UART and return (transport, ProactTarget) ready to use."""
    from .transport import ProactTarget, UartTransport
    t = UartTransport(port=a.port).open()
    try:
        tgt = ProactTarget(t)
        tgt.enable_sendback()
    except BaseException:
        t.close()
        raise
    return t, tgt


def _status_bits(value):
    out = []
    for name in sorted(dir(regs)):
        if name.startswith("STAT_"):
            bit = getattr(regs, name)
            if isinstance(bit, int) and bit and (value & bit) == bit:
                out.append(name[5:])
    return out


# ------------------------------------------------------------------ info
def cmd_info(_a):
    head("proact-host")
    kv("version", __import__("proact_host").__version__)
    kv("repo", REPO)
    kv("input clock", f"{config.INPUT_CLOCK_HZ/1e6:.1f} MHz {dim('(config.py)')}")
    head("address map " + dim("(config/hardware.json -> regs.py)"))
    for name in ("AES1", "AES2", "XOODYAK", "ASCON", "UART", "TIMER", "RNG",
                 "SCREG", "RII_IMEM", "RII_DMEM"):
        kv(name, f"0x{getattr(regs, name + '_BASE'):08X}")
    head("facts every user needs")
    note(f"capture trigger = control bit30 (0x{regs.CTRL_TRIGGER:08X}) -- NOT bit31")
    note("ASCON/Xoodyak hardware is ENCRYPT-only; decrypt in software "
         "(`proact decrypt-soft`, proact_host.aead_soft)")
    note("never launch with sudo; use ./run_cli.sh / ./run_gui.sh")
    kv("docs", "docs/START_HERE.md | docs/CLI_REFERENCE.md | docs/ARCHITECTURE.md")
    kv("references", "docs/manual/proact_manual.pdf | examples/PROACT_Tutorial.ipynb (historical)")
    kv("wiki", "https://github.com/abolfazlsajadi/PROACT_Design/wiki")


def cmd_devices(_a):
    head("USB bench devices")
    try:
        import hid
        found = []
        for vid, pid, kind in ((0x04D8, 0x00DE, "MCP2210 (SPI loader)"),
                               (0x04D8, 0x00DF, "MCP2200 (UART)")):
            for d in hid.enumerate(vid, pid):
                ok(f"{kind}  serial={d.get('serial_number')}")
                found.append(kind)
        if not found:
            bad("no MCP2200/MCP2210 found " + dim("(check USB + udev rules)"))
    except Exception as e:  # noqa: BLE001
        bad(f"hid unavailable: {e}")
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            kv(p.device, p.description)
    except Exception as e:  # noqa: BLE001
        bad(f"pyserial unavailable: {e}")
    try:
        import chipwhisperer  # noqa: F401
        ok("chipwhisperer importable (scope support available)")
    except Exception:  # noqa: BLE001
        note("chipwhisperer not importable (capture disabled)")


def cmd_status(a):
    t, tgt = _target(a)
    try:
        while True:
            v = tgt.read_status()
            bits = _status_bits(v)
            print(f"status = {bold(f'0x{v:08X}')}   {green(' '.join(bits)) if bits else dim('(no bits set)')}")
            if not a.watch:
                break
            time.sleep(a.watch)
    except KeyboardInterrupt:
        pass
    finally:
        t.close()


def cmd_timer(a):
    t, tgt = _target(a)
    try:
        cyc = tgt.get_timer()
        clk = config.INPUT_CLOCK_HZ
        kv("cycles", f"{cyc} (0x{cyc:X})")
        kv("time", f"{cyc/clk*1e6:.2f} us @ {clk/1e6:.0f} MHz")
    finally:
        t.close()


def cmd_version(_a):
    print(__import__("proact_host").__version__)


def cmd_doctor(a):
    """Show installation facts without opening or enumerating any devices."""
    from .diagnostics import environment_report
    report = environment_report()
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        head("PROACT environment — offline")
        kv("version", report["proact_version"])
        kv("Python", report["python"])
        kv("workspace", report["workspace"])
        for dep in report["dependencies"]:
            label = "available" if dep["discoverable"] else "missing"
            kv(dep["purpose"], f"{label}: {dep['distribution']} {dep['version'] or ''}".rstrip())
        note(report["check_scope"])
        note("Setup: bash tools/setup_env.sh --with gui --with hdf5 --with dev")
        missing = [p for p, present in report["files"].items() if not present]
        if missing:
            note("Not built/present: " + ", ".join(missing))
    return 0 if report["core_dependencies_available"] else 1


# ------------------------------------------------------------------ build
def _make(subdir, extra=None):
    d = os.path.join(REPO, "Software", subdir)
    cmd = ["make", "-C", d, "all"] + (extra or [])
    print(dim("$ " + " ".join(cmd)))
    return subprocess.call(cmd)


def cmd_build_controller(a):
    sys.exit(_make("Controller", ["RISCV=" + a.riscv] if a.riscv else None))


def cmd_build_target(a):
    sys.exit(_make("SW_RV", ["RISCV=" + a.riscv] if a.riscv else None))


def cmd_test(_a):
    from .transport import ProactTarget, block_to_words, words_to_block

    class M:
        def __init__(s): s.buf = bytearray()
        def write(s, b): s.buf += b
        def read(s, n): return b""
    t = M(); tg = ProactTarget(t); k = bytes(range(16))
    tg.select("aes1"); tg.set_key(k); tg.run()
    assert t.buf[0] == regs.CMD_AES1 and t.buf[1] == regs.CMD_KEY and t.buf[2:18] == k
    assert words_to_block(block_to_words(k)) == k
    from .validation import aes128_encrypt_block
    assert aes128_encrypt_block(bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
                                bytes.fromhex("00112233445566778899aabbccddeeff")).hex() \
        == "69c4e0d86a7b0430d8cdb78070b4c55a"
    from .aead_soft import selftest
    res = selftest()
    assert all(res.values()), res
    ok("host protocol + AES reference + software ASCON/Xoodyak: OK")


# ------------------------------------------------------------------ run
def cmd_run(a):
    from .validation import validate_aes, validate_aead
    if a.decrypt and a.core in ("ascon", "xoodyak"):
        note(yell("AEAD hardware is ENCRYPT-only on this silicon; a hardware "
                  "decrypt times out and returns zeros."))
        note("use:  proact decrypt-soft --cipher %s ..." % a.core)
    t, tgt = _target(a)
    results = []
    try:
        tgt.select(a.core)
        tgt.set_cfgsel(None if a.trig == "auto" else a.trig)
        if a.core in ("ascon", "xoodyak"):
            tgt.set_trigger_cfg(a.inttrig)
        tgt.set_decrypt(a.decrypt)
        import secrets
        for i in range(a.runs):
            pt = secrets.token_bytes(16) if a.random else a.pt
            tgt.set_key(a.key)
            if a.core in ("ascon", "xoodyak"):
                tgt.set_nonce(a.nonce or bytes(16)); tgt.set_ad(a.ad or bytes(16))
            tgt.set_plaintext(pt)
            mode, payload = tgt.run_and_read()
            row = {"run": i, "pt": pt.hex(), "mode": mode, "out": payload.hex()}
            if a.core in ("ascon", "xoodyak") and len(payload) >= 32:
                row["ct"], row["tag"] = payload[:16].hex(), payload[16:32].hex()
            if a.compare:
                if a.core in ("aes1", "aes2", "swrv"):
                    row["pass"] = validate_aes(a.key, pt, payload[:16], decrypt=a.decrypt)
                elif a.core in ("ascon", "xoodyak"):
                    row["pass"] = validate_aead(a.core, a.key, pt, payload,
                                                nonce=a.nonce, ad=a.ad, decrypt=a.decrypt)
            if a.timer:
                row["cycles"] = tgt.get_timer()
            results.append(row)
            if not a.json:
                extra = ""
                if "pass" in row:
                    extra += "  " + (green("PASS") if row["pass"] else red("FAIL"))
                if "cycles" in row:
                    extra += dim(f"  cyc={row['cycles']}")
                if "tag" in row:
                    print(f"  {i:3d} pt={row['pt']} -> ct={row['ct']} tag={row['tag']}{extra}")
                else:
                    print(f"  {i:3d} pt={row['pt']} -> {row['out']}{extra}")
    finally:
        t.close()
    if a.json:
        print(json.dumps(results, indent=1))
    if a.compare and any(r.get("pass") is False for r in results):
        sys.exit(1)


def cmd_aead_kat(a):
    t, tgt = _target(a)
    try:
        xoo, asc = tgt.aead_kat()
        (ok if asc else bad)("ASCON   on-chip encrypt KAT " + ("PASS" if asc else "FAIL"))
        (ok if xoo else bad)("Xoodyak on-chip encrypt KAT " + ("PASS" if xoo else "FAIL"))
        sys.exit(0 if (xoo and asc) else 1)
    finally:
        t.close()


def cmd_decrypt_soft(a):
    from . import aead_soft
    if a.selftest:
        head("software ASCON/Xoodyak self-test vs the silicon's vectors")
        res = aead_soft.selftest()
        for k, v in res.items():
            (ok if v else bad)(f"{k}: {'PASS' if v else 'FAIL'}")
        sys.exit(0 if all(res.values()) else 1)
    if not (a.key and a.nonce and a.ct is not None and a.tag):
        bad("need --key --nonce --ct --tag (or --selftest)"); sys.exit(2)
    dec = aead_soft.ascon128_decrypt if a.cipher == "ascon" else aead_soft.xoodyak_decrypt
    pt = dec(a.key, a.nonce, a.ad or b"", a.ct, a.tag)
    if pt is None:
        bad("TAG MISMATCH -- no plaintext released"); sys.exit(1)
    ok(f"tag verified; plaintext = {pt.hex()}")


def cmd_seed(a):
    t, tgt = _target(a)
    try:
        tgt.seed_rng(a.value)
        ok(f"PRNG seeded with 0x{a.value:08X}")
    finally:
        t.close()


def cmd_cpa(a):
    """Run the CPA attack on a capture file -- no board needed."""
    import subprocess
    import sys
    script = "cpa_swrv.py" if a.core == "swrv" else "cpa_lastround.py"
    path = a.capture
    if path is None:                       # default to the shipped reference capture
        path = os.path.join(REPO, "datasets", f"{a.core}_reference.npz")
        note(f"no --capture given, using the reference dataset {path}")
    if not os.path.exists(path):
        bad(f"capture not found: {path}"); return 1
    cmd = [sys.executable, os.path.join(REPO, "examples", script), path]
    if a.filter is not None:
        cmd += ["--filter", str(a.filter)]
    if a.window:
        cmd += ["--window", a.window]
    if a.plot:
        cmd += ["--plot", a.plot]
    head(f"CPA on {os.path.basename(path)} ({a.core})")
    return subprocess.call(cmd)


def cmd_capture(a):
    from .experiment import PROACTExperiment
    with PROACTExperiment(platform=a.platform, target=a.core, traces=a.traces,
                          output=a.output, randomize=not a.fixed, key=a.key,
                          capture=not a.no_scope, samples=a.samples,
                          clock_hz=a.clock * 1e6, bitstream=a.bitstream,
                          gain_db=a.gain, gain_mode=a.gain_mode,
                          auto_samples=not a.no_auto_samples) as exp:
        exp.prepare()
        completed = exp.capture()
        exp.save()
        if completed != a.traces:
            print(f"error: saved {completed} of {a.traces} requested records; inspect the dataset failure log",
                  file=sys.stderr)
            return 1


# ------------------------------------------------------------------ control
def cmd_program(a):
    from .programmer import Mcp2210Programmer
    from .transport import UartTransport
    _firmware_words(a.vmem)  # validate before opening the SPI bridge
    prog = Mcp2210Programmer(serial=a.serial).open()
    # Confirm the load actually produced a running chip (the SPI slave is
    # write-only, so program() alone cannot tell -- see verify_running).
    try:
        prog.program(a.vmem, progress=lambda p: print(f"\r  {p:3d}%", end="", flush=True))
        print()
        uart = None
        try:
            uart = UartTransport(port=a.port).open()
            running = prog.verify_running(uart)
        except Exception:  # noqa: BLE001 -- report verification unavailable separately
            running = None
        finally:
            if uart is not None:
                uart.close()
    finally:
        prog.close()
    if running:
        ok(f"programmed {a.vmem}; controller booted (announced itself over UART)")
    elif running is False:
        ok(f"programmed {a.vmem} over SPI")
        print(yell("  warning: the controller did not announce itself over UART."),
              file=sys.stderr)
        for hint in ("on FPGA, upload the bitstream first (ChipWhisperer tab / see docs/bringup_guide.md)",
                     "check the MCP2200 UART is connected"):
            print(f"  -> {hint}", file=sys.stderr)
    else:
        note(f"SPI transfer completed for {a.vmem}; controller boot could not be verified over UART")


def cmd_reset(a):
    from .programmer import Mcp2210Programmer
    from .resets import ResetController
    prog = Mcp2210Programmer().open()
    try:
        rc = ResetController(prog)
        if a.mode:
            rc.apply_mode(a.mode)
            ok(f"applied preset '{a.mode}'")
        st = rc.status()
    finally:
        prog.close()
    head("reset lines " + dim("(True = released/active)"))
    for name, val in st.items():
        col = green("active") if val else (red("held") if val is False else dim("?"))
        kv(name, col)


def cmd_restart(a):
    from .programmer import Mcp2210Programmer
    prog = Mcp2210Programmer(serial=a.serial).open()
    try:
        prog.restart_controller()
    finally:
        prog.close()
    ok("controller restarted (full run state restored)")


def cmd_peek(a):
    t, tgt = _target(a)
    try:
        for i, w in enumerate(tgt.peek_words(a.addr, a.count)):
            print(f"  0x{a.addr + 4*i:08X}: {bold(f'0x{w:08X}')}")
    finally:
        t.close()


def cmd_poke(a):
    t, tgt = _target(a)
    try:
        tgt.poke_words(a.addr, a.data)
        ok(f"wrote {len(a.data)} word(s) at 0x{a.addr:08X}")
        note(yell("poking CPU RAM can wedge the chip -- know your address"))
    finally:
        t.close()


# ------------------------------------------------------------------ checks
def cmd_selfcheck(a):
    from .fullcheck import run_full_check, summarize
    from .vmem import parse_vmem
    scope = None
    if a.capture:
        from .capture import ChipWhispererCapture
        scope = ChipWhispererCapture(samples=a.samples, clock_hz=a.clock * 1e6,
                                     platform=a.platform)
        scope.connect(platform=a.platform,
                      bitstream=(a.bitstream if a.platform == "fpga" and a.bitstream else None))
    swrv = None
    if not a.no_swrv:
        imf = os.path.join(REPO, "Software", "SW_RV", "sw_rv_imem.vmem")
        dmf = os.path.join(REPO, "Software", "SW_RV", "sw_rv_dmem.vmem")
        if os.path.exists(imf) and os.path.exists(dmf):
            swrv = ([v for _, v in parse_vmem(imf)],
                    [v for _, v in parse_vmem(dmf)], regs.SWRV_DMEM_LOAD_BASE)
        else:
            note("Sw-RV vmem not built (make -C Software/SW_RV) -- step will SKIP")
    t, tgt = _target(a)
    lines = []
    try:
        head("PROACT full self-check (A-Z)")

        def on_item(it):
            color = {"PASS": green, "FAIL": red, "SKIP": yell}.get(it.status, dim)
            print(f"  [{color(it.status)}] {it.name:22s} {dim(it.detail)}")
            lines.append(it.line())
        items = run_full_check(tgt, scope=scope, clock_hz=a.clock * 1e6,
                               swrv_words=swrv, do_capture=a.capture,
                               on_item=on_item, platform=a.platform)
        p, f, s = summarize(items)
        verdict = green(bold("ALL PASS ✔")) if f == 0 else red(bold(f"{f} FAILED ✘"))
        print(f"\n  {verdict}   {p} pass / {f} fail / {s} skip")
        if a.log:
            with open(a.log, "w") as fh:
                fh.write("\n".join(lines) + f"\n{p} PASS {f} FAIL {s} SKIP\n")
            note(f"log written to {a.log}")
        sys.exit(1 if f else 0)
    finally:
        t.close()
        if scope:
            scope.disconnect()


def cmd_selftest(a):
    t, tgt = _target(a)
    try:
        tgt.enable_debug()
        tgt.self_test()
        time.sleep(2)
        print(t.read(4096).decode(errors="replace"))
    finally:
        t.close()


def cmd_load_swrv(a):
    imem = [value for _, value in _firmware_words(a.imem)]
    # Programs without initialized .data/.rodata legitimately have no DMEM
    # words; the existing loader represents this with a zero-count transfer.
    dmem = [value for _, value in _firmware_words(a.dmem, allow_empty=True)]
    t, tgt = _target(a)
    try:
        tgt.load_swrv_program(imem, dmem,
                              regs.SWRV_DMEM_LOAD_BASE)
        ok("Sw-RV program loaded and booted")
        tgt.set_key(a.key)
        tgt.set_plaintext(a.pt)
        mode, payload = tgt.run_and_read()
        kv("sw-AES result", payload.hex())
    finally:
        t.close()


def cmd_monitor(a):
    from .monitor import MonitorDecoder
    t, tgt = _target(a)
    mon = MonitorDecoder()
    note(f"dumping UART for {a.secs}s (Ctrl-C to stop)")
    end = time.monotonic() + a.secs
    try:
        while time.monotonic() < end:
            data = t.read_available()
            for text, hexcol in mon.feed(data):
                if text or hexcol:
                    print(f"  {text} {dim(hexcol)}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        t.close()


def cmd_gui(_a):
    gui = os.path.join(REPO, "Software", "GUI", "proact_gui.py")
    sys.exit(subprocess.call([sys.executable, gui]))


# ------------------------------------------------------------------ errors
# Everything that reaches the bench fails through one of these: TimeoutError
# (no / short reply frame), OSError incl. serial.SerialException (the port
# itself), RuntimeError (no MCP2200/MCP2210, port already locked, scope not
# connected) and ImportError (a driver package is missing). Those are bench
# conditions, not host bugs, so they get a diagnostic; anything else
# (ValueError, KeyError, AssertionError, ...) keeps its traceback.
_BENCH_ERRORS = (OSError, RuntimeError, ImportError)   # TimeoutError is an OSError

_PROGRAM_HINT = ("is the firmware loaded?  "
                 "./run_cli.sh program --vmem Software/Controller/main.vmem")


def _debug():
    return os.environ.get("PROACT_DEBUG", "") not in ("", "0")


def _hints(exc):
    """Actionable next steps for a bench error -- same wording as the GUI's
    MainWindow._hint(), which reports the very same failures."""
    s = str(exc).lower()
    if "frame" in s:
        return [_PROGRAM_HINT,
                "on FPGA, upload the bitstream first (see docs/bringup_guide.md)"]
    # Detection is tested before permissions: the "No MCP22xx ... found"
    # messages mention permissions too, and USB comes first when it is missing.
    if ("no device" in s or "device found" in s or "not found" in s
            or "no cw" in s or "returned none" in s):
        msg = "device not detected: check USB"
        if sys.platform == "linux":
            msg += ", and run  sudo bash tools/install_udev.sh"
        return [msg]
    if ("permission" in s or "denied" in s or "unable to open" in s
            or "could not open" in s or "errno 13" in s or "access" in s):
        if sys.platform == "linux":
            return ["run once:  sudo bash tools/install_udev.sh   then replug "
                    "the device (no sudo needed after)"]
        return ["check if another program is using the device, and try replugging it"]
    if isinstance(exc, ImportError):
        # a driver package is missing -- point at the one setup script
        if "chipwhisperer" in s:
            return ["install it:  pip install chipwhisperer"]
        return ["missing dependencies: run  bash tools/setup_env.sh"]
    return []


def _fail(exc):
    """Print a short diagnostic for a bench error and return the exit status."""
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, TimeoutError) and "frame" in text.lower():
        text = f"the controller did not answer ({text})"
    print(f"{red('error')}: {text}", file=sys.stderr)
    for hint in _hints(exc):
        print(f"  -> {hint}", file=sys.stderr)
    print(dim("  (PROACT_DEBUG=1 for the full traceback)"), file=sys.stderr)
    return 1


# ------------------------------------------------------------------ parser
def main(argv=None):
    global _COLOR
    ap = argparse.ArgumentParser(prog="proact", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: auto-detect the MCP2200)")
    ap.add_argument("--no-color", action="store_true", help="disable colored output")
    ap.add_argument("--version", action="version", version=__import__("proact_host").__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="version, address map, key facts").set_defaults(func=cmd_info)
    p = sub.add_parser("doctor", help="offline environment diagnostics; no device access")
    p.add_argument("--json", action="store_true", help="machine-readable environment report")
    p.set_defaults(func=cmd_doctor)
    sub.add_parser("devices", help="list bench USB devices").set_defaults(func=cmd_devices)
    p = sub.add_parser("status", help="read + decode the status register")
    p.add_argument("--watch", type=_positive_float, metavar="SECS", help="repeat every positive SECS")
    p.set_defaults(func=cmd_status)
    sub.add_parser("timer", help="read the trigger-window cycle counter").set_defaults(func=cmd_timer)
    sub.add_parser("version", help="print the version").set_defaults(func=cmd_version)

    p = sub.add_parser("build-controller", help="make the controller firmware")
    p.add_argument("--riscv", help="toolchain prefix"); p.set_defaults(func=cmd_build_controller)
    p = sub.add_parser("build-target", help="make the Sw-RV firmware")
    p.add_argument("--riscv"); p.set_defaults(func=cmd_build_target)
    sub.add_parser("test", help="offline host-side self-checks").set_defaults(func=cmd_test)

    p = sub.add_parser("run", help="run crypto operations and print results")
    p.add_argument("--core", required=True, choices=["aes1", "aes2", "ascon", "xoodyak", "swrv"])
    p.add_argument("--key", type=_hex16, default=bytes(range(16)), help="16-byte key (hex)")
    p.add_argument("--pt", type=_hex16, default=bytes(range(16, 32)), help="16-byte input (hex)")
    p.add_argument("--nonce", type=_hex16, help="AEAD nonce (default zeros)")
    p.add_argument("--ad", type=_hex16, help="AEAD associated data (default zeros)")
    p.add_argument("--decrypt", action="store_true")
    p.add_argument("--runs", type=_positive_int, default=1, help="repeat N times (N > 0)")
    p.add_argument("--random", action="store_true", help="fresh random plaintext per run")
    p.add_argument("--compare", action="store_true", help="check AES/Sw-RV vs software reference")
    p.add_argument("--timer", action="store_true", help="read cycle count per run")
    p.add_argument("--trig", default="auto",
                   choices=["auto", "software", "aes1", "aes2", "ascon", "xoodyak", "swrv"],
                   help="trigger-source mux (cfg_sel)")
    p.add_argument("--inttrig", type=_trigger_phase, default=0x12,
                   help="AEAD in-core trigger phase (7-bit, default 0x12)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("aead-kat", help="on-chip ASCON+Xoodyak reference-vector KAT")
    p.set_defaults(func=cmd_aead_kat)

    p = sub.add_parser("decrypt-soft", help="software AEAD decrypt + tag verify (aead_soft)")
    p.add_argument("--cipher", choices=["ascon", "xoodyak"], default="ascon")
    p.add_argument("--key", type=_hex16); p.add_argument("--nonce", type=_hex16)
    p.add_argument("--ct", type=_hexbytes, help="ciphertext hex (any length)")
    p.add_argument("--tag", type=_hex16); p.add_argument("--ad", type=_hexbytes)
    p.add_argument("--selftest", action="store_true",
                   help="validate both ciphers against the silicon's vectors")
    p.set_defaults(func=cmd_decrypt_soft)

    p = sub.add_parser("seed", help="seed the masking PRNG")
    p.add_argument("--value", type=_u32, required=True, help="32-bit seed (e.g. 0xACE1ACE1)")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("cpa", help="run the CPA attack on a capture (offline, no board)")
    p.add_argument("--core", default="aes1", choices=["aes1", "aes2", "swrv"],
                   help="which attack to run: aes1/aes2 use the last-round ciphertext "
                        "model, swrv the first-round S-box model")
    p.add_argument("--capture", help="capture .npz/.h5 (default: the matching file in "
                                     "datasets/, so this works with no board)")
    p.add_argument("--filter", help="moving-average width: 'auto', an integer, or 1 to "
                                    "disable (see the ChipWhisperer wiki page)")
    p.add_argument("--window", help="sample window lo:hi, or 'auto' (default)")
    p.add_argument("--plot", metavar="PNG", help="save the correlation figure")
    p.set_defaults(func=cmd_cpa)

    p = sub.add_parser("capture", help="capture power traces")
    p.add_argument("--core", required=True, choices=["aes1", "aes2", "ascon", "xoodyak", "swrv"])
    p.add_argument("--traces", type=_positive_int, default=100,
                   help="number of records to acquire; required budget depends on the measured setup")
    p.add_argument("--output", default="results/run")
    p.add_argument("--platform", default="asic", choices=["asic", "fpga"])
    p.add_argument("--key", type=_hex16, default=bytes(range(16)))
    p.add_argument("--samples", type=_positive_int, default=5000,
                   help="samples per trace; grown automatically to cover the whole "
                        "trigger window unless --no-auto-samples")
    p.add_argument("--clock", type=_positive_float, default=50.0, help="target clock MHz")
    p.add_argument("--bitstream", help="program this CW305 bitstream first (fpga)")
    p.add_argument("--gain", type=_finite_float,
                   help="ADC gain in dB (default: per-core recommendation -- 10 dB for "
                        "the hardware cores, 20 dB for swrv). Too low wastes ADC range "
                        "and costs traces; too high clips and destroys leakage")
    p.add_argument("--gain-mode", choices=["low", "high"], help="ADC gain mode (default low)")
    p.add_argument("--no-auto-samples", action="store_true",
                   help="do not grow --samples to the measured trigger window")
    p.add_argument("--fixed", action="store_true", help="fixed input (default random)")
    p.add_argument("--no-scope", action="store_true", help="functional only, no traces")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("program", help="load controller firmware over SPI")
    p.add_argument("--vmem", required=True); p.add_argument("--serial")
    p.set_defaults(func=cmd_program)

    p = sub.add_parser("reset", help="apply a reset preset and/or show line states")
    p.add_argument("--mode", choices=["run", "controller", "global", "spi", "reset_all"],
                   help="preset to apply (omit to just show states)")
    p.set_defaults(func=cmd_reset)
    p = sub.add_parser("restart", help="reboot the controller (restores run state)")
    p.add_argument("--serial"); p.set_defaults(func=cmd_restart)

    p = sub.add_parser("peek", help="raw bus read (CMD_PEEK)")
    p.add_argument("--addr", type=_address, required=True)
    p.add_argument("--count", type=_positive_int, default=1, help="consecutive words")
    p.set_defaults(func=cmd_peek)
    p = sub.add_parser("poke", help="raw bus write (CMD_POKE) -- know your address!")
    p.add_argument("--addr", type=_address, required=True)
    p.add_argument("--data", type=_u32, nargs="+", required=True, help="unsigned 32-bit word(s)")
    p.set_defaults(func=cmd_poke)

    p = sub.add_parser("selfcheck", help="the unified A-Z self-check (same as the GUI tab)")
    p.add_argument("--capture", action="store_true", help="include a real trace capture")
    p.add_argument("--platform", default="fpga", choices=["asic", "fpga"])
    p.add_argument("--clock", type=_positive_float, default=50.0, help="target clock MHz")
    p.add_argument("--samples", type=_positive_int, default=5000)
    p.add_argument("--bitstream", help="program this CW305 bitstream first (fpga)")
    p.add_argument("--no-swrv", action="store_true", help="skip the Sw-RV step")
    p.add_argument("--log", help="also write a plain-text report")
    p.set_defaults(func=cmd_selfcheck)

    sub.add_parser("selftest", help="on-chip debug self-test (prints firmware log)"
                   ).set_defaults(func=cmd_selftest)

    p = sub.add_parser("load-swrv", help="load + boot a program on the Sw-RV target")
    p.add_argument("--imem", required=True); p.add_argument("--dmem", required=True)
    p.add_argument("--key", type=_hex16, default=bytes(16))
    p.add_argument("--pt", type=_hex16, default=bytes(16))
    p.set_defaults(func=cmd_load_swrv)

    p = sub.add_parser("monitor", help="dump raw UART output (noise-safe)")
    p.add_argument("--secs", type=_positive_float, default=5.0)
    p.set_defaults(func=cmd_monitor)

    sub.add_parser("gui", help="launch the GUI").set_defaults(func=cmd_gui)

    args = ap.parse_args(argv)
    if args.cmd == "run" and args.decrypt and args.core in ("ascon", "xoodyak"):
        ap.error("ASCON/Xoodyak hardware supports encryption only; use decrypt-soft for host decryption")
    if args.cmd in ("peek", "poke"):
        count = args.count if args.cmd == "peek" else len(args.data)
        if args.addr + 4 * (count - 1) > 0xFFFFFFFF:
            ap.error("word range exceeds the 32-bit address space")
    if args.no_color:
        _COLOR = False
    try:
        rc = args.func(args)          # a handler may return its exit status
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        rc = 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        rc = 130
    except _BENCH_ERRORS as exc:
        if _debug():
            raise
        rc = _fail(exc)
    if rc:                            # None / 0 = success
        sys.exit(rc)


if __name__ == "__main__":
    sys.exit(main())
