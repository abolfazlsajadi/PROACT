"""AEAD adapter base for Xoodyak / ASCON (L2.5, L2.6).

The AEAD cores encrypt as ct = pt XOR keystream; the key is mixed with the NONCE in
the first permutation, so the acquisition varies the NONCE (fixed nonce => constant
keystream => unattackable). AD and PT stay fixed all-zero. Output is CT||TAG (32 B).

Trigger: these cores have a CONFIGURABLE in-core trigger. `cfg.aead_trigger` (7-bit
triggercfg) picks the phase window via t.set_trigger_cfg(); default 0x12 (nonce->done),
0x11 = key->nonce (~10 cy, brackets the key mixing). This is the narrow trigger the
board's cfg jumper exposes.
"""
from __future__ import annotations
import os, sys
from .base import Target
from ..paths import REPO, SWRV_FW  # noqa: F401
from proact_host import aead_soft   # noqa: E402

AD_FIXED = b"\x00" * 16
PT_FIXED = b"\x00" * 16


class AEADTarget(Target):
    out_len = 32
    soft_enc = None       # set in subclass

    def select(self, key):
        self.t.select(self.core)
        self.t.set_cfgsel(self.core)      # point cfg mux at this AEAD core
        self.t.set_decrypt(False)         # silicon is encrypt-only
        self.t.set_key(key)
        self.t.set_ad(AD_FIXED)
        self.t.set_plaintext(PT_FIXED)

    def configure_trigger(self):
        # AcqConfig validates the 7-bit register range.  Do not silently mask a
        # typo into a different trigger window than the one stored in metadata.
        self.t.set_trigger_cfg(int(self.cfg.aead_trigger))

    def run(self, key, inp):
        # `inp` is the NONCE (the varying input for AEAD)
        if self.cfg.key_policy == "random":
            self.t.set_key(key)
        self.t.set_nonce(inp)
        _, pl = self.t.run_and_read()
        return bytes(pl)[:self.out_len]

    def expected(self, key, inp):
        ct, tag = self.soft_enc(key, inp, AD_FIXED, PT_FIXED)
        return bytes(ct) + bytes(tag)


class XoodyakTarget(AEADTarget):
    core = "xoodyak"
    soft_enc = staticmethod(aead_soft.xoodyak_encrypt)


class AsconTarget(AEADTarget):
    core = "ascon"
    soft_enc = staticmethod(aead_soft.ascon128_encrypt)
