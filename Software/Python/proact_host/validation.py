"""
Result validation. For the AES cores we compare the chip's output against a
compact dependency-free software AES-128 (ECB). For the AEAD cores (ASCON,
Xoodyak) a full software reference is out of scope for v1, so those are
validated by encrypt->decrypt round-trip at the firmware level; validate_aead()
returns None ("not checked") here and callers should use the round-trip path.
"""
from typing import List, Optional

# ---- AES-128 (pure Python, ECB single block) -- for expected-ciphertext check
_SBOX = []
_INV_SBOX = []


def _init_sbox():
    p = q = 1
    sbox = [0] * 256
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        xf = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ \
             ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (xf ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


_SBOX, _INV_SBOX = _init_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1)


def _mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        a = _xtime(a); b >>= 1
    return r & 0xFF


def _expand_key(key: bytes) -> List[List[int]]:
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(words[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        words.append([words[i - 4][j] ^ t[j] for j in range(4)])
    return words


def _add_round_key(s, w):
    for c in range(4):
        for r in range(4):
            s[r][c] ^= w[c][r]


def aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    w = _expand_key(key)
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(s, w[0:4])
    for rnd in range(1, 10):
        s = [[_SBOX[s[r][c]] for c in range(4)] for r in range(4)]
        s = [s[r][r:] + s[r][:r] for r in range(4)]
        ns = [[0] * 4 for _ in range(4)]
        for c in range(4):
            col = [s[r][c] for r in range(4)]
            ns[0][c] = _mul(col[0], 2) ^ _mul(col[1], 3) ^ col[2] ^ col[3]
            ns[1][c] = col[0] ^ _mul(col[1], 2) ^ _mul(col[2], 3) ^ col[3]
            ns[2][c] = col[0] ^ col[1] ^ _mul(col[2], 2) ^ _mul(col[3], 3)
            ns[3][c] = _mul(col[0], 3) ^ col[1] ^ col[2] ^ _mul(col[3], 2)
        s = ns
        _add_round_key(s, w[4 * rnd:4 * rnd + 4])
    s = [[_SBOX[s[r][c]] for c in range(4)] for r in range(4)]
    s = [s[r][r:] + s[r][:r] for r in range(4)]
    _add_round_key(s, w[40:44])
    return bytes(s[r][c] for c in range(4) for r in range(4))


def validate_aes(key: bytes, input_block: bytes, chip_output: bytes, decrypt: bool = False) -> bool:
    """True if the chip's AES output matches the software reference."""
    # Everything the reference cipher touches must be full-length: callers hand
    # us a truncated key or read-back whenever the chip times out mid-frame, and
    # that is a FAIL in both directions, not an IndexError.
    if len(key) != 16:
        return False
    if decrypt:
        return (len(chip_output) == 16
                and aes128_encrypt_block(key, chip_output) == input_block)
    return (len(input_block) == 16
            and aes128_encrypt_block(key, input_block) == chip_output)


def validate_aead(target: str, key: bytes, pt: bytes, chip_output: bytes,
                  nonce: Optional[bytes] = None, ad: Optional[bytes] = None,
                  decrypt: bool = False) -> bool:
    """True if the chip's ASCON/Xoodyak output matches the software reference."""
    from . import aead_soft
    target = target.lower()
    if target == "ascon":
        enc = aead_soft.ascon128_encrypt
    elif target == "xoodyak":
        enc = aead_soft.xoodyak_encrypt
    else:
        return False

    if decrypt:
        # Hardware AEAD is encrypt-only on this silicon; a hardware decrypt
        # times out and returns zeros.
        return False

    # Only an omitted nonce/AD falls back to the zero default: empty AD is a
    # legal AEAD input and gives a different tag, so b"" must not be coerced.
    if nonce is None:
        nonce = bytes(16)
    if ad is None:
        ad = bytes(16)

    # For encryption, input is PT and chip returns CT || Tag.
    # Software reference:
    ct, tag = enc(key, nonce, ad, pt)
    return chip_output == (ct + tag)
