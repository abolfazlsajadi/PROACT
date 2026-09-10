"""
Software ASCON-128 / Xoodyak -- the DECRYPT patch for the frozen silicon.

The chip's ASCON and Xoodyak wrappers are ENCRYPT-only (the RTL drops the
decrypt output and has no tag-input path -- see proact_aead.h). That cannot be
fixed by firmware, but it CAN be patched one level up: this module implements
both ciphers bit-exactly in pure Python (no dependencies), so the host can
decrypt and tag-verify anything the hardware encrypted:

    hardware  encrypt(key, npub, ad, pt)      -> ct, tag     (fast, on-chip)
    software  decrypt(key, npub, ad, ct, tag) -> pt          (this module)

Both implementations are validated against the professor's reference vectors
from hello_test.c -- the same CT/TAG the silicon itself produces (the on-chip
KAT) -- so software and hardware are provably the same cipher.

Algorithms: ASCON-128 v1.2 and Xoodyak (Cyclist over Xoodoo[12]), the NIST LWC
versions instantiated in ASIC/rtl/{ASCON,Xoodyak}. Byte order matches the
hardware FIFOs: 32-bit words, first byte of the stream in the most significant
byte (`words_to_bytes` / `bytes_to_words`).

This is plain Python: fine for validation, round-trip tests and experiment
post-processing; not constant-time and not for production key handling.
"""
from typing import List, Optional, Tuple
from hmac import compare_digest

_M64 = 0xFFFFFFFFFFFFFFFF
_M32 = 0xFFFFFFFF


# ---------------------------------------------------------------- word helpers
def words_to_bytes(words: List[int], nbytes: int) -> bytes:
    """Hardware FIFO word list -> byte stream (big-endian words, truncated)."""
    return b"".join(w.to_bytes(4, "big") for w in words)[:nbytes]


def bytes_to_words(data: bytes) -> List[int]:
    """Byte stream -> hardware FIFO words (zero-padded into the last word)."""
    pad = (-len(data)) % 4
    data = data + b"\x00" * pad
    return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]


# ==================================================================== ASCON-128
def _ror64(x: int, n: int) -> int:
    return ((x >> n) | (x << (64 - n))) & _M64


def _ascon_perm(s: List[int], rounds: int) -> None:
    """Ascon permutation p^rounds on the 5x64-bit state, in place."""
    for r in range(12 - rounds, 12):
        # round constant
        s[2] ^= ((0xF - r) << 4) | r
        # substitution layer
        x0, x1, x2, x3, x4 = s
        x0 ^= x4; x4 ^= x3; x2 ^= x1
        t0 = (~x0 & _M64) & x1
        t1 = (~x1 & _M64) & x2
        t2 = (~x2 & _M64) & x3
        t3 = (~x3 & _M64) & x4
        t4 = (~x4 & _M64) & x0
        x0 ^= t1; x1 ^= t2; x2 ^= t3; x3 ^= t4; x4 ^= t0
        x1 ^= x0; x0 ^= x4; x3 ^= x2; x2 = ~x2 & _M64
        # linear diffusion layer
        s[0] = x0 ^ _ror64(x0, 19) ^ _ror64(x0, 28)
        s[1] = x1 ^ _ror64(x1, 61) ^ _ror64(x1, 39)
        s[2] = x2 ^ _ror64(x2, 1) ^ _ror64(x2, 6)
        s[3] = x3 ^ _ror64(x3, 10) ^ _ror64(x3, 17)
        s[4] = x4 ^ _ror64(x4, 7) ^ _ror64(x4, 41)


_ASCON_IV = 0x80400C0600000000  # ASCON-128: k=128, r=64, a=12, b=6


def _ascon_init(key: bytes, nonce: bytes) -> Tuple[List[int], int, int]:
    if len(key) != 16 or len(nonce) != 16:
        raise ValueError("ASCON-128 needs a 16-byte key and 16-byte nonce")
    k0 = int.from_bytes(key[:8], "big")
    k1 = int.from_bytes(key[8:], "big")
    n0 = int.from_bytes(nonce[:8], "big")
    n1 = int.from_bytes(nonce[8:], "big")
    s = [_ASCON_IV, k0, k1, n0, n1]
    _ascon_perm(s, 12)
    s[3] ^= k0
    s[4] ^= k1
    return s, k0, k1


