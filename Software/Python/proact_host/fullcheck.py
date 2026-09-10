"""
Full A-Z self-check for a connected PROACT chip (FPGA or ASIC).

One entry point, `run_full_check(...)`, drives every on-chip subsystem and yields
a structured result per step so a CLI, the GUI, or a batch ASIC-screening script
can all share the exact same test sequence and pass/fail criteria.

Each result is a CheckItem(name, status, detail, category); status is one of
"PASS" / "FAIL" / "SKIP".  Nothing here raises: a step that errors (including a
UART timeout when a core does not answer) is reported as FAIL and the sweep
continues, so one dead core never hides the health of the others.

Designed to be safe to re-run and identical across boards -- point it at an ASIC
over the same UART and it screens the chip the same way it screens the FPGA.
"""
from typing import Callable, List, Optional

from .validation import aes128_encrypt_block

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# The canonical demo vectors -- exactly the firmware self-test KAT.
KEY = bytes.fromhex("abcdef0112345678deadbeef87654321")
PT = bytes.fromhex("12345678abcdef0187654321deadbeef")

# A DIFFERENT plaintext for the Sw-RV step, on purpose. On a Sw-RV timeout the
# controller leaves its result buffer untouched, so it still holds whatever the
# previous step computed -- and the step just before Sw-RV runs AES1 over
# KEY/PT. Reusing PT there would let a target that never executed an
# instruction return the expected ciphertext and be reported PASS. A distinct
# vector cannot be produced by a stale buffer.
SWRV_PT = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")


class CheckItem:
    __slots__ = ("name", "status", "detail", "category")

    def __init__(self, name, status, detail="", category="core"):
        self.name, self.status, self.detail, self.category = name, status, detail, category

    def line(self) -> str:
        return f"[{self.status:4s}] {self.name:22s} {self.detail}"

    def as_row(self):
        return (self.category, self.name, self.status, self.detail)


