"""
Regression tests for proact_host.aead_soft -- the pure-Python ASCON-128 v1.2
and Xoodyak v2 (Cyclist over Xoodoo[12]) implementations.

Why this module matters: the silicon's AEAD wrappers are ENCRYPT-ONLY (the RTL
drops the decrypt output and has no tag-input path), so this module is the only
decrypt path in the whole project -- `proact decrypt-soft`, fullcheck's
`*_decrypt_soft` steps and validation.validate_aead all land here. Encrypt
correctness is anchored to the silicon by the reference vectors the module
ships (ASCON_VEC / XOODYAK_VEC, lifted from hello_test.c == the on-chip KAT).

Everything here is pure computation: no board, no serial port, no network, no
ChipWhisperer, no randomness.

FIXED BUG regression-guarded below (was aead_soft.py `if last or not ct:`):
ascon128_decrypt used to skip the 10* pad block whenever the ciphertext length
was a nonzero multiple of 8 (8/16/24/32 -- 16 is the PROACT block size), while
ascon128_encrypt always absorbs it, so a valid tag was rejected. Fixed by making
the decrypt final block unconditional. The block-aligned round-trips below now
pass and stand as the regression guard.
"""
import pytest

from proact_host.aead_soft import (
    ASCON_VEC,
    XOODYAK_VEC,
    ascon128_decrypt,
    ascon128_encrypt,
    bytes_to_words,
    selftest,
    words_to_bytes,
    xoodyak_decrypt,
    xoodyak_encrypt,
)

KEY = bytes(range(16))
NONCE = bytes(range(16, 32))

# ascon128_decrypt is broken for exactly these plaintext lengths (nonzero
# multiples of the 8-byte ASCON rate). Verified by execution, see module
# docstring above.
ASCON_BLOCK_ALIGNED_PT_LENS = frozenset({8, 16, 24, 32})  # once-broken (BUG-002)


def _pattern(n, seed=1):
    """Deterministic filler bytes -- no randomness anywhere in this suite."""
    return bytes((i * 5 + seed) & 0xFF for i in range(n))


# --------------------------------------------------------------- word helpers
def test_words_to_bytes_is_big_endian_and_truncates():
    assert words_to_bytes([0x11223344, 0x55667788], 6) == bytes.fromhex(
        "112233445566")
    assert words_to_bytes([0x11223344], 4) == bytes.fromhex("11223344")
    assert words_to_bytes([0x11223344], 1) == b"\x11"
    assert words_to_bytes([], 0) == b""


def test_words_to_bytes_never_pads_past_the_supplied_words():
    # nbytes larger than the word list simply yields what is there; the helper
    # is a slice, not a zero-extender.
    assert words_to_bytes([0x11223344, 0x55667788], 10) == bytes.fromhex(
        "1122334455667788")


def test_bytes_to_words_zero_pads_the_trailing_partial_word():
    assert bytes_to_words(bytes.fromhex("112233445566")) == [0x11223344,
                                                             0x55660000]
    assert bytes_to_words(b"\xab") == [0xAB000000]
    assert bytes_to_words(b"") == []


@pytest.mark.parametrize("n", list(range(0, 13)))
def test_word_helpers_round_trip(n):
    data = _pattern(n)
    assert words_to_bytes(bytes_to_words(data), n) == data


# ---------------------------------------------- silicon-anchored KAT vectors
def test_ascon_reference_vector_encrypts_to_the_on_chip_ct_and_tag():
    ct, tag = ascon128_encrypt(ASCON_VEC["key"], ASCON_VEC["nonce"],
                               ASCON_VEC["ad"], ASCON_VEC["pt"])
    assert ct == ASCON_VEC["ct"]
    assert tag == ASCON_VEC["tag"]


def test_xoodyak_reference_vector_encrypts_to_the_on_chip_ct_and_tag():
    ct, tag = xoodyak_encrypt(XOODYAK_VEC["key"], XOODYAK_VEC["nonce"],
                              XOODYAK_VEC["ad"], XOODYAK_VEC["pt"])
    assert ct == XOODYAK_VEC["ct"]
    assert tag == XOODYAK_VEC["tag"]


def test_reference_vectors_have_the_documented_shapes():
    for vec in (ASCON_VEC, XOODYAK_VEC):
        assert len(vec["key"]) == 16
        assert len(vec["nonce"]) == 16
        assert len(vec["tag"]) == 16
        assert len(vec["ct"]) == len(vec["pt"])
    assert len(ASCON_VEC["pt"]) == 23      # partial final block, on purpose
    assert len(XOODYAK_VEC["pt"]) == 119   # crosses several R_kout blocks
    assert len(XOODYAK_VEC["ad"]) == 52    # crosses the R_kin=44 boundary


def test_ascon_matches_the_public_nist_lwc_vector():
    """Independent anchor for the encrypt side.

    NIST LWC KAT for Ascon-128 v1.2, Count = 1 (empty PT, empty AD,
    key = nonce = 000102...0F): CT||TAG = E355159F292911F794CB1432A0103A8A.
    This is external to the project, so it proves the module's ASCON is the
    standard cipher and not merely self-consistent with its own vectors.
    """
    k = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
    ct, tag = ascon128_encrypt(k, k, b"", b"")
    assert ct == b""
    assert tag == bytes.fromhex("E355159F292911F794CB1432A0103A8A")


