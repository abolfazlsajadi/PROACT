"""Sw-RV software-AES adapter (L2.4).

The Sw-RV target is the RISC-V (Ibex) core running tiny-AES-c. Unlike the hardware
cores it needs its program (imem/dmem) uploaded once at bring-up. It leaks on the
first-round S-box and recovers the AES key directly (software AES). Its core trigger
is the RISC-V status-register bit (`swrv` cfg source, mux value 5). Auto follows the
selected core, core pins that source, and firmware selects CFGSEL 0.

transport uses core name 'swrv' (config target is 'sw_rv').
"""
from __future__ import annotations
import os, sys
from .base import Target
from ..paths import REPO, SWRV_FW  # noqa: F401
from proact_host.validation import aes128_encrypt_block   # noqa: E402
from proact_host.vmem import parse_vmem                    # noqa: E402



class SwRVTarget(Target):
    core = "swrv"
    out_len = 16

    def _load_program(self):
        imem = [v for _, v in parse_vmem(os.path.join(SWRV_FW, "sw_rv_imem.vmem"))]
        dmem = [v for _, v in parse_vmem(os.path.join(SWRV_FW, "sw_rv_dmem.vmem"))]
        self.t.load_swrv_program(imem, dmem, 0x08100000)

    def select(self, key):
        self.t.select(self.core)
        self.t.set_key(key)
        self._load_program()

    def configure_trigger(self):
        source = {"auto": None, "core": "swrv", "firmware": "software"}[
            self.cfg.trigger_mode]
        self.t.set_cfgsel(source)

    def run(self, key, inp):
        if self.cfg.key_policy == "random":
            self.t.set_key(key)
        self.t.set_plaintext(inp)
        _, pl = self.t.run_and_read()
        return bytes(pl[:16])

    def expected(self, key, inp):
        return aes128_encrypt_block(key, inp)
