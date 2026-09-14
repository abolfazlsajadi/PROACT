"""Xoodyak round-1 REGISTER-HAMMING-DISTANCE attack + full key reconstruction.

Improvement over the chi-output (HW) attack: the 384 flip-flops are rewritten
every permutation cycle, so what leaks is HD(A_old, A_new).  rho-east tells us
exactly which old bit each chi output overwrites:

    c0(x)(i) -> FF(0,x,i)      old K(x)(i)      unknown  (a KEY BIT, directly)
    c1(x)(i) -> FF(1,x,i+1)    old N(x)(i+1)    known
    c2(x)(i) -> FF(2,x+2,i+8)  old C(x+2)(i+8)  known

Hypothesis per position: (u0,u1,u2,k4) = 16;  classes (n0,n1,n2,m) = 16.
k4 = K(x)(i) is a key bit itself, so the attack yields 512 GF(2) equations
(384 u-bits + 128 direct key bits) in the 128 key unknowns.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from . import engine as E
from . import vecperm as V

def hd_value_tensor():
    """V[p, h, c, j] -> predicted HD bit j.  h=(u0,u1,u2,k4), c=(n0,n1,n2,m)."""
    tab = E.xoo_hd_table()                        # (2,2,2,2, 2,2,2,2, 3)
    off = E.xoo_hd_const().reshape(128)           # per-position offset of hd2
    Vt = np.zeros((128, 16, 16, 3))
    for h in range(16):
        u0, u1, u2, k4 = h & 1, (h >> 1) & 1, (h >> 2) & 1, (h >> 3) & 1
        for c in range(16):
            n0, n1, n2, m = c & 1, (c >> 1) & 1, (c >> 2) & 1, (c >> 3) & 1
            v = tab[u0, u1, u2, k4, n0, n1, n2, m].astype(float)
            Vt[:, h, c, 0] = v[0]
            Vt[:, h, c, 1] = v[1]
            Vt[:, h, c, 2] = np.abs(v[2] - off)   # xor with the per-pos constant
    return Vt


def truth_hyp(key):
    u0, u1, u2 = E.xoo_unknown_bits(np.frombuffer(key, np.uint8))
    k4 = E.xoo_k4_true(np.frombuffer(key, np.uint8))
    return (u0 + 2 * u1 + 4 * u2 + 8 * k4).reshape(128)


def selftest(key=bytes(range(16)), n=256):
    rng = np.random.RandomState(1)
    non = rng.randint(0, 256, (n, 16)).astype(np.uint8)
    tr = V.xoodoo_round_trace(key, non, 1)
    old, new = tr[0], tr[1]
    hd = np.stack([np.stack([((old[4 * y + x] ^ new[4 * y + x]) >> np.uint32(i))
                             & np.uint32(1) for i in range(32)], 1)
                   for y in range(3) for x in range(4)], 1)     # (n,12,32)
    hd = hd.reshape(n, 3, 4, 32)
    cls = E.xoo_hd_known_bits(non).reshape(n, 128)
    Vt = hd_value_tensor()
    th = truth_hyp(key)
    pred = np.zeros((n, 128, 3))
    for p in range(128):
        pred[:, p] = Vt[p, th[p], cls[:, p]]
    # real HD bits belonging to position p (chi output -> its destination FF)
    real = np.zeros((n, 128, 3))
    for x in range(4):
        for i in range(32):
            p = x * 32 + i
            real[:, p, 0] = hd[:, 0, x, i]
            real[:, p, 1] = hd[:, 1, x, (i + 1) % 32]
            real[:, p, 2] = hd[:, 2, (x + 2) % 4, (i + 8) % 32]
    ok = bool((pred == real).all())
    print("XOODYAK register-HD model == RTL round-1 register update :", ok)
    return ok


# --------------------------------------------------------------- accumulate -- #
def rho_all(cnt, sums, st, stt, n, Vt):
    p = cnt / n
    mh = np.einsum("pk,phk->ph", p, Vt)
    vh = np.einsum("pk,phk->ph", p, Vt * Vt) - mh ** 2
    mt = st / n
    vt = np.maximum(stt / n - mt ** 2, 1e-30)
    cov = np.einsum("phk,pks->phs", Vt, sums) / n - mh[..., None] * mt[None, None, :]
    return cov / np.sqrt(np.maximum(vh, 1e-30)[..., None] * vt[None, None, :])


def score(acc_file, n_pick=None):
    z = np.load(acc_file, allow_pickle=True)
    ns = sorted(int(x) for x in z["snap_ns"])
    key = bytes(z["key"]); win = tuple(int(x) for x in z["win"])
    out = {}
    Vt = hd_value_tensor()
    th = truth_hyp(key)
    for n in ns:
        a = {f: z[f"s{n}_{f}"] for f in ("n", "sm", "smm", "st", "stt", "smt")}
        N = int(a["n"])
        cnt = a["sm"].reshape(128, 16)
        sums = a["smt"].reshape(128, 16, -1)
        R = [rho_all(cnt, sums, a["st"], a["stt"], N, Vt[..., j]) for j in range(3)]
        SCj = sum(r ** 2 for r in R)                       # (128,16,S) joint
        best = SCj.reshape(128, -1).argmax(1)
        hb = best // SCj.shape[2]
        sb = best % SCj.shape[2]
        ok = int((hb == th).sum())
        Sfull = SCj[np.arange(128), :, sb]                 # (128,16) at best sample
        out[N] = dict(hyp_correct=ok, of=128,
                      binom_p=float(stats.binomtest(ok, 128, 1 / 16,
                                                    alternative="greater").pvalue),
                      max_rho=float(np.sqrt(SCj.max())),
                      best_sample=int(np.bincount(sb).argmax()) + win[0],
                      S=Sfull, hb=hb, key=key)
        print(f"  N={N:>9} HD-joint 16-ary correct {ok}/128 (chance 8, "
              f"p={out[N]['binom_p']:.3g}) rho~{out[N]['max_rho']:.5f}", flush=True)
    return out, key, ns
