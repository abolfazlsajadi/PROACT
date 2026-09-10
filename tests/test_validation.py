"""
Regression tests for proact_host.validation.

Why this module matters: every PASS/FAIL verdict that the CLI, the GUI,
`experiment.py` and `fullcheck.py` print about the silicon comes out of
`validate_aes()` / `validate_aead()` and the dependency-free AES-128 reference
underneath them. A silent bug here is either a fake PASS on a broken chip or a
fake FAIL on a good one, and neither is recoverable after the fact.

Ground truth is deliberately EXTERNAL, not self-referential:

  * AES-128 ECB      -- FIPS-197 Appendix B/C.1 and NIST SP 800-38A section
                        F.1.1 (ECB-AES128.Encrypt), plus the FIPS-197
                        Appendix A.1 key schedule and the section 4.2 finite
                        field examples. Hex literals below are the published
                        values, not values scraped from this implementation.
  * ASCON / Xoodyak  -- the on-chip KAT vectors shipped in
                        proact_host.aead_soft (ASCON_VEC / XOODYAK_VEC), which
                        are the same vectors baked into proact_aead_kat.c.

Offline only: pure computation, no serial port, no board, no network.
"""
import pytest

from proact_host import aead_soft, validation
from proact_host.validation import (
    _INV_SBOX,
    _RCON,
    _SBOX,
    _expand_key,
    _init_sbox,
    _mul,
    _xtime,
    aes128_encrypt_block,
    validate_aead,
    validate_aes,
)

H = bytes.fromhex

# --------------------------------------------------------------------------
# Published AES-128 ECB test vectors
# --------------------------------------------------------------------------

# FIPS-197, Appendix B ("Cipher Example") / C.1 ("AES-128 (Nk=4, Nr=10)").
FIPS197_KEY = H("000102030405060708090a0b0c0d0e0f")
FIPS197_PT = H("00112233445566778899aabbccddeeff")
FIPS197_CT = H("69c4e0d86a7b0430d8cdb78070b4c55a")

# NIST SP 800-38A, F.1.1 ECB-AES128.Encrypt (the four standard blocks).
SP38A_KEY = H("2b7e151628aed2a6abf7158809cf4f3c")
SP38A_BLOCKS = [
    ("6bc1bee22e409f96e93d7e117393172a", "3ad77bb40d7a3660a89ecaf32466ef97"),
    ("ae2d8a571e03ac9c9eb76fac45af8e51", "f5d3d58503b9699de785895a96fdbaaf"),
    ("30c81c46a35ce411e5fbc1191a0a52ef", "43b1cd7f598ece23881b00e3ed030688"),
    ("f69f2445df4f9b17ad2b417be66c3710", "7b0c785e27e8ad3f8223207104725dd4"),
]

# AES-128 of an all-zero block under an all-zero key -- the most widely quoted
# AES sanity value (it is also the AES-CMAC subkey-generation constant L).
ZERO_KEY_ZERO_BLOCK_CT = H("66e94bd4ef8a2c3b884cfa59ca342b2e")


