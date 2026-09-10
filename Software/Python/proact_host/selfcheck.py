"""
Overall chip self-check. Optionally sets the Husky target clock to 50 MHz on
HS2, then exercises every on-chip target, validates the result, reads the
trigger-window cycle count, and produces a human-readable log report.

Every check carries an explicit verification LABEL so the report never
overstates what was proven:
  RTL-simulated / unit-tested / hardware  (only 'hardware' means a real run).
"""
import time
from typing import List, Optional

from .transport import ProactTarget
from .validation import aes128_encrypt_block, validate_aead

# Canonical demo vectors -- EXACTLY the firmware self-test KAT
# (key = {0xabcdef01,0x12345678,0xdeadbeef,0x87654321},
#  pt  = {0x12345678,0xabcdef01,0x87654321,0xdeadbeef}).
KEY = bytes.fromhex("abcdef0112345678deadbeef87654321")
PT = bytes.fromhex("12345678abcdef0187654321deadbeef")
assert len(KEY) == 16 and len(PT) == 16, "self-check vectors must be 16 bytes"


class CheckResult:
    def __init__(self, name, ok, detail, label="unverified"):
        self.name, self.ok, self.detail, self.label = name, ok, detail, label

    def line(self):
        mark = "PASS" if self.ok else "FAIL" if self.ok is False else "----"
        return f"[{mark}] {self.name:10s} ({self.label}) {self.detail}"


def run_self_check(target: ProactTarget, scope=None, clock_hz: float = 50e6,
                   logfile: Optional[str] = None,
                   swrv_loaded: bool = False) -> List[CheckResult]:
    """Run the full on-chip self-check. `target` must be a connected ProactTarget;
    `scope` (optional) a connected ChipWhispererCapture -> sets 50 MHz on HS2.
    The Sw-RV software-AES leg is skipped unless `swrv_loaded=True` (it first
    needs its .vmem loaded via load_target_imem/dmem, else it cannot answer)."""
    results: List[CheckResult] = []

    if scope is not None and scope.is_connected:
        try:
            scope.set_clock(clock_hz)
            st = scope.clock_status()
            ok = st["clkgen_locked"]
            results.append(CheckResult(
                "clock", ok,
                f"HS2={st['clkgen_freq_MHz']}MHz adc={st['adc_freq_MHz']}MHz locked={ok}",
                label="hardware"))
        except Exception as e:  # noqa: BLE001
            results.append(CheckResult("clock", False, f"error: {e}"))

    target.enable_sendback()

    # AES1 / AES2 (and Sw-RV only if its firmware is loaded): encrypt and
    # validate against the software reference.
    expected = aes128_encrypt_block(KEY, PT)
    aes_cores = ("aes1", "aes2", "swrv") if swrv_loaded else ("aes1", "aes2")
    if not swrv_loaded:
        results.append(CheckResult("swrv", None,
                       "skipped -- load the target .vmem first (swrv_loaded=True)",
                       label="inspected"))
    for core in aes_cores:
        try:
            target.select(core)
            target.set_key(KEY)
            target.set_plaintext(PT)
            target.set_decrypt(False)
            _, payload = target.run_and_read()
            out = payload[:16]
            ok = (out == expected)
            cyc = _read_timer(target)
            results.append(CheckResult(f"{core}_enc", ok,
                           f"ct={out.hex()} {'==ref' if ok else '!=ref '+expected.hex()}"
                           f" cycles={cyc}", label="hardware"))

            # decrypt round-trip (uses the just-captured ciphertext)
            target.set_plaintext(out)
            target.set_decrypt(True)
            _, payload = target.run_and_read()
            dec = payload[:16]
            ok = (dec == PT)
            cyc = _read_timer(target)
            results.append(CheckResult(f"{core}_dec", ok,
                           f"pt={dec.hex()} {'==ref' if ok else '!=ref '+PT.hex()}"
                           f" cycles={cyc}", label="hardware"))
        except Exception as e:  # noqa: BLE001
            results.append(CheckResult(core, False, f"error: {e}"))

    # ASCON / Xoodyak: encrypt (validated vs software) then decrypt round-trip.
    for core in ("ascon", "xoodyak"):
        try:
            target.select(core)
            target.set_key(KEY)
            target.set_nonce(PT)
            target.set_ad(bytes(16))
            target.set_plaintext(PT)
            target.set_decrypt(False)
            _, enc_payload = target.run_and_read()

            # 1) Encrypt validation (vs software reference)
            ok_enc = validate_aead(core, KEY, PT, enc_payload, nonce=PT, ad=bytes(16))
            cyc_enc = _read_timer(target)
            results.append(CheckResult(f"{core}_enc", ok_enc,
                           f"ct={enc_payload.hex()} {'==ref' if ok_enc else '!=ref'}"
                           f" cycles={cyc_enc}", label="hardware"))

            # 2) Decrypt round-trip (hardware only -- known to fail on this silicon)
            ct = enc_payload[:16]
            target.set_plaintext(ct)
            target.set_decrypt(True)
            _, dec_payload = target.run_and_read()
            dec = dec_payload[:16]
            ok_dec = (dec == PT)
            cyc_dec = _read_timer(target)
            detail = f"pt={dec.hex()} {'==ref' if ok_dec else '!=ref '+PT.hex()}"
            if not ok_dec:
                detail += " (note: hardware AEAD is encrypt-only)"
            results.append(CheckResult(f"{core}_dec", ok_dec,
                           f"{detail} cycles={cyc_dec}", label="hardware"))
        except Exception as e:  # noqa: BLE001
            results.append(CheckResult(core, False, f"error: {e}"))

    if logfile:
        _write_log(results, logfile)
    return results


def _read_timer(target: ProactTarget) -> str:
    try:
        return "0x%08X" % target.get_timer()
    except Exception:  # noqa: BLE001
        return "n/a"


def _write_log(results: List[CheckResult], path: str):
    with open(path, "w") as f:
        f.write("PROACT self-check report  %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("=" * 60 + "\n")
        for r in results:
            f.write(r.line() + "\n")
        n_ok = sum(1 for r in results if r.ok)
        f.write("-" * 60 + "\n")
        f.write("%d/%d checks passed\n" % (n_ok, len(results)))


def report_text(results: List[CheckResult]) -> str:
    lines = [r.line() for r in results]
    n_ok = sum(1 for r in results if r.ok)
    lines.append("%d/%d checks passed" % (n_ok, len(results)))
    return "\n".join(lines)
