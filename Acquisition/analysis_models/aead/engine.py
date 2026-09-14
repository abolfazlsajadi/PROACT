"""Streaming CPA engine + the AEAD leakage models.

One pass over a campaign accumulates the joint statistics of an arbitrary set
of model columns M (n x C) against every trace sample T (n x S):

    N, sum(M), sum(M*M), sum(T), sum(T*T), M^T @ T

from which the exact Pearson correlation of every column against every sample
follows.  Model columns are built as ONE-HOT CLASS INDICATORS wherever
possible: if a predicted intermediate depends only on a small set of known
nonce bits, the class-conditional trace means are a *sufficient statistic* and
every key hypothesis can then be scored offline for free, exactly, without
re-reading the 3 GB of traces.
"""
import numpy as np

# --------------------------------------------------------------------------- #
#  accumulator
# --------------------------------------------------------------------------- #


class Corr:
    """Streaming Pearson correlation of C model columns vs S trace samples."""

    def __init__(self, C, S):
        self.C, self.S = C, S
        self.n = 0
        self.sm = np.zeros(C, np.float64)
        self.smm = np.zeros(C, np.float64)
        self.st = np.zeros(S, np.float64)
        self.stt = np.zeros(S, np.float64)
        self.smt = np.zeros((C, S), np.float64)

    def update(self, M, T):
        """M: (n,C) float32, T: (n,S) float32."""
        self.n += M.shape[0]
        self.sm += M.sum(0, dtype=np.float64)
        self.smm += (M * M).sum(0, dtype=np.float64)
        self.st += T.sum(0, dtype=np.float64)
        self.stt += (T * T).sum(0, dtype=np.float64)
        self.smt += (M.T @ T).astype(np.float64)

    def snapshot(self):
        return dict(n=self.n, sm=self.sm.copy(), smm=self.smm.copy(),
                    st=self.st.copy(), stt=self.stt.copy(), smt=self.smt.copy())

    @staticmethod
    def rho_from(s):
        n = s["n"]
        mm = s["sm"] / n
        mt = s["st"] / n
        vm = s["smm"] / n - mm ** 2
        vt = s["stt"] / n - mt ** 2
        cov = s["smt"] / n - mm[:, None] * mt[None, :]
        den = np.sqrt(np.maximum(vm, 1e-30)[:, None] * np.maximum(vt, 1e-30)[None, :])
        return cov / den

    def rho(self):
        return self.rho_from(self.snapshot())


def rho_of_class_model(cls_stats, h):
    """Exact Pearson rho of a predictor that is a function of a class label.

    cls_stats : (K, S+2) -> [:, 0] = count, [:, 1] = sum(T) is folded in
                we instead pass (cnt (K,), sums (K,S), st (S,), stt (S,), n)
    h         : (K,) predicted leakage value for each class
    """
    cnt, sums, st, stt, n = cls_stats
    p = cnt / n
    mh = float((p * h).sum())
    vh = float((p * h * h).sum()) - mh ** 2
    mt = st / n
    vt = stt / n - mt ** 2
    cov = (h[:, None] * sums).sum(0) / n - mh * mt
    return cov / np.sqrt(max(vh, 1e-30) * np.maximum(vt, 1e-30))


# --------------------------------------------------------------------------- #
#  bit helpers
# --------------------------------------------------------------------------- #