class TestAesReferenceVectors:
    def test_fips197_appendix_c1(self):
        assert aes128_encrypt_block(FIPS197_KEY, FIPS197_PT) == FIPS197_CT

    @pytest.mark.parametrize("pt_hex,ct_hex", SP38A_BLOCKS)
    def test_sp800_38a_ecb_aes128(self, pt_hex, ct_hex):
        assert aes128_encrypt_block(SP38A_KEY, H(pt_hex)) == H(ct_hex)

    def test_all_zero_key_and_block(self):
        assert aes128_encrypt_block(bytes(16), bytes(16)) == ZERO_KEY_ZERO_BLOCK_CT

    def test_output_is_sixteen_bytes(self):
        out = aes128_encrypt_block(FIPS197_KEY, FIPS197_PT)
        assert isinstance(out, bytes) and len(out) == 16

    def test_deterministic_across_calls(self):
        a = aes128_encrypt_block(SP38A_KEY, H(SP38A_BLOCKS[0][0]))
        b = aes128_encrypt_block(SP38A_KEY, H(SP38A_BLOCKS[0][0]))
        assert a == b

    def test_does_not_mutate_its_inputs(self):
        key = bytearray(FIPS197_KEY)
        blk = bytearray(FIPS197_PT)
        aes128_encrypt_block(bytes(key), bytes(blk))
        assert bytes(key) == FIPS197_KEY and bytes(blk) == FIPS197_PT

    def test_single_plaintext_bit_flip_avalanches(self):
        """A one-bit input change must scramble the whole block.

        This is what catches a reference that silently degenerates (e.g. a
        MixColumns that is accidentally the identity): such a bug can still
        reproduce one stored vector but never survives avalanche.
        """
        base = aes128_encrypt_block(FIPS197_KEY, FIPS197_PT)
        flipped = bytearray(FIPS197_PT)
        flipped[0] ^= 0x01
        other = aes128_encrypt_block(FIPS197_KEY, bytes(flipped))
        differing = sum(1 for x, y in zip(base, other) if x != y)
        assert differing >= 14, f"only {differing}/16 bytes changed"

    def test_single_key_bit_flip_avalanches(self):
        base = aes128_encrypt_block(FIPS197_KEY, FIPS197_PT)
        flipped = bytearray(FIPS197_KEY)
        flipped[15] ^= 0x80
        other = aes128_encrypt_block(bytes(flipped), FIPS197_PT)
        differing = sum(1 for x, y in zip(base, other) if x != y)
        assert differing >= 14, f"only {differing}/16 bytes changed"

    def test_distinct_plaintexts_give_distinct_ciphertexts(self):
        seen = {}
        for i in range(64):
            pt = bytes([i]) + bytes(15)
            ct = aes128_encrypt_block(SP38A_KEY, pt)
            assert ct not in seen, f"collision between block {seen.get(ct)} and {i}"
            seen[ct] = i

    def test_only_first_sixteen_key_bytes_are_used(self):
        """Pinned quirk: _expand_key slices key[0:16], so a longer buffer is
        silently truncated rather than rejected. Callers pass exactly 16."""
        long_key = FIPS197_KEY + b"\xff" * 16
        assert aes128_encrypt_block(long_key, FIPS197_PT) == FIPS197_CT

    def test_short_key_raises_rather_than_producing_junk(self):
        with pytest.raises(IndexError):
            aes128_encrypt_block(FIPS197_KEY[:8], FIPS197_PT)

    def test_short_block_raises_rather_than_producing_junk(self):
        with pytest.raises(IndexError):
            aes128_encrypt_block(FIPS197_KEY, FIPS197_PT[:8])


# --------------------------------------------------------------------------
# S-box construction (computed at import time -- worth pinning)
# --------------------------------------------------------------------------

# FIPS-197 Figure 7 spot values (row/col addressing: _SBOX[0xXY]).
SBOX_SPOT_VALUES = {
    0x00: 0x63,
    0x01: 0x7C,
    0x0F: 0x76,
    0x10: 0xCA,
    0x53: 0xED,
    0x7A: 0xDA,
    0xC6: 0xB4,
    0xF0: 0x8C,
    0xFF: 0x16,
}


class TestSbox:
    def test_sbox_has_256_entries(self):
        assert len(_SBOX) == 256 and len(_INV_SBOX) == 256

    def test_sbox_is_a_permutation_of_0_255(self):
        assert sorted(_SBOX) == list(range(256))

    def test_all_entries_are_bytes(self):
        assert all(isinstance(v, int) and 0 <= v <= 0xFF for v in _SBOX)

    @pytest.mark.parametrize("idx,expected", sorted(SBOX_SPOT_VALUES.items()))
    def test_sbox_published_entries(self, idx, expected):
        assert _SBOX[idx] == expected

    def test_inv_sbox_is_the_exact_inverse(self):
        for i in range(256):
            assert _INV_SBOX[_SBOX[i]] == i
            assert _SBOX[_INV_SBOX[i]] == i

    def test_inv_sbox_is_also_a_permutation(self):
        assert sorted(_INV_SBOX) == list(range(256))

    def test_sbox_has_no_fixed_points(self):
        """The AES S-box was designed with S(x) != x for every x."""
        assert [i for i in range(256) if _SBOX[i] == i] == []

    def test_sbox_has_no_opposite_fixed_points(self):
        """...and S(x) != x XOR 0xFF for every x."""
        assert [i for i in range(256) if _SBOX[i] == i ^ 0xFF] == []

    def test_init_sbox_is_deterministic(self):
        """Recomputing must reproduce the module-level tables exactly; the
        import-time values are not allowed to depend on call order or state."""
        sbox, inv = _init_sbox()
        assert sbox == _SBOX
        assert inv == _INV_SBOX
        again, _ = _init_sbox()
        assert again == sbox

    def test_init_sbox_returns_fresh_lists(self):
        sbox, inv = _init_sbox()
        assert sbox is not _SBOX and inv is not _INV_SBOX