def _ascon_ad(s: List[int], ad: bytes) -> None:
    if ad:
        padded = ad + b"\x80" + b"\x00" * ((-len(ad) - 1) % 8)
        for i in range(0, len(padded), 8):
            s[0] ^= int.from_bytes(padded[i:i + 8], "big")
            _ascon_perm(s, 6)
    s[4] ^= 1  # domain separation


def _ascon_final(s: List[int], k0: int, k1: int) -> bytes:
    s[1] ^= k0
    s[2] ^= k1
    _ascon_perm(s, 12)
    return ((s[3] ^ k0).to_bytes(8, "big") + ((s[4] ^ k1).to_bytes(8, "big")))


def ascon128_encrypt(key: bytes, nonce: bytes, ad: bytes,
                     pt: bytes) -> Tuple[bytes, bytes]:
    """ASCON-128 v1.2 AEAD encrypt. Returns (ciphertext, 16-byte tag)."""
    s, k0, k1 = _ascon_init(key, nonce)
    _ascon_ad(s, ad)
    ct = bytearray()
    for i in range(0, len(pt) - len(pt) % 8, 8):  # full 8-byte blocks
        s[0] ^= int.from_bytes(pt[i:i + 8], "big")
        ct += s[0].to_bytes(8, "big")
        _ascon_perm(s, 6)
    last = pt[len(pt) - len(pt) % 8:]  # final partial block (may be empty)
    s[0] ^= int.from_bytes((last + b"\x80").ljust(8, b"\x00"), "big")
    ct += s[0].to_bytes(8, "big")[:len(last)]
    return bytes(ct), _ascon_final(s, k0, k1)


def ascon128_decrypt(key: bytes, nonce: bytes, ad: bytes, ct: bytes,
                     tag: bytes) -> Optional[bytes]:
    """ASCON-128 v1.2 AEAD decrypt. Returns plaintext, or None if the tag is
    wrong (in which case no plaintext is released -- the AEAD contract)."""
    s, k0, k1 = _ascon_init(key, nonce)
    _ascon_ad(s, ad)
    pt = bytearray()
    for i in range(0, len(ct) - len(ct) % 8, 8):
        c = int.from_bytes(ct[i:i + 8], "big")
        pt += (s[0] ^ c).to_bytes(8, "big")
        s[0] = c
        _ascon_perm(s, 6)
    # Final block, ALWAYS (mirrors ascon128_encrypt): even a block-aligned
    # ciphertext -- len(ct) a nonzero multiple of 8, so `last` is empty -- must
    # still absorb the 0x80 pad byte, or the tag diverges. The old `if last or
    # not ct:` guard skipped this whole block in exactly that case (BUG-002).
    last = ct[len(ct) - len(ct) % 8:]
    mask = (1 << (8 * len(last))) - 1
    c = int.from_bytes(last.ljust(8, b"\x00"), "big")
    p_last = ((s[0] ^ c) >> (64 - 8 * len(last))) & mask if last else 0
    plast_bytes = p_last.to_bytes(len(last), "big") if last else b""
    pt += plast_bytes
    s[0] ^= int.from_bytes((plast_bytes + b"\x80").ljust(8, b"\x00"), "big")
    calc = _ascon_final(s, k0, k1)
    return bytes(pt) if len(tag) == 16 and compare_digest(calc, tag) else None


# ====================================================================== Xoodyak
_XOODOO_RC = (0x058, 0x038, 0x3C0, 0x0D0, 0x120, 0x014,
              0x060, 0x02C, 0x380, 0x0F0, 0x1A0, 0x012)


