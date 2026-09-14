"""AES1 hardware core adapter (L2.2).

Trigger: the AES trigger (Start_AES) is ~119 cy on the frozen RTL and cannot be
narrowed in hardware, so we use the auto cfg mux for reliable sync and rely on
scope-side windowing (cfg.samples / cfg.offset) to keep stored traces short.
"""
from __future__ import annotations
import os, sys
from .base import Target
from ..paths import REPO, SWRV_FW  # noqa: F401
from proact_host.validation import aes128_encrypt_block   # noqa: E402


class AES1Target(Target):
    core = "aes1"
    out_len = 16

    def select(self, key):
        self.t.select(self.core)
        self.t.set_key(key)

    def configure_trigger(self):
        # auto: controller follows the selected core; core: pin the mux here;
        # firmware: select control_reg.trigger (the supported firmware currently
        # does not pulse it, so preflight will refuse a no-trigger campaign).
        source = {"auto": None, "core": self.core, "firmware": "software"}[
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