# --------------------------------------------------------------------------
# GF(2^8) arithmetic
# --------------------------------------------------------------------------


class TestGaloisField:
    # FIPS-197 section 4.1: repeated xtime() of 0x57.
    @pytest.mark.parametrize("a,expected", [
        (0x57, 0xAE),
        (0xAE, 0x47),
        (0x47, 0x8E),
        (0x8E, 0x07),
        (0x00, 0x00),
        (0x01, 0x02),
        (0x80, 0x1B),
    ])
    def test_xtime_published(self, a, expected):
        assert _xtime(a) == expected

    def test_xtime_stays_in_byte_range(self):
        assert all(0 <= _xtime(a) <= 0xFF for a in range(256))

    def test_xtime_equals_multiply_by_two(self):
        assert all(_xtime(a) == _mul(a, 2) for a in range(256))

    # FIPS-197 section 4.2 worked example and its 4.2.1 companion.
    @pytest.mark.parametrize("a,b,expected", [
        (0x57, 0x83, 0xC1),
        (0x57, 0x13, 0xFE),
        (0x53, 0xCA, 0x01),   # 0x53 and 0xCA are multiplicative inverses
        (0x02, 0x87, 0x15),
    ])
    def test_mul_published(self, a, b, expected):
        assert _mul(a, b) == expected

    def test_mul_identity_and_zero(self):
        for a in range(256):
            assert _mul(a, 1) == a
            assert _mul(a, 0) == 0
            assert _mul(0, a) == 0

    def test_mul_is_commutative(self):
        for a in (0x00, 0x01, 0x02, 0x03, 0x57, 0x83, 0xC1, 0xFF):
            for b in (0x00, 0x01, 0x02, 0x03, 0x53, 0xCA, 0x8E, 0xFF):
                assert _mul(a, b) == _mul(b, a)

    def test_mul_distributes_over_xor(self):
        for a in (0x01, 0x02, 0x03, 0x57, 0xC1, 0xFF):
            for b in (0x02, 0x03, 0x53, 0x8E):
                for c in (0x01, 0x0B, 0x7A, 0xFF):
                    assert _mul(a, b ^ c) == (_mul(a, b) ^ _mul(a, c))

    def test_mul_stays_in_byte_range(self):
        assert all(0 <= _mul(a, 0x03) <= 0xFF for a in range(256))

    def test_rcon_is_the_published_sequence(self):
        assert _RCON == [0x01, 0x02, 0x04, 0x08, 0x10,
                         0x20, 0x40, 0x80, 0x1B, 0x36]

    def test_rcon_follows_xtime_recurrence(self):
        for i in range(1, len(_RCON)):
            assert _RCON[i] == _xtime(_RCON[i - 1])


# --------------------------------------------------------------------------
# Key expansion (FIPS-197 Appendix A.1)
# --------------------------------------------------------------------------

# Appendix A.1 expands 2b7e1516 28aed2a6 abf71588 09cf4f3c; these are the
# published words at the round-key boundaries.
A1_EXPECTED_WORDS = {
    0: "2b7e1516",
    1: "28aed2a6",
    2: "abf71588",
    3: "09cf4f3c",
    4: "a0fafe17",
    5: "88542cb1",
    6: "23a33939",
    7: "2a6c7605",
    8: "f2c295f2",
    12: "3d80477d",
    16: "ef44a541",
    20: "d4d1c6f8",
    24: "6d88a37a",
    28: "4e54f70e",
    32: "ead27321",
    36: "ac7766f3",
    40: "d014f9a8",
    41: "c9ee2589",
    42: "e13f0cc8",
    43: "b6630ca6",
}