def _rotl32(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & _M32


def _xoodoo(state: bytearray) -> None:
    """Xoodoo[12] permutation on the 48-byte state (little-endian lanes)."""
    a = [int.from_bytes(state[4 * i:4 * i + 4], "little") for i in range(12)]
    for rc in _XOODOO_RC:
        # theta
        p = [a[x] ^ a[x + 4] ^ a[x + 8] for x in range(4)]
        e = [_rotl32(p[(x - 1) % 4], 5) ^ _rotl32(p[(x - 1) % 4], 14)
             for x in range(4)]
        for x in range(4):
            a[x] ^= e[x]; a[x + 4] ^= e[x]; a[x + 8] ^= e[x]
        # rho-west
        a[4:8] = [a[4 + (x - 1) % 4] for x in range(4)]
        a[8:12] = [_rotl32(a[8 + x], 11) for x in range(4)]
        # iota
        a[0] ^= rc
        # chi
        b = [(~a[(i + 4) % 12] & _M32) & a[(i + 8) % 12] for i in range(12)]
        a = [a[i] ^ b[i] for i in range(12)]
        # rho-east
        a[4:8] = [_rotl32(a[4 + x], 1) for x in range(4)]
        a[8:12] = [_rotl32(a[8 + (x - 2) % 4], 8) for x in range(4)]
    for i in range(12):
        state[4 * i:4 * i + 4] = a[i].to_bytes(4, "little")


class _Cyclist:
    """Cyclist keyed mode over Xoodoo[12] -- Xoodyak v2, the NIST LWC
    final-round version instantiated in the silicon (the GMU CryptoCore.vhd):
    the nonce is absorbed TOGETHER with the key as K||N||0x10 in one block
    (PADD_01_KEY_NONCE in the RTL), not as a separate Absorb call.
    R_kin=44, R_kout=24, 16-byte tag."""
    R_KIN, R_KOUT = 44, 24

    def __init__(self, key: bytes, nonce: bytes):
        if len(key) != 16 or len(nonce) != 16:
            raise ValueError("Xoodyak needs a 16-byte key and 16-byte nonce")
        self.s = bytearray(48)
        self.up_phase = True
        # AbsorbKey v2: K || N || enc8(|N|), domain 0x02
        self._absorb_any(key + nonce + b"\x10", self.R_KIN, 0x02)

    def _up(self, n: int, cd: int) -> bytes:
        self.s[47] ^= cd
        _xoodoo(self.s)
        self.up_phase = True
        return bytes(self.s[:n])

    def _down(self, block: bytes, cd: int) -> None:
        for i, byte in enumerate(block):
            self.s[i] ^= byte
        self.s[len(block)] ^= 0x01
        self.s[47] ^= cd
        self.up_phase = False

    def _absorb_any(self, data: bytes, r: int, cd: int) -> None:
        blocks = [data[i:i + r] for i in range(0, len(data), r)] or [b""]
        for i, block in enumerate(blocks):
            if not self.up_phase:
                self._up(0, 0x00)
            self._down(block, cd if i == 0 else 0x00)

    def absorb(self, data: bytes) -> None:
        self._absorb_any(data, self.R_KIN, 0x03)

    def crypt(self, data: bytes, decrypt: bool) -> bytes:
        out = bytearray()
        blocks = [data[i:i + self.R_KOUT]
                  for i in range(0, len(data), self.R_KOUT)] or [b""]
        for i, block in enumerate(blocks):
            ks = self._up(len(block), 0x80 if i == 0 else 0x00)
            o = bytes(x ^ y for x, y in zip(block, ks))
            self._down(o if decrypt else block, 0x00)
            out += o
        return bytes(out)

    def squeeze(self, n: int) -> bytes:
        return self._up(n, 0x40)


def xoodyak_encrypt(key: bytes, nonce: bytes, ad: bytes,
                    pt: bytes) -> Tuple[bytes, bytes]:
    """Xoodyak v2 (NIST LWC) AEAD encrypt. Returns (ciphertext, 16-byte tag)."""
    cy = _Cyclist(key, nonce)
    cy.absorb(ad)
    ct = cy.crypt(pt, decrypt=False)
    return ct, cy.squeeze(16)


def xoodyak_decrypt(key: bytes, nonce: bytes, ad: bytes, ct: bytes,
                    tag: bytes) -> Optional[bytes]:
    """Xoodyak v2 (NIST LWC) AEAD decrypt. Returns plaintext, or None if the
    tag is wrong (no plaintext released)."""
    cy = _Cyclist(key, nonce)
    cy.absorb(ad)
    pt = cy.crypt(ct, decrypt=True)
    calc = cy.squeeze(16)
    return pt if len(tag) == 16 and compare_digest(calc, tag) else None


# ================================================== reference-vector self-test
# The professor's vectors from hello_test.c -- identical to the on-chip KAT
# (proact_aead_kat.c), so passing here proves software == silicon.
ASCON_VEC = dict(
    key=words_to_bytes([0xB9706737, 0x90D61894, 0x80658A26, 0xF1153990], 16),
    nonce=words_to_bytes([0x80DD6990, 0x40345DF4, 0x8EF8551F, 0x6EAFF74B], 16),
    ad=words_to_bytes([0xBF7977FA, 0x96AE8D19, 0x162305B8,
                       0xE9390A0D, 0x426547A3, 0xF2D74325], 24),
    pt=words_to_bytes([0x712A8602, 0xCA9B115B, 0xDFFF23E2,
                       0x0DB024BE, 0x60E40836, 0x8DC7E400], 23),
    ct=words_to_bytes([0xD261E5EC, 0xE87E069D, 0x4A7D4AAB,
                       0xFAD859B7, 0x13D1D33C, 0x4AF5649A], 23),
    tag=words_to_bytes([0xD6C6D05E, 0x763D136B, 0xEFAF0070, 0x08582726], 16),
)
XOODYAK_VEC = dict(
    key=words_to_bytes([0xC0836CAF, 0xDFFE489B, 0x5EFF7464, 0x3738D040], 16),
    nonce=words_to_bytes([0xD3AD2BCF, 0x33EFF168, 0xC77C6DC8, 0x49F4862E], 16),
    ad=words_to_bytes([0xEB02C844, 0xF99DA94B, 0x1AB21CFF, 0x0240B08A,
                       0x0EF32862, 0xB449C7C0, 0x8E0DD0EE, 0x08620960,
                       0xC78E8B04, 0x89B83712, 0xC78B9D57, 0x532E068E,
                       0x57CC0E37], 52),
    pt=words_to_bytes([0x654D167D, 0xAD146BF1, 0x2FDCC174, 0xB79DF921,
                       0x1C5ED878, 0x854F1593, 0x9B3C40EA, 0x798D4333,
                       0x7FD73731, 0xA693CA3B, 0x70634557, 0x1F52A2BC,
                       0xE27B0E68, 0x9E9BD0A3, 0xD9DE0246, 0xF2555F68,
                       0x4C7BD6E7, 0xFC32752F, 0x0CA355AA, 0x94C4CF0F,
                       0x904AC1A0, 0xCDFE3B25, 0xE5E0F315, 0xBB9E7263,
                       0x09BF60A6, 0x3EC3A3DD, 0x2A382F58, 0x02BDC03E,
                       0x40192241, 0x848DD900], 119),
    ct=words_to_bytes([0x72F6F21D, 0x37EE08E3, 0x6FB542B4, 0x3CEF1DDD,
                       0x5E5AEC02, 0xEDE082A7, 0x07C98C3B, 0x0B855895,
                       0xFE0B1E83, 0x1B754328, 0xA186EE41, 0xCE7E42E3,
                       0x735B51F5, 0xAF093321, 0xC456B9C5, 0xEA9060C8,
                       0xBC1F101E, 0x38BCDB6C, 0x0FF0AA82, 0x500AB835,
                       0x44B368D1, 0x327B1F88, 0xA227BCC1, 0xBC117891,
                       0x0E513860, 0xA6616B78, 0x741053D6, 0x1DB30FD6,
                       0x11B07D19, 0xF54BEE1F], 119),
    tag=words_to_bytes([0x33BF7F7D, 0xD03606BE, 0x52B77D21, 0x709FBC1E], 16),
)


def selftest() -> dict:
    """Validate both ciphers against the silicon's reference vectors, both
    directions, plus a wrong-tag rejection check. Returns {name: bool}."""
    r = {}
    for name, vec, enc, dec in (
            ("ascon", ASCON_VEC, ascon128_encrypt, ascon128_decrypt),
            ("xoodyak", XOODYAK_VEC, xoodyak_encrypt, xoodyak_decrypt)):
        ct, tag = enc(vec["key"], vec["nonce"], vec["ad"], vec["pt"])
        r[f"{name}_encrypt"] = (ct == vec["ct"] and tag == vec["tag"])
        r[f"{name}_decrypt"] = (
            dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"], vec["tag"])
            == vec["pt"])
        bad = bytes([vec["tag"][0] ^ 1]) + vec["tag"][1:]
        r[f"{name}_reject_bad_tag"] = (
            dec(vec["key"], vec["nonce"], vec["ad"], vec["ct"], bad) is None)
    return r