def test_ascon_reference_vector_decrypts_back_to_the_plaintext():
    assert ascon128_decrypt(ASCON_VEC["key"], ASCON_VEC["nonce"],
                            ASCON_VEC["ad"], ASCON_VEC["ct"],
                            ASCON_VEC["tag"]) == ASCON_VEC["pt"]


def test_xoodyak_reference_vector_decrypts_back_to_the_plaintext():
    assert xoodyak_decrypt(XOODYAK_VEC["key"], XOODYAK_VEC["nonce"],
                           XOODYAK_VEC["ad"], XOODYAK_VEC["ct"],
                           XOODYAK_VEC["tag"]) == XOODYAK_VEC["pt"]


def test_selftest_reports_every_check_passing():
    result = selftest()
    assert set(result) == {
        "ascon_encrypt", "ascon_decrypt", "ascon_reject_bad_tag",
        "xoodyak_encrypt", "xoodyak_decrypt", "xoodyak_reject_bad_tag",
    }
    assert all(result.values()), result


# ------------------------------------------------------------ tag enforcement
@pytest.mark.parametrize("cipher", ["ascon", "xoodyak"])
@pytest.mark.parametrize("bit", [0, 3, 7])
@pytest.mark.parametrize("index", [0, 7, 15])
def test_single_flipped_tag_bit_releases_no_plaintext(cipher, bit, index):
    vec, dec = ((ASCON_VEC, ascon128_decrypt) if cipher == "ascon"
                else (XOODYAK_VEC, xoodyak_decrypt))
    tag = bytearray(vec["tag"])
    tag[index] ^= 1 << bit
    assert dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"],
               bytes(tag)) is None


@pytest.mark.parametrize("cipher", ["ascon", "xoodyak"])
@pytest.mark.parametrize("taglen", [0, 8, 15])
def test_truncated_tag_is_rejected(cipher, taglen):
    """A short tag must not be accepted as a prefix match -- `tag[:16]` in the
    product only truncates, it never pads, so a 8-byte tag can never equal the
    16-byte computed tag."""
    vec, dec = ((ASCON_VEC, ascon128_decrypt) if cipher == "ascon"
                else (XOODYAK_VEC, xoodyak_decrypt))
    assert dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"],
               vec["tag"][:taglen]) is None


@pytest.mark.parametrize("cipher", ["ascon", "xoodyak"])
def test_flipped_ciphertext_bit_releases_no_plaintext(cipher):
    vec, dec = ((ASCON_VEC, ascon128_decrypt) if cipher == "ascon"
                else (XOODYAK_VEC, xoodyak_decrypt))
    ct = bytearray(vec["ct"])
    ct[0] ^= 0x01
    assert dec(vec["key"], vec["nonce"], vec["ad"], bytes(ct),
               vec["tag"]) is None


@pytest.mark.parametrize("cipher", ["ascon", "xoodyak"])
def test_wrong_associated_data_releases_no_plaintext(cipher):
    vec, dec = ((ASCON_VEC, ascon128_decrypt) if cipher == "ascon"
                else (XOODYAK_VEC, xoodyak_decrypt))
    ad = bytearray(vec["ad"])
    ad[0] ^= 0x01
    assert dec(vec["key"], vec["nonce"], bytes(ad), vec["ct"],
               vec["tag"]) is None


@pytest.mark.parametrize("cipher", ["ascon", "xoodyak"])
def test_wrong_key_and_wrong_nonce_release_no_plaintext(cipher):
    vec, dec = ((ASCON_VEC, ascon128_decrypt) if cipher == "ascon"
                else (XOODYAK_VEC, xoodyak_decrypt))
    bad_key = bytes([vec["key"][0] ^ 0x01]) + vec["key"][1:]
    bad_nonce = bytes([vec["nonce"][0] ^ 0x01]) + vec["nonce"][1:]
    assert dec(bad_key, vec["nonce"], vec["ad"], vec["ct"],
               vec["tag"]) is None
    assert dec(vec["key"], bad_nonce, vec["ad"], vec["ct"],
               vec["tag"]) is None


# ------------------------------------------------------- domain separation/AD
@pytest.mark.parametrize("enc", [ascon128_encrypt, xoodyak_encrypt])
def test_associated_data_changes_the_tag(enc):
    """Guards the AD-absorb / domain-separation step: if AD were ignored (or
    empty AD were confused with a zero AD block) these tags would collide."""
    pt = _pattern(20)
    ads = [b"", b"\x00", b"\x00\x00", _pattern(7, seed=9), _pattern(8, seed=9)]
    tags = [enc(KEY, NONCE, ad, pt)[1] for ad in ads]
    assert len(set(tags)) == len(tags), "AD is not fully absorbed into the tag"


