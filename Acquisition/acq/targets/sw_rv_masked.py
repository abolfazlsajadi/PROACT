"""Masked software-AES adapter for the Sw-RV (Ibex) target.

This is deliberately a separate acquisition target from ``sw_rv``. Its pinned
external imem/dmem pair reads six mask bytes from the on-chip RNG for every AES
operation.  The RNG is held in reset until the controller's seed command sets
``CTRL_ENABLE_RNG``, so it must be enabled before the program boots.  A new
nonzero 32-bit seed from the host OS CSPRNG is then sent before every operation.

The trigger and mailbox protocol are the same as the unmasked Sw-RV program.
"""
from __future__ import annotations

import os
import hashlib
import secrets

from .sw_rv import SwRVTarget
from ..paths import SWRV_MASKED_FW
from proact_host.vmem import parse_vmem


class MaskedSwRVTarget(SwRVTarget):
    """Sw-RV running the pinned external RNG-masked AES firmware."""

    firmware_dir = SWRV_MASKED_FW
    rng_reseed_policy = "host_os_csprng_nonzero_u32_before_every_operation"
    firmware_sha256 = {
        "sw_rv_imem.vmem":
            "a11776046589d0af6877e874551c9df61c14ef2896512def91195e299317125d",
        "sw_rv_dmem.vmem":
            "971f306c720a96e1603efc98f5f7271a1bec052fc3e57d0ba7ab25e008d1835e",
    }

    def __init__(self, cfg):
        super().__init__(cfg)
        self._verify_firmware()

    def _verify_firmware(self) -> None:
        """Refuse a missing or changed masked image before opening hardware."""
        for name, expected in self.firmware_sha256.items():
            path = os.path.join(self.firmware_dir, name)
            try:
                with open(path, "rb") as stream:
                    actual = hashlib.sha256(stream.read()).hexdigest()
            except OSError as exc:
                raise RuntimeError(f"masked Sw-RV firmware unavailable: {path}") from exc
            if actual != expected:
                raise RuntimeError(
                    f"masked Sw-RV firmware identity mismatch for {path}: "
                    f"expected {expected}, got {actual}")

    @staticmethod
    def _fresh_rng_seed() -> int:
        # Zero is a poor LFSR seed.  Mapping the vanishingly unlikely zero draw
        # to one also makes the contract simple for transports and tests.
        return int(secrets.randbits(32)) or 1

    def _seed_rng(self) -> None:
        self.t.seed_rng(self._fresh_rng_seed())

    def _load_program(self):
        imem_path = os.path.join(self.firmware_dir, "sw_rv_imem.vmem")
        dmem_path = os.path.join(self.firmware_dir, "sw_rv_dmem.vmem")
        imem = [value for _, value in parse_vmem(imem_path)]
        dmem = [value for _, value in parse_vmem(dmem_path)]
        self.t.load_swrv_program(imem, dmem, 0x08100000)

    def select(self, key):
        # Enable and seed the target RNG before boot.  Without this, the first
        # firmware read from 0x40000000 can wait forever for RNG-valid.
        self.t.select(self.core)
        self.t.set_decrypt(False)
        self.t.set_key(key)
        self._seed_rng()
        self._load_program()

    def run(self, key, inp):
        # Reseed each attempted AES operation, including KATs, preflight,
        # warm-up, retries and stored captures.  Fresh masks are therefore not
        # reused for repeated fixed-input TVLA rows.
        self._seed_rng()
        return super().run(key, inp)