def bits_msb64(byte_block):
    """(n,8) bytes, big-endian 64-bit word -> (n,64) bits, index 0 = LSB."""
    v = np.zeros((byte_block.shape[0], 64), np.uint8)
    for i in range(64):
        byte = 7 - (i // 8)
        v[:, i] = (byte_block[:, byte] >> (i % 8)) & 1
    return v


def lane_bits(block16):
    """(n,16) bytes -> (n,4,32) bits with Xoodoo lane convention:
    lane x holds bytes 4x..4x+3, bit i = bit (i%8) of byte 4x + i//8."""
    n = block16.shape[0]
    return np.unpackbits(block16.reshape(n, 4, 4), axis=2,
                         bitorder="little").reshape(n, 4, 32)


# --------------------------------------------------------------------------- #
#  ASCON round-1 model
# --------------------------------------------------------------------------- #
ASCON_IV = 0x80400C0600000000
ASCON_RC1 = 0xF0                       # first round of the 12-round init


def _iv_bits():
    return np.array([(ASCON_IV >> i) & 1 for i in range(64)], np.uint8)


def _rc_bits(rc):
    return np.array([(rc >> i) & 1 for i in range(64)], np.uint8)


def ascon_sbox_bits(x0, x1, x2, x3, x4):
    """Bit-level ASCON S-box, inputs/outputs are 0/1 arrays of any shape."""
    x0 = x0 ^ x4
    x4 = x4 ^ x3
    x2 = x2 ^ x1
    t0 = (1 - x0) * x1
    t1 = (1 - x1) * x2
    t2 = (1 - x2) * x3
    t3 = (1 - x3) * x4
    t4 = (1 - x4) * x0
    x0 = x0 ^ t1
    x1 = x1 ^ t2
    x2 = x2 ^ t3
    x3 = x3 ^ t4
    x4 = x4 ^ t0
    x1 = x1 ^ x0
    x0 = x0 ^ x4
    x3 = x3 ^ x2
    x2 = x2 ^ 1
    return np.stack([x0, x1, x2, x3, x4])


def ascon_sbox_table():
    """table[i, a, b, c, d, j] = bit j of the round-1 S-box output at bit
    position i, for key hypothesis (a,b) = (K0_i, K1_i) and nonce (c,d).

    Inputs to the S-box at position i are (IV_i, K0_i, K1_i ^ rc_i, N0_i, N1_i).
    """
    iv = _iv_bits()
    rc = _rc_bits(ASCON_RC1)
    tab = np.zeros((64, 2, 2, 2, 2, 5), np.uint8)
    for i in range(64):
        for a in range(2):
            for b in range(2):
                for c in range(2):
                    for d in range(2):
                        o = ascon_sbox_bits(np.uint8(iv[i]), np.uint8(a),
                                            np.uint8(b ^ rc[i]),
                                            np.uint8(c), np.uint8(d))
                        tab[i, a, b, c, d] = o
    return tab


def ascon_classes(nonce):
    """(n,16) nonce -> (n,64) class label 2*N0_i + N1_i for bit position i."""
    b0 = bits_msb64(nonce[:, 0:8])
    b1 = bits_msb64(nonce[:, 8:16])
    return (b0 * 2 + b1).astype(np.uint8)


# --------------------------------------------------------------------------- #
#  Xoodyak round-1 model
# --------------------------------------------------------------------------- #
# state before permutation #1 :  bytes 0..15 = K, 16..31 = N,
#                                byte 32 = 0x10, 33 = 0x01, 47 = 0x02
XOO_C = np.array([0x10, 0x01, 0, 0, 0, 0, 0, 0,
                  0, 0, 0, 0, 0, 0, 0, 0x02], np.uint8)
XOO_RC1 = 0x058


def xoo_const_bits():
    return lane_bits(XOO_C[None, :])[0]          # (4,32)


def xoo_known_bits(nonce):
    """Return n0,n1,n2 : the KNOWN (nonce-dependent) part of the three chi
    inputs B0,B1,B2 at every position (x,i).  Shapes (n,4,32)."""
    N = lane_bits(nonce).astype(np.uint8)        # (n,4,32) N(x)(i)

    def sh(a, dx, di):                           # a[(x-dx) mod 4][(i-di) mod 32]
        return np.roll(np.roll(a, dx, axis=1), di, axis=2)

    # E(x)(i) = P(x-1)(i-5) ^ P(x-1)(i-14) ; only the N part here
    En = sh(N, 1, 5) ^ sh(N, 1, 14)
    # B0(x)(i) = K(x)(i) ^ E(x)(i)  -> the nonce enters ONLY through E
    n0 = En
    # B1(x)(i)   = N(x-1)(i) ^ E(x-1)(i)
    n1 = sh(N, 1, 0) ^ sh(En, 1, 0)
    # B2(x)(i)   = C(x)(i-11) ^ E(x)(i-11)
    n2 = sh(En, 0, 11)
    return n0, n1, n2


def xoo_unknown_bits(key):
    """The three unknown bits u0,u1,u2 per position, as a function of the key.
    Ground truth only - used to score the attack.  Shapes (4,32)."""
    K = lane_bits(np.asarray(key, np.uint8)[None, :])[0]
    C = xoo_const_bits()
    rc = np.zeros((4, 32), np.uint8)
    rc[0] = [(XOO_RC1 >> i) & 1 for i in range(32)]      # iota hits lane x=0

    def sh(a, dx, di):
        return np.roll(np.roll(a, dx, axis=0), di, axis=1)

    KC = K ^ C
    EkC = sh(KC, 1, 5) ^ sh(KC, 1, 14)      # key/const part of E(x)(i)
    u0 = K ^ EkC ^ rc                       # B0 = K(x)(i)      ^ E(x)(i)   (+iota)
    u1 = sh(EkC, 1, 0)                      # B1 = N(x-1)(i)    ^ E(x-1)(i)
    u2 = sh(C, 0, 11) ^ sh(EkC, 0, 11)      # B2 = C(x)(i-11)   ^ E(x)(i-11)
    return u0.astype(np.uint8), u1.astype(np.uint8), u2.astype(np.uint8)


def xoo_hd_known_bits(nonce):
    """16-class label for the register-HD model of round 1.

    rho-east sends the chi outputs to these flip-flops:
        c0(x)(i) -> FF(0,x,i)      old value K(x)(i)      [unknown]
        c1(x)(i) -> FF(1,x,i+1)    old value N(x)(i+1)    [known]
        c2(x)(i) -> FF(2,x+2,i+8)  old value C(x+2)(i+8)  [known]
    so the HD bits are  hd0 = K(x)(i)^c0,  hd1 = N(x)(i+1)^c1,  hd2 = C'^c2.
    """
    n0, n1, n2 = xoo_known_bits(nonce)
    N = lane_bits(nonce).astype(np.uint8)
    m = np.roll(N, -1, axis=2)                    # N(x)(i+1)
    return (n0 + 2 * n1 + 4 * n2 + 8 * m).astype(np.uint8)


def xoo_hd_const():
    """C(x+2)(i+8) per position, the known offset of hd2.  (4,32)"""
    C = xoo_const_bits()
    return np.roll(np.roll(C, -2, axis=0), -8, axis=1)


def xoo_hd_table():
    """table[u0,u1,u2,k4, n0,n1,n2,m, j] -> the three HD bits (c2 offset added
    separately, per position)."""
    chi = xoo_chi_table()
    t = np.zeros((2, 2, 2, 2, 2, 2, 2, 2, 3), np.uint8)
    for u0 in range(2):
        for u1 in range(2):
            for u2 in range(2):
                for k4 in range(2):
                    for n0 in range(2):
                        for n1 in range(2):
                            for n2 in range(2):
                                for m in range(2):
                                    c = chi[u0, u1, u2, n0, n1, n2]
                                    t[u0, u1, u2, k4, n0, n1, n2, m] = [
                                        k4 ^ c[0], m ^ c[1], c[2]]
    return t


def xoo_k4_true(key):
    """K(x)(i) per position -- the 4th unknown of the HD model (= key bits)."""
    return lane_bits(np.asarray(key, np.uint8)[None, :])[0]


def xoo_unknown_bits_linear(K):
    """The LINEAR-in-K part of (u0,u1,u2); the constants C/rc are an affine
    offset and are dropped here so the GF(2) rank can be measured."""
    def sh(a, dx, di):
        return np.roll(np.roll(a, dx, axis=0), di, axis=1)
    Ek = sh(K, 1, 5) ^ sh(K, 1, 14)
    return K ^ Ek, sh(Ek, 1, 0), sh(Ek, 0, 11)


def xoo_chi_table():
    """table[u0,u1,u2, n0,n1,n2, j] = chi output bit j (j = plane 0,1,2)."""
    t = np.zeros((2, 2, 2, 2, 2, 2, 3), np.uint8)
    for u0 in range(2):
        for u1 in range(2):
            for u2 in range(2):
                for n0 in range(2):
                    for n1 in range(2):
                        for n2 in range(2):
                            b0, b1, b2 = u0 ^ n0, u1 ^ n1, u2 ^ n2
                            t[u0, u1, u2, n0, n1, n2] = [
                                b0 ^ ((1 - b1) & b2),
                                b1 ^ ((1 - b2) & b0),
                                b2 ^ ((1 - b0) & b1)]
    return t
