"""ASCON-128 round-1 REGISTER-HAMMING-DISTANCE attack.

The Xoodyak break came from modelling what the flip-flops actually do, not the
S-box net.  Same idea for ASCON.  With UROL=1 the 320-bit state register takes
the FULL round output (S-box then linear diffusion) every cycle, so

    x0_new[i] = S0[i] ^ S0[i+19] ^ S0[i+28]        (rotr 19, 28)
    old value at that flip-flop = IV[i]

S0[p] is the first S-box output bit at bit-position p, a function of
(IV_p, K0_p, K1_p^rc_p, N0_p, N1_p).  So one register bit of x0 depends on
   6 key bits   (K0,K1 at p in {i, i+19, i+28})   -> 64 hypotheses
   6 nonce bits (N0,N1 at the same three p)       -> 64 classes
Each solved position yields 6 DIRECT key bits, so 64 positions give 384
equations (multiplicity 3) in the 128 key unknowns.

The same 64 classes also determine the full 5-bit S-box column at all three
positions, so the combinational HW models come for free from the same
accumulator.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from . import engine as E

ROTS = (0, 19, 28)                       # the three bit-positions that mix into x0_new[i]


def positions(i):
    return [(i + r) % 64 for r in ROTS]


def build_tables():
    """V[i, h, c, m] for m = 0 (register HD bit of x0) and 1 (HW of the three
    5-bit S-box columns).  h = 6 key bits, c = 6 nonce bits, both ordered as
    (pos0, pos1, pos2) x (K0/N0, K1/N1)."""
    tab = E.ascon_sbox_table()               # (64, a,b, c,d, 5)
    iv = E._iv_bits()
    V = np.zeros((64, 64, 64, 2))
    for i in range(64):
        ps = positions(i)
        for h in range(64):
            for c in range(64):
                s0 = 0
                hw = 0.0
                for t, p in enumerate(ps):
                    a = (h >> (2 * t)) & 1
                    b = (h >> (2 * t + 1)) & 1
                    cc = (c >> (2 * t)) & 1
                    dd = (c >> (2 * t + 1)) & 1
                    out = tab[p, a, b, cc, dd]
                    s0 ^= int(out[0])
                    hw += float(out.sum())
                V[i, h, c, 0] = s0 ^ int(iv[i])      # register HD bit
                V[i, h, c, 1] = hw                   # combinational column HW
    return V


def truth_hyp(key):
    k0 = int.from_bytes(key[0:8], "big"); k1 = int.from_bytes(key[8:16], "big")
    th = np.zeros(64, int)
    for i in range(64):
        v = 0
        for t, p in enumerate(positions(i)):
            v |= ((k0 >> p) & 1) << (2 * t)
            v |= ((k1 >> p) & 1) << (2 * t + 1)
        th[i] = v
    return th


def classes(nonce):
    b0 = E.bits_msb64(nonce[:, 0:8]); b1 = E.bits_msb64(nonce[:, 8:16])
    n = nonce.shape[0]
    cls = np.zeros((n, 64), np.uint8)
    for i in range(64):
        for t, p in enumerate(positions(i)):
            cls[:, i] |= (b0[:, p] << (2 * t)).astype(np.uint8)
            cls[:, i] |= (b1[:, p] << (2 * t + 1)).astype(np.uint8)
    return cls


def selftest():
    from . import vecperm as V2
    key = bytes(range(16))
    rng = np.random.RandomState(3)
    non = rng.randint(0, 256, (256, 16)).astype(np.uint8)
    tr = V2.ascon_round_trace(key, non, 1)
    x0_old, x0_new = tr[0][0], tr[1][0]
    hd = ((x0_old ^ x0_new)[:, None] >> np.arange(64, dtype=np.uint64)[None, :]) \
        & np.uint64(1)
    Vt = build_tables(); th = truth_hyp(key); cls = classes(non)
    pred = np.stack([Vt[i, th[i], cls[:, i], 0] for i in range(64)], 1)
    ok = bool((pred == hd).all())
    print("ASCON register-HD model == RTL round-1 x0 register update :", ok)
    return ok


def rho_all(cnt, sums, st, stt, n, Vt):
    p = cnt / n
    mh = np.einsum("pk,phk->ph", p, Vt)
    vh = np.einsum("pk,phk->ph", p, Vt * Vt) - mh ** 2
    mt = st / n
    vt = np.maximum(stt / n - mt ** 2, 1e-30)
    cov = np.einsum("phk,pks->phs", Vt, sums) / n - mh[..., None] * mt[None, None, :]
    return cov / np.sqrt(np.maximum(vh, 1e-30)[..., None] * vt[None, None, :])


def score(acc_file):
    z = np.load(acc_file, allow_pickle=True)
    ns = sorted(int(x) for x in z["snap_ns"])
    key = bytes(z["key"]); win = tuple(int(x) for x in z["win"])
    Vt = build_tables(); th = truth_hyp(key)
    out = {}
    for n in ns:
        a = {f: z[f"s{n}_{f}"] for f in ("n", "sm", "smm", "st", "stt", "smt")}
        N = int(a["n"])
        cnt = a["sm"].reshape(64, 64); sums = a["smt"].reshape(64, 64, -1)
        res = {}
        for mi, mn in ((0, "regHD"), (1, "colHW")):
            R = rho_all(cnt, sums, a["st"], a["stt"], N, Vt[..., mi])
            A = np.abs(R)
            fb = A.reshape(64, -1).argmax(1)
            hb = fb // A.shape[2]
            ok = int((hb == th).sum())
            rc = np.abs(R[np.arange(64), th]).max(1)
            res[mn] = dict(correct=ok, of=64, chance=1.0,
                           binom_p=float(stats.binomtest(ok, 64, 1 / 64,
                                                         alternative="greater").pvalue),
                           max_rho_best=float(A.max()),
                           median_rho_correct=float(np.median(rc)),
                           hb=hb, R=R)
        # joint of the two models
        Rj = (rho_all(cnt, sums, a["st"], a["stt"], N, Vt[..., 0]) ** 2 +
              rho_all(cnt, sums, a["st"], a["stt"], N, Vt[..., 1]) ** 2)
        fb = Rj.reshape(64, -1).argmax(1)
        hbj = fb // Rj.shape[2]
        sbj = fb % Rj.shape[2]
        okj = int((hbj == th).sum())
        res["joint"] = dict(correct=okj, of=64,
                            binom_p=float(stats.binomtest(okj, 64, 1 / 64,
                                                          alternative="greater").pvalue),
                            hb=hbj, S=Rj[np.arange(64), :, sbj])
        out[N] = res
        print(f"  N={N:>9} regHD {res['regHD']['correct']}/64 "
              f"(chance 1, p={res['regHD']['binom_p']:.3g})  "
              f"colHW {res['colHW']['correct']}/64  joint {okj}/64 "
              f"rho_corr_med={res['regHD']['median_rho_correct']:.5f}", flush=True)
    return out, key, ns
