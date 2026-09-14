"""Per-trace key / input generation (L1.7).

Supports the full policy matrix (fixed|random key x fixed|random input) for both the
AES cores (input = plaintext) and the AEAD cores (input = nonce; AD and PT stay
fixed all-zero). The RNG is seeded and its stream is reproducible: storing the seed
plus the trace index lets any trace's inputs be regenerated for validation.

The generator is DETERMINISTIC in the trace index, which is what makes resume safe:
after a restart, regenerating from the same seed reproduces exactly the same
key/input sequence, so trace i always has the inputs it had before.
"""
from __future__ import annotations
import numpy as np


class InputGen:
    def __init__(self, cfg):
        self.cfg = cfg
        # a per-campaign 64-bit seed, recorded in metadata for reproducibility
        if cfg.seed:
            self.seed = int(cfg.seed) & ((1 << 64) - 1)
        else:
            self.seed = int.from_bytes(np.random.SeedSequence().generate_state(2).tobytes()[:8], "little")
        self.fixed_key = bytes(cfg.fixed_key)
        self.fixed_input = bytes(cfg.fixed_input)
        self._tvla_cached_block = None
        self._tvla_cached_labels = None

    def _rng(self, i):
        # independent, index-addressable stream: SeedSequence(seed, i) so trace i is
        # reproducible regardless of order or resume point.
        return np.random.default_rng(np.random.SeedSequence([self.seed, i]))

    def key(self, i) -> bytes:
        if self.cfg.key_policy == "fixed":
            return self.fixed_key
        return self._rng(i).integers(0, 256, 16, dtype=np.uint8).tobytes()

    @property
    def is_tvla(self) -> bool:
        return bool(getattr(self.cfg, "tvla", False))

    def random_input(self, i) -> bytes:
        """Return the deterministic random input assigned to index ``i``."""
        # Offset the substream so a random key and input never share bytes.
        return self._rng(int(i) ^ (1 << 40)).integers(
            0, 256, 16, dtype=np.uint8).tobytes()

    def tvla_group(self, i) -> int:
        """Return 0=fixed or 1=random for stored TVLA row ``i``.

        ``alternate`` implements the explicitly requested F,R,F,R order.  It is
        available for controlled comparisons, but is not the default because an
        odd/even acquisition artefact then aliases perfectly into the TVLA group.

        ``block`` creates deterministic, shuffled, exactly balanced blocks.  The
        final partial block is balanced too, so every completed campaign contains
        exactly ``tvla_per_class`` rows in each group for any requested count.
        """
        i = int(i)
        if self.cfg.tvla_order == "alternate":
            return i & 1
        size = int(self.cfg.tvla_block_size)
        block, pos = divmod(i, size)
        start = block * size
        length = min(size, int(self.cfg.traces) - start)
        if length <= 0 or length % 2:
            raise ValueError("TVLA blocks must contain a positive even row count")
        if self._tvla_cached_block != block:
            labels = np.zeros(length, dtype=np.uint8)
            labels[length // 2:] = 1
            np.random.default_rng(
                np.random.SeedSequence([self.seed, 0xB10C, block])).shuffle(labels)
            self._tvla_cached_block = block
            self._tvla_cached_labels = labels
        return int(self._tvla_cached_labels[pos])

    def tvla_block(self, i) -> int:
        if self.cfg.tvla_order == "alternate":
            return int(i) // 2
        return int(i) // int(self.cfg.tvla_block_size)

    def input_for_group(self, i, group: int) -> bytes:
        """Build a fixed/random input explicitly (used by deterministic preflight)."""
        if group == 0:
            return self.fixed_input
        if group == 1:
            return self.random_input(i)
        raise ValueError("TVLA group must be 0 (fixed) or 1 (random)")

    def inp(self, i) -> bytes:
        if self.is_tvla:
            return self.input_for_group(i, self.tvla_group(i))
        if self.cfg.input_policy == "fixed":
            return self.fixed_input
        return self.random_input(i)

    @property
    def key_varies(self) -> bool:
        return self.cfg.key_policy == "random"
