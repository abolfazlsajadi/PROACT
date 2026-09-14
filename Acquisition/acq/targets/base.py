"""Target adapter base (L2.1).

A Target isolates everything that differs between cores: bring-up, core select,
loading key+input, running one operation, computing the expected output, and the
trigger strategy. The run loop and every common module are core-agnostic.

Trigger strategy per core lives in `configure_trigger()`:
  * AES1/AES2/Sw-RV : auto follows the selected core, core pins that source, and
    firmware selects the controller software-trigger input (CFGSEL 0).  Their wide
    core triggers can be scope-windowed via cfg.offset/cfg.samples.
  * Xoodyak/ASCON   : configurable in-core triggercfg (cfg.aead_trigger).
"""
from __future__ import annotations
import os, sys, time
from ..paths import CONTROLLER_VMEM  # noqa: F401
from proact_host.transport import UartTransport, ProactTarget   # noqa: E402


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


class Target:
    core = None            # transport core name
    out_len = 16           # bytes returned (16 AES, 32 AEAD)

    def __init__(self, cfg):
        self.cfg = cfg
        self.u = None
        self.t = None

    # --- link -------------------------------------------------------------
    def open(self):
        self.u = UartTransport(port=self.cfg.port or None,
                               baud=self.cfg.uart_host_baud).open()
        self.t = ProactTarget(self.u)
        self.t.enable_sendback()

    def close(self):
        try:
            self.u.close()
        except Exception:
            pass

    def reprogram(self):
        from proact_host.programmer import Mcp2210Programmer
        _log("RECOVERY: reprogramming controller over SPI")
        p = Mcp2210Programmer().open()
        p.program(CONTROLLER_VMEM)
        p.restart_controller()
        time.sleep(1.0)

    # --- per-core hooks (override) ---------------------------------------
    def select(self, key: bytes):
        """Select the core and load the fixed-for-this-key state."""
        raise NotImplementedError

    def configure_trigger(self):
        """Point the trigger at this core / set the trigger config."""
        raise NotImplementedError

    def run(self, key: bytes, inp: bytes) -> bytes:
        """Load key+input, run one op, return the chip output bytes."""
        raise NotImplementedError

    def expected(self, key: bytes, inp: bytes) -> bytes:
        """Software reference output for verification."""
        raise NotImplementedError

    # --- bring-up + verify ------------------------------------------------
    def bringup_and_verify(self, key: bytes) -> None:
        """Open link, select core, verify a KAT; reprogram once on failure."""
        def ok():
            try:
                self.select(key)
                self.configure_trigger()
                inp = self.cfg.fixed_input
                return self.run(key, inp) == self.expected(key, inp)
            except Exception as e:
                _log(f"  verify raised {type(e).__name__}: {str(e)[:70]}")
                return False
        if not ok():
            _log(f"{self.core}: not responding -> reprogramming")
            self.reprogram()
            self.close()
            self.open()
            if not ok():
                raise RuntimeError(f"{self.core} wrong output even after reprogram")
        _log(f"{self.core}: verified against software reference")