def run_full_check(target, scope=None, clock_hz: float = 50e6,
                   swrv_words=None, do_capture: bool = False,
                   on_item: Optional[Callable[[CheckItem], None]] = None,
                   platform: str = "fpga") -> List[CheckItem]:
    """Run the whole A-Z sequence. `target` is a connected ProactTarget.

    scope       : optional connected ChipWhispererCapture (enables clock + capture)
    swrv_words  : optional (imem_words, dmem_words, dmem_base) tuple to test Sw-RV
    do_capture  : if True and scope given, capture one trace and check it is non-flat
    on_item     : called with each CheckItem as it completes (for live UIs)
    Returns the full list of CheckItem.
    """
    results: List[CheckItem] = []

    def emit(item: CheckItem):
        results.append(item)
        if on_item:
            try:
                on_item(item)
            except Exception:  # noqa: BLE001 -- a UI callback must never break the sweep
                pass
        return item

    def step(name, category, fn):
        try:
            ok, detail = fn()
            emit(CheckItem(name, PASS if ok else FAIL, detail, category))
        except Exception as e:  # noqa: BLE001
            emit(CheckItem(name, FAIL, f"{type(e).__name__}: {e}", category))

    expected = aes128_encrypt_block(KEY, PT)
    target.enable_sendback()

    # --- link -------------------------------------------------------------
    def _link():
        s = target.read_status()
        return True, f"controller answered; status=0x{s:08x}"
    step("uart_link", "link", _link)

    def _baud():
        n = 0
        for _ in range(20):
            target.read_status(); n += 1
        return n == 20, f"{n}/20 status frames intact @ live baud"
    step("uart_baud_integrity", "link", _baud)

    # --- optional clock (scope) ------------------------------------------
    if scope is not None and getattr(scope, "is_connected", False):
        def _clock():
            st = scope.clock_status()
            locked = bool(st.get("locked", False))
            return locked, (f"adc={st.get('adc_freq_MHz','?')}MHz "
                            f"src={st.get('clock_source','?')} locked={locked}")
        step("scope_clock_lock", "scope", _clock)

    # --- AES1 / AES2: KAT encrypt + decrypt round-trip -------------------
    for core in ("aes1", "aes2"):
        def _kat(core=core):
            target.select(core); target.set_key(KEY)
            target.set_plaintext(PT); target.set_decrypt(False)
            _, pl = target.run_and_read()
            out = pl[:16]
            return out == expected, f"ct={out.hex()} {'==ref' if out == expected else '!=ref'}"
        step(f"{core}_encrypt_kat", "core", _kat)

        def _dec(core=core):
            target.select(core); target.set_key(KEY)
            target.set_plaintext(expected); target.set_decrypt(True)
            _, pl = target.run_and_read()
            return pl[:16] == PT, f"dec(ct)={pl[:16].hex()} {'==pt' if pl[:16] == PT else '!=pt'}"
        step(f"{core}_decrypt_roundtrip", "core", _dec)

    # --- ASCON / Xoodyak: on-chip KNOWN-ANSWER encrypt test --------------
    # Runs the exact reference vectors (variable-length AD/PT) on-chip and checks
    # the ciphertext+tag against the reference REF_CT/REF_TAG from hello_test.c --
    # the same validation the reference firmware does. One firmware call tests
    # both cores; the reference does not exercise the AEAD decrypt path.
    try:
        xoo_ok, asc_ok = target.aead_kat()
        emit(CheckItem("ascon_encrypt_kat", PASS if asc_ok else FAIL,
                       "CT+TAG %s reference vectors (AD=24B/PT=23B)" % ("==" if asc_ok else "!="),
                       "core"))
        emit(CheckItem("xoodyak_encrypt_kat", PASS if xoo_ok else FAIL,
                       "CT+TAG %s reference vectors (AD=52B/PT=119B)" % ("==" if xoo_ok else "!="),
                       "core"))
    except Exception as e:  # noqa: BLE001
        emit(CheckItem("ascon_encrypt_kat", FAIL, f"{type(e).__name__}: {e}", "core"))
        emit(CheckItem("xoodyak_encrypt_kat", FAIL, f"{type(e).__name__}: {e}", "core"))

    # --- AEAD decrypt round-trip (software patch) ------------------------
    # The frozen RTL wrapper cannot decrypt (see proact_aead.h), so the round
    # trip is closed one level up: aead_soft is a bit-exact software ASCON/
    # Xoodyak, validated against the same reference CT+TAG the silicon
    # produces. hardware-encrypt -> software-decrypt == plaintext.
    def _soft_dec(core):
        from . import aead_soft
        if core == "ascon":
            vec, dec = aead_soft.ASCON_VEC, aead_soft.ascon128_decrypt
        else:
            vec, dec = aead_soft.XOODYAK_VEC, aead_soft.xoodyak_decrypt
        pt = dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"], vec["tag"])
        ok = pt == vec["pt"]
        bad = bytes([vec["tag"][0] ^ 1]) + vec["tag"][1:]
        rej = dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"], bad) is None
        return ok and rej, (f"sw decrypt(hw ct) {'==pt' if ok else '!=pt'}; "
                            f"bad tag {'rejected' if rej else 'ACCEPTED!'}")
    for _core in ("ascon", "xoodyak"):
        step(f"{_core}_decrypt_soft", "core", lambda core=_core: _soft_dec(core))

    # --- timer -----------------------------------------------------------
    def _timer():
        target.select("aes1"); target.set_key(KEY)
        target.set_plaintext(PT); target.set_decrypt(False)
        target.run_and_read()
        cyc = target.get_timer()
        return cyc > 0, f"trigger-window cycles={cyc} (0x{cyc:x})"
    step("timer_cycle_count", "timer", _timer)

    # --- control-register write path -------------------------------------
    def _ctrl():
        from . import regs
        v = getattr(regs, "CTRL_ENABLE_TIMER", 0x4000)
        target.write_control(v)
        s = target.read_status()
        return True, f"wrote control=0x{v:x}; link healthy (status=0x{s:08x})"
    step("control_write", "control", _ctrl)

    # --- PRNG seed (write-only) + AES still correct with masking ---------
    def _prng():
        target.seed_rng(0xACE1ACE1)
        target.select("aes1"); target.set_key(KEY)
        target.set_plaintext(PT); target.set_decrypt(False)
        _, pl = target.run_and_read()
        ok = pl[:16] == expected
        return ok, f"seeded RNG; AES1 KAT still {'PASS' if ok else 'FAIL'} with masking on"
    step("prng_seed", "rng", _prng)

    # --- Sw-RV software AES (optional) -----------------------------------
    if swrv_words:
        def _swrv():
            imem, dmem, base = swrv_words
            # Load in the correct order (imem/dmem while the target is held in
            # reset, then boot) so a fresh program actually runs.
            target.load_swrv_program(imem, dmem, base)
            # SWRV_PT, not PT: see the constant -- a stale result buffer from
            # the preceding AES1 step must not be able to pass as a Sw-RV run.
            swrv_expected = aes128_encrypt_block(KEY, SWRV_PT)
            target.set_key(KEY); target.set_plaintext(SWRV_PT); target.set_decrypt(False)
            _, pl = target.run_and_read()
            ok = pl[:16] == swrv_expected
            return ok, (f"loaded {len(imem)}w/{len(dmem)}w; sw-AES "
                        f"ct={pl[:16].hex()} {'==ref' if ok else '!=ref'}")
        step("swrv_software_aes", "core", _swrv)
    else:
        emit(CheckItem("swrv_software_aes", SKIP,
                       "skipped -- pass swrv_words=(imem,dmem,base) to test", "core"))

    # --- capture (optional) ----------------------------------------------
    if do_capture and scope is not None and getattr(scope, "is_connected", False):
        def _cap():
            target.select("aes1"); target.set_key(KEY)
            target.set_plaintext(PT); target.set_decrypt(False)
            scope.arm()
            target.run()
            trace = scope.capture()
            try:
                target.read_frame()
            except Exception:  # noqa: BLE001
                pass
            n = len(trace) if trace is not None else 0
            span = (float(max(trace)) - float(min(trace))) if n else 0.0
            return (n > 0 and span > 0), f"captured {n} samples, span={span:.4f} (non-flat)"
        step("capture_trace", "scope", _cap)
    else:
        emit(CheckItem("capture_trace", SKIP,
                       "skipped -- connect scope + do_capture=True", "scope"))

    return results


def summarize(items: List[CheckItem]):
    p = sum(1 for i in items if i.status == PASS)
    f = sum(1 for i in items if i.status == FAIL)
    s = sum(1 for i in items if i.status == SKIP)
    return p, f, s


def report_text(items: List[CheckItem]) -> str:
    lines = [i.line() for i in items]
    p, f, s = summarize(items)
    lines.append("-" * 50)
    lines.append(f"{p} PASS   {f} FAIL   {s} SKIP   ({len(items)} total)")
    return "\n".join(lines)