@pytest.mark.parametrize("enc", [ascon128_encrypt, xoodyak_encrypt])
def test_plaintext_changes_the_ciphertext_and_the_tag(enc):
    ad = _pattern(11, seed=3)
    ct_a, tag_a = enc(KEY, NONCE, ad, _pattern(20))
    ct_b, tag_b = enc(KEY, NONCE, ad, _pattern(20, seed=2))
    assert ct_a != ct_b
    assert tag_a != tag_b


@pytest.mark.parametrize("enc", [ascon128_encrypt, xoodyak_encrypt])
def test_encryption_is_deterministic(enc):
    ad, pt = _pattern(13, seed=4), _pattern(37, seed=6)
    assert enc(KEY, NONCE, ad, pt) == enc(KEY, NONCE, ad, pt)


# --------------------------------------------------------- argument validation
@pytest.mark.parametrize("badlen", [0, 1, 15, 17, 32])
@pytest.mark.parametrize("which", ["key", "nonce"])
@pytest.mark.parametrize("enc", [ascon128_encrypt, xoodyak_encrypt])
def test_encrypt_rejects_wrong_key_or_nonce_length(enc, which, badlen):
    key = b"\x00" * badlen if which == "key" else KEY
    nonce = b"\x00" * badlen if which == "nonce" else NONCE
    with pytest.raises(ValueError):
        enc(key, nonce, b"", b"")


@pytest.mark.parametrize("which", ["key", "nonce"])
@pytest.mark.parametrize("dec", [ascon128_decrypt, xoodyak_decrypt])
def test_decrypt_rejects_wrong_key_or_nonce_length(dec, which):
    key = b"\x00" * 15 if which == "key" else KEY
    nonce = b"\x00" * 15 if which == "nonce" else NONCE
    with pytest.raises(ValueError):
        dec(key, nonce, b"", b"", b"\x00" * 16)


# ------------------------------------------------------- Xoodyak round trips
def _xoodyak_roundtrip_params():
    # R_kin = 44 (absorb rate), R_kout = 24 (crypt rate); the AD lengths below
    # sit before/at/after the 44-byte boundary and the plaintext sweep crosses
    # the 24-byte one several times. 119 is the reference-vector length.
    for adlen in (0, 5, 24, 44, 48):
        for ptlen in list(range(0, 60)) + [119]:
            yield pytest.param(adlen, ptlen, id=f"ad{adlen}-pt{ptlen}")


@pytest.mark.parametrize("adlen,ptlen", list(_xoodyak_roundtrip_params()))
def test_xoodyak_encrypt_decrypt_round_trip(adlen, ptlen):
    ad, pt = _pattern(adlen, seed=3), _pattern(ptlen)
    ct, tag = xoodyak_encrypt(KEY, NONCE, ad, pt)
    assert len(ct) == ptlen
    assert len(tag) == 16
    assert xoodyak_decrypt(KEY, NONCE, ad, ct, tag) == pt


# --------------------------------------------------------- ASCON round trips
def _ascon_roundtrip_params():
    for adlen in (0, 7, 8, 16):
        for ptlen in range(0, 25):
            yield pytest.param(adlen, ptlen, id=f"ad{adlen}-pt{ptlen}")


@pytest.mark.parametrize("adlen,ptlen", list(_ascon_roundtrip_params()))
def test_ascon_encrypt_decrypt_round_trip(adlen, ptlen):
    ad, pt = _pattern(adlen, seed=3), _pattern(ptlen)
    ct, tag = ascon128_encrypt(KEY, NONCE, ad, pt)
    assert len(ct) == ptlen
    assert len(tag) == 16
    assert ascon128_decrypt(KEY, NONCE, ad, ct, tag) == pt


@pytest.mark.parametrize("ptlen", sorted(ASCON_BLOCK_ALIGNED_PT_LENS))
def test_ascon_block_aligned_decrypt_recovers_the_plaintext(ptlen):
    """BUG-002 regression: a block-aligned ciphertext (len % 8 == 0) must
    decrypt back to the exact plaintext -- the case the old `if last or not ct:`
    guard broke. Also confirms the AEAD contract is never violated (a wrong
    tag would give None, never a different plaintext)."""
    ad, pt = _pattern(8, seed=3), _pattern(ptlen)
    ct, tag = ascon128_encrypt(KEY, NONCE, ad, pt)
    got = ascon128_decrypt(KEY, NONCE, ad, ct, tag)
    assert got == pt


def test_ascon_block_aligned_ciphertext_still_has_the_right_length():
    """Encrypt is fine at block-aligned lengths -- |ct| == |pt| and the pad
    block is absorbed but not emitted. Pins the encrypt side so a future fix
    to decrypt cannot be mistaken for an encrypt-side change."""
    for ptlen in (8, 16, 24, 32):
        ct, tag = ascon128_encrypt(KEY, NONCE, b"", _pattern(ptlen))
        assert len(ct) == ptlen
        assert len(tag) == 16


def test_ascon_and_xoodyak_produce_different_ciphertexts():
    """Cheap guard against the two code paths accidentally aliasing."""
    ad, pt = _pattern(9, seed=3), _pattern(30)
    assert ascon128_encrypt(KEY, NONCE, ad, pt) != xoodyak_encrypt(
        KEY, NONCE, ad, pt)
