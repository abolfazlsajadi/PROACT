"""Vectorised (numpy, whole-campaign-at-once) ASCON-p and Xoodoo round
functions, used to build known-key leakage models that locate each permutation
round inside the trigger window.
"""
import numpy as np

_POP = np.array([bin(i).count("1") for i in range(256)], np.uint8)


def popcount(a):
    """Hamming weight of each element of a uint32/uint64 array."""
    return _POP[a.view(np.uint8).reshape(a.shape[0], -1)].sum(1, dtype=np.int32)


def hw_state(words):
    """words: list of uint32/uint64 arrays -> total Hamming weight (n,)."""
    return sum(popcount(w) for w in words)


# ----------------------------- ASCON --------------------------------------- #
U64 = np.uint64
RC12 = [U64(c) for c in (0xF0, 0xE1, 0xD2, 0xC3, 0xB4, 0xA5,
                         0x96, 0x87, 0x78, 0x69, 0x5A, 0x4B)]


def _rotr64(x, n):
    return (x >> U64(n)) | (x << U64(64 - n))


def ascon_round_vec(S, rc):
    x0, x1, x2, x3, x4 = S
    x2 = x2 ^ rc
    x0 = x0 ^ x4
    x4 = x4 ^ x3
    x2 = x2 ^ x1
    t0 = ~x0 & x1
    t1 = ~x1 & x2
    t2 = ~x2 & x3
    t3 = ~x3 & x4
    t4 = ~x4 & x0
    x0 = x0 ^ t1
    x1 = x1 ^ t2
    x2 = x2 ^ t3
    x3 = x3 ^ t4
    x4 = x4 ^ t0
    x1 = x1 ^ x0
    x0 = x0 ^ x4
    x3 = x3 ^ x2
    x2 = ~x2
    x0 = x0 ^ _rotr64(x0, 19) ^ _rotr64(x0, 28)
    x1 = x1 ^ _rotr64(x1, 61) ^ _rotr64(x1, 39)
    x2 = x2 ^ _rotr64(x2, 1) ^ _rotr64(x2, 6)
    x3 = x3 ^ _rotr64(x3, 10) ^ _rotr64(x3, 17)
    x4 = x4 ^ _rotr64(x4, 7) ^ _rotr64(x4, 41)
    return [x0, x1, x2, x3, x4]


def ascon_be64(block):
    """(n,8) uint8 big-endian -> (n,) uint64."""
    out = np.zeros(block.shape[0], np.uint64)
    for j in range(8):
        out = (out << U64(8)) | block[:, j].astype(np.uint64)
    return out


def ascon_init_vec(key, nonce):
    """key: bytes(16); nonce: (n,16) uint8 -> the 5 state words."""
    n = nonce.shape[0]
    k0 = np.full(n, int.from_bytes(key[0:8], "big"), np.uint64)
    k1 = np.full(n, int.from_bytes(key[8:16], "big"), np.uint64)
    iv = np.full(n, 0x80400C0600000000, np.uint64)
    return [iv, k0, k1, ascon_be64(nonce[:, 0:8]), ascon_be64(nonce[:, 8:16])]


def ascon_round_trace(key, nonce, nrounds=12):
    """Return [state_0, state_1, ... state_nrounds] of the p^12 init."""
    S = ascon_init_vec(key, nonce)
    out = [S]
    for r in range(nrounds):
        S = ascon_round_vec(S, RC12[r])
        out.append(S)
    return out


# ----------------------------- Xoodoo -------------------------------------- #
U32 = np.uint32
XRC = [U32(c) for c in (0x058, 0x038, 0x3C0, 0x0D0, 0x120, 0x014,
                        0x060, 0x02C, 0x380, 0x0F0, 0x1A0, 0x012)]


def _rotl32(x, n):
    n %= 32
    if n == 0:
        return x
    return (x << U32(n)) | (x >> U32(32 - n))


def xoodoo_round_vec(A, rc):
    a = list(A)
    P = [a[x] ^ a[4 + x] ^ a[8 + x] for x in range(4)]
    E = [_rotl32(P[(x - 1) % 4], 5) ^ _rotl32(P[(x - 1) % 4], 14) for x in range(4)]
    for y in range(3):
        for x in range(4):
            a[4 * y + x] = a[4 * y + x] ^ E[x]
    b = list(a)
    for x in range(4):
        b[4 + x] = a[4 + ((x - 1) % 4)]
        b[8 + x] = _rotl32(a[8 + x], 11)
    a = b
    a[0] = a[0] ^ rc
    c = list(a)
    for x in range(4):
        a[x] = c[x] ^ (~c[4 + x] & c[8 + x])
        a[4 + x] = c[4 + x] ^ (~c[8 + x] & c[x])
        a[8 + x] = c[8 + x] ^ (~c[x] & c[4 + x])
    b = list(a)
    for x in range(4):
        b[4 + x] = _rotl32(a[4 + x], 1)
        b[8 + x] = _rotl32(a[8 + ((x - 2) % 4)], 8)
    return b


XOO_C = bytes([0x10, 0x01, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x02])


def xoo_le32(block):
    """(n,4k) uint8 -> list of k uint32 lanes (little-endian bytes)."""
    n, m = block.shape
    return [block[:, 4 * i:4 * i + 4].astype(np.uint32)
            @ np.array([1, 1 << 8, 1 << 16, 1 << 24], np.uint32)
            for i in range(m // 4)]


def xoodyak_init_vec(key, nonce):
    """State just before permutation #1: K | N | 10 01 .. 02."""
    n = nonce.shape[0]
    kl = xoo_le32(np.frombuffer(key, np.uint8)[None, :].repeat(1, 0))
    cl = xoo_le32(np.frombuffer(XOO_C, np.uint8)[None, :])
    A = [np.full(n, int(kl[i][0]), np.uint32) for i in range(4)]
    A += xoo_le32(nonce)
    A += [np.full(n, int(cl[i][0]), np.uint32) for i in range(4)]
    return A


def xoodoo_round_trace(key, nonce, nrounds=12):
    A = xoodyak_init_vec(key, nonce)
    out = [A]
    for r in range(nrounds):
        A = xoodoo_round_vec(A, XRC[r])
        out.append(A)
    return out