class TestKeyExpansion:
    def test_produces_44_words_of_4_bytes(self):
        words = _expand_key(SP38A_KEY)
        assert len(words) == 44
        assert all(len(w) == 4 for w in words)
        assert all(0 <= b <= 0xFF for w in words for b in w)

    def test_first_four_words_are_the_raw_key(self):
        words = _expand_key(FIPS197_KEY)
        assert bytes(b for w in words[:4] for b in w) == FIPS197_KEY

    @pytest.mark.parametrize("idx,hex_word", sorted(A1_EXPECTED_WORDS.items()))
    def test_appendix_a1_words(self, idx, hex_word):
        assert bytes(_expand_key(SP38A_KEY)[idx]) == H(hex_word)

    def test_recurrence_holds_for_non_multiple_of_four(self):
        words = _expand_key(FIPS197_KEY)
        for i in range(4, 44):
            if i % 4:
                assert words[i] == [words[i - 4][j] ^ words[i - 1][j]
                                    for j in range(4)]

    def test_expansion_is_deterministic(self):
        assert _expand_key(SP38A_KEY) == _expand_key(SP38A_KEY)

    def test_distinct_keys_give_distinct_schedules(self):
        assert _expand_key(SP38A_KEY) != _expand_key(FIPS197_KEY)

    def test_short_key_raises(self):
        with pytest.raises(IndexError):
            _expand_key(b"\x00" * 8)


# --------------------------------------------------------------------------
# validate_aes()
# --------------------------------------------------------------------------


class TestValidateAes:
    def test_true_on_correct_chip_output(self):
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT) is True

    @pytest.mark.parametrize("pt_hex,ct_hex", SP38A_BLOCKS)
    def test_true_on_every_sp800_38a_block(self, pt_hex, ct_hex):
        assert validate_aes(SP38A_KEY, H(pt_hex), H(ct_hex)) is True

    @pytest.mark.parametrize("pos", list(range(16)))
    def test_false_when_any_single_output_byte_is_corrupted(self, pos):
        bad = bytearray(FIPS197_CT)
        bad[pos] ^= 0x01
        assert validate_aes(FIPS197_KEY, FIPS197_PT, bytes(bad)) is False

    def test_false_on_wrong_key(self):
        assert validate_aes(SP38A_KEY, FIPS197_PT, FIPS197_CT) is False

    def test_false_on_wrong_input_block(self):
        assert validate_aes(FIPS197_KEY, bytes(16), FIPS197_CT) is False

    def test_false_on_all_zero_chip_output(self):
        """A dead/timed-out core reads back zeros; that must never PASS."""
        assert validate_aes(FIPS197_KEY, FIPS197_PT, bytes(16)) is False

    def test_false_on_echoed_plaintext(self):
        """A core stuck in bypass echoes the plaintext; must never PASS."""
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_PT) is False

    def test_false_on_truncated_chip_output(self):
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT[:8]) is False

    def test_false_on_overlong_chip_output(self):
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT + b"\x00") is False

    def test_false_on_short_key_rather_than_indexerror(self):
        # A truncated key read-back must FAIL, not crash the compare loop.
        assert validate_aes(b"\x00" * 8, FIPS197_PT, FIPS197_CT) is False
        assert validate_aes(b"\x00" * 8, FIPS197_CT, FIPS197_PT, decrypt=True) is False

    def test_decrypt_inverts_the_comparison_direction(self):
        """decrypt=True: chip_output is the recovered plaintext, so the check
        is encrypt(chip_output) == input_block (the ciphertext fed in)."""
        assert validate_aes(FIPS197_KEY, FIPS197_CT, FIPS197_PT,
                            decrypt=True) is True

    @pytest.mark.parametrize("pt_hex,ct_hex", SP38A_BLOCKS)
    def test_decrypt_true_on_every_sp800_38a_block(self, pt_hex, ct_hex):
        assert validate_aes(SP38A_KEY, H(ct_hex), H(pt_hex),
                            decrypt=True) is True

    def test_decrypt_false_on_wrong_recovered_plaintext(self):
        bad = bytearray(FIPS197_PT)
        bad[7] ^= 0x40
        assert validate_aes(FIPS197_KEY, FIPS197_CT, bytes(bad),
                            decrypt=True) is False

    def test_decrypt_false_on_all_zero_recovered_plaintext(self):
        assert validate_aes(FIPS197_KEY, FIPS197_CT, bytes(16),
                            decrypt=True) is False

    def test_encrypt_and_decrypt_directions_are_not_interchangeable(self):
        """Guards against a refactor that drops the `decrypt` branch: the
        arguments that pass one way must fail the other way."""
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT,
                            decrypt=True) is False
        assert validate_aes(FIPS197_KEY, FIPS197_CT, FIPS197_PT) is False

    def test_returns_a_real_bool(self):
        assert type(validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT)) is bool

    def test_decrypt_defaults_to_false(self):
        assert (validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT)
                == validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT,
                                decrypt=False))

    @pytest.mark.parametrize("n", [0, 1, 8, 15, 17, 32])
    def test_both_directions_agree_on_a_malformed_chip_output(self, n):
        """cli.py hands validate_aes payload[:16] whichever way the core is
        running, so a short/long read-back must be a verdict, not a crash."""
        out = (FIPS197_CT + FIPS197_CT)[:n]
        assert validate_aes(FIPS197_KEY, FIPS197_PT, out) is False
        assert validate_aes(FIPS197_KEY, FIPS197_PT, out, decrypt=True) is False

    def test_decrypt_should_report_false_on_truncated_output(self):
        assert validate_aes(FIPS197_KEY, FIPS197_PT, FIPS197_CT[:8],
                            decrypt=True) is False


# --------------------------------------------------------------------------
# validate_aead()
# --------------------------------------------------------------------------

AEAD_VECTORS = {
    "ascon": aead_soft.ASCON_VEC,
    "xoodyak": aead_soft.XOODYAK_VEC,
}

# validate_aead() substitutes 16 ZERO BYTES (not b"") for a missing nonce/ad.
# These are the resulting CT||TAG for key=000102..0f, pt=001122..ff; they pin
# the default down to the byte so a change to "" or None cannot slip through.
DEFAULT_PARAM_KEY = FIPS197_KEY
DEFAULT_PARAM_PT = FIPS197_PT
DEFAULT_PARAM_OUTPUT = {
    "ascon": H("42c56069815729c733779f2cb2d5f197"
               "fcd1d33e671cf8973c12895785a720f3"),
    "xoodyak": H("c57d2b06dabeab2d613f5b508fc83f8b"
                 "a276a8cb3b08ed817679b13630fef528"),
}
# Same key/pt/nonce but with genuinely EMPTY associated data -- different tag.
EMPTY_AD_OUTPUT = {
    "ascon": H("e7549efffdddbd29b82cc535e0263218"
               "91433ca70b3b2e68bac83c2d06b79617"),
    "xoodyak": H("ace3a7ba3b7e9d0cd81902e81a29dbac"
                 "c39fb5d14f81b41405c23518f5f6fc1c"),
}

ALL_CORES = ["ascon", "xoodyak"]


def _vec_output(core):
    v = AEAD_VECTORS[core]
    return v["ct"] + v["tag"]


class TestValidateAead:
    @pytest.mark.parametrize("core", ALL_CORES)
    def test_true_on_the_on_chip_kat_vector(self, core):
        v = AEAD_VECTORS[core]
        assert validate_aead(core, v["key"], v["pt"], _vec_output(core),
                             nonce=v["nonce"], ad=v["ad"]) is True

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_when_the_tag_is_corrupted(self, core):
        v = AEAD_VECTORS[core]
        bad_tag = bytes([v["tag"][0] ^ 0x01]) + v["tag"][1:]
        assert validate_aead(core, v["key"], v["pt"], v["ct"] + bad_tag,
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_when_the_last_tag_bit_is_corrupted(self, core):
        v = AEAD_VECTORS[core]
        bad_tag = v["tag"][:-1] + bytes([v["tag"][-1] ^ 0x80])
        assert validate_aead(core, v["key"], v["pt"], v["ct"] + bad_tag,
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_when_the_ciphertext_is_corrupted(self, core):
        v = AEAD_VECTORS[core]
        bad_ct = bytes([v["ct"][0] ^ 0x01]) + v["ct"][1:]
        assert validate_aead(core, v["key"], v["pt"], bad_ct + v["tag"],
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_when_the_tag_is_missing(self, core):
        v = AEAD_VECTORS[core]
        assert validate_aead(core, v["key"], v["pt"], v["ct"],
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_on_all_zero_chip_output(self, core):
        """A timed-out AEAD core reads back zeros; that must never PASS."""
        v = AEAD_VECTORS[core]
        zeros = bytes(len(v["ct"]) + len(v["tag"]))
        assert validate_aead(core, v["key"], v["pt"], zeros,
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_on_wrong_key(self, core):
        v = AEAD_VECTORS[core]
        wrong = bytes([v["key"][0] ^ 0xFF]) + v["key"][1:]
        assert validate_aead(core, wrong, v["pt"], _vec_output(core),
                             nonce=v["nonce"], ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_on_wrong_nonce(self, core):
        v = AEAD_VECTORS[core]
        wrong = bytes([v["nonce"][0] ^ 0xFF]) + v["nonce"][1:]
        assert validate_aead(core, v["key"], v["pt"], _vec_output(core),
                             nonce=wrong, ad=v["ad"]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_on_wrong_associated_data(self, core):
        v = AEAD_VECTORS[core]
        wrong = bytes([v["ad"][0] ^ 0xFF]) + v["ad"][1:]
        assert validate_aead(core, v["key"], v["pt"], _vec_output(core),
                             nonce=v["nonce"], ad=wrong) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_false_on_wrong_plaintext(self, core):
        v = AEAD_VECTORS[core]
        wrong = bytes([v["pt"][0] ^ 0xFF]) + v["pt"][1:]
        assert validate_aead(core, v["key"], wrong, _vec_output(core),
                             nonce=v["nonce"], ad=v["ad"]) is False

    def test_cores_are_not_interchangeable(self):
        """An ASCON result must not validate as Xoodyak, or the CLI would pass
        a chip whose core-select mux is broken."""
        a, x = AEAD_VECTORS["ascon"], AEAD_VECTORS["xoodyak"]
        assert validate_aead("xoodyak", a["key"], a["pt"], _vec_output("ascon"),
                             nonce=a["nonce"], ad=a["ad"]) is False
        assert validate_aead("ascon", x["key"], x["pt"], _vec_output("xoodyak"),
                             nonce=x["nonce"], ad=x["ad"]) is False

    @pytest.mark.parametrize("name", ["ASCON", "Ascon", "XOODYAK", "XooDyak"])
    def test_target_name_is_case_insensitive(self, name):
        v = AEAD_VECTORS[name.lower()]
        assert validate_aead(name, v["key"], v["pt"], _vec_output(name.lower()),
                             nonce=v["nonce"], ad=v["ad"]) is True

    @pytest.mark.parametrize("name", ["des", "aes1", "aes2", "swrv", "",
                                      "ascon128", "asco", "gimli"])
    def test_false_for_unknown_target(self, name):
        v = AEAD_VECTORS["ascon"]
        assert validate_aead(name, v["key"], v["pt"], _vec_output("ascon"),
                             nonce=v["nonce"], ad=v["ad"]) is False

    def test_unknown_target_is_rejected_before_any_reference_run(self):
        """The unknown-target guard must not depend on the payload at all."""
        assert validate_aead("des", b"", b"", b"") is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_decrypt_is_unconditionally_false(self, core):
        """Documented: hardware AEAD is encrypt-only on this silicon, so a
        decrypt run can never be validated -- even against a byte-perfect
        software result."""
        v = AEAD_VECTORS[core]
        assert validate_aead(core, v["key"], v["pt"], _vec_output(core),
                             nonce=v["nonce"], ad=v["ad"],
                             decrypt=True) is False
        assert validate_aead(core, v["key"], v["ct"], v["pt"],
                             nonce=v["nonce"], ad=v["ad"],
                             decrypt=True) is False

    def test_decrypt_false_also_for_unknown_target(self):
        assert validate_aead("des", bytes(16), b"", b"", decrypt=True) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_returns_a_real_bool(self, core):
        v = AEAD_VECTORS[core]
        assert type(validate_aead(core, v["key"], v["pt"], _vec_output(core),
                                  nonce=v["nonce"], ad=v["ad"])) is bool

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_agrees_with_aead_soft_on_a_fresh_message(self, core):
        enc = (aead_soft.ascon128_encrypt if core == "ascon"
               else aead_soft.xoodyak_encrypt)
        key = bytes(range(16))
        nonce = bytes(range(16, 32))
        ad = b"\xa5" * 24
        pt = bytes(range(40))
        ct, tag = enc(key, nonce, ad, pt)
        assert validate_aead(core, key, pt, ct + tag, nonce=nonce, ad=ad) is True
        assert validate_aead(core, key, pt, tag + ct, nonce=nonce,
                             ad=ad) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_empty_plaintext_still_validates_the_tag(self, core):
        enc = (aead_soft.ascon128_encrypt if core == "ascon"
               else aead_soft.xoodyak_encrypt)
        key = bytes(range(16))
        ct, tag = enc(key, bytes(16), bytes(16), b"")
        assert validate_aead(core, key, b"", ct + tag) is True
        bad = bytes([tag[0] ^ 1]) + tag[1:]
        assert validate_aead(core, key, b"", ct + bad) is False


class TestValidateAeadDefaults:
    """The nonce/ad defaults are load-bearing: experiment.py programs the chip
    with set_nonce(bytes(16)) / set_ad(bytes(16)) and then calls validate_aead
    with neither argument. If the default ever became b"" or None the verdicts
    would silently invert on every AEAD experiment run."""

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_omitted_nonce_and_ad_are_sixteen_zero_bytes(self, core):
        assert validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                             DEFAULT_PARAM_OUTPUT[core]) is True

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_omitting_matches_passing_zeros_explicitly(self, core):
        implicit = validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                                 DEFAULT_PARAM_OUTPUT[core])
        explicit = validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                                 DEFAULT_PARAM_OUTPUT[core],
                                 nonce=bytes(16), ad=bytes(16))
        assert implicit is True and explicit is True

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_default_ad_is_zeros_not_empty(self, core):
        """Sixteen zero AD bytes and zero AD bytes are different messages: the
        default must NOT validate a chip that processed empty AD."""
        assert EMPTY_AD_OUTPUT[core] != DEFAULT_PARAM_OUTPUT[core]
        assert validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                             EMPTY_AD_OUTPUT[core]) is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_matches_aead_soft_called_with_explicit_zeros(self, core):
        enc = (aead_soft.ascon128_encrypt if core == "ascon"
               else aead_soft.xoodyak_encrypt)
        ct, tag = enc(DEFAULT_PARAM_KEY, bytes(16), bytes(16), DEFAULT_PARAM_PT)
        assert ct + tag == DEFAULT_PARAM_OUTPUT[core]

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_empty_ad_constant_is_the_true_empty_ad_result(self, core):
        """Anchors EMPTY_AD_OUTPUT so the coercion xfail below is meaningful:
        it really is what a chip processing empty AD would return."""
        enc = (aead_soft.ascon128_encrypt if core == "ascon"
               else aead_soft.xoodyak_encrypt)
        ct, tag = enc(DEFAULT_PARAM_KEY, bytes(16), b"", DEFAULT_PARAM_PT)
        assert ct + tag == EMPTY_AD_OUTPUT[core]

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_explicit_empty_ad_is_not_coerced_to_zeros(self, core):
        """Only an OMITTED argument gets the zero default: b"" is a real,
        different message, so it must not validate the 16-zero-AD result."""
        assert validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                             DEFAULT_PARAM_OUTPUT[core], ad=b"") is False

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_explicit_empty_nonce_reaches_the_reference(self, core):
        """An empty nonce is not a legal AEAD input for either core, so it must
        hit aead_soft's length check rather than be quietly turned into zeros
        and validated as if the run had used a zero nonce."""
        with pytest.raises(ValueError):
            validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                          DEFAULT_PARAM_OUTPUT[core], nonce=b"")

    @pytest.mark.parametrize("core", ALL_CORES)
    def test_explicit_empty_ad_should_be_honoured(self, core):
        assert validate_aead(core, DEFAULT_PARAM_KEY, DEFAULT_PARAM_PT,
                             EMPTY_AD_OUTPUT[core], ad=b"") is True


class TestModuleSurface:
    def test_module_imports_nothing_at_module_scope_but_typing(self):
        """validation.py must stay dependency-free and hardware-free: it is
        imported by the CLI, the GUI and the offline `cpa` path. aead_soft is
        imported lazily inside validate_aead, so it must not appear here."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(validation))
        top_level = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                top_level.append(node.module or ".")
        assert top_level == ["typing"]

    def test_public_helpers_are_exported(self):
        for name in ("aes128_encrypt_block", "validate_aes", "validate_aead"):
            assert callable(getattr(validation, name))
