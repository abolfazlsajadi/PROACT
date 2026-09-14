"""Minimal AES CPA primitives used by the public acquisition runner."""
from __future__ import annotations

import numpy as np

SBOX = bytes.fromhex(
 "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
 "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
 "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
 "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
 "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
 "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
 "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
 "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
SB = np.frombuffer(SBOX, np.uint8)
INV = np.zeros(256, np.uint8)
for _i, _v in enumerate(SBOX):
    INV[_v] = _i
# ShiftRows index map: state_after_SR[i] = state_before_SR[SHIFT[i]]
SHIFT = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]
HW = np.array([bin(i).count("1") for i in range(256)], np.float32)
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]
G = np.arange(256)
def round10_key(k16):
    """AES-128 key schedule -> the round-10 subkey that a last-round attack finds."""
    k = list(k16)
    for r in range(10):
        t = k[12:16][:]
        t = t[1:] + t[:1]
        t = [SBOX[x] for x in t]
        t[0] ^= RCON[r]
        nk = []
        for i in range(4):
            p = k[i * 4:(i + 1) * 4]
            s = t if i == 0 else nk[(i - 1) * 4:i * 4]
            nk += [p[j] ^ s[j] for j in range(4)]
        k = nk
    return bytes(k)


# ------------------------------------------------------------ leakage models
def model_first_sbox(PT, CT, lo, hi, b):
    """Software AES, first round: HW(SBOX[pt ^ k]). Target = the real key."""
    v = PT[lo:hi, b].astype(np.int32)
    return HW[SB[np.bitwise_xor(v[:, None], G[None, :])]]


def model_last_hd(PT, CT, lo, hi, b):
    """Hardware AES, last round register transition:
           HW( INV_SBOX[ct[b] ^ k] ^ ct[SHIFT[b]] )
    Derivation: ct = AddRoundKey(ShiftRows(SubBytes(s9))), so
    ct[i] = SBOX[s9[SHIFT[i]]] ^ k10[i], hence INV_SBOX[ct[i]^k10[i]] = s9[SHIFT[i]],
    and the register holding that byte goes s9[SHIFT[i]] -> ct[SHIFT[i]].
    Target = the ROUND-10 subkey.

    NOTE for bytes 0, 4, 8, 12: SHIFT[b] == b (row 0 is not rotated), so the model
    degenerates into a function of the single ciphertext byte ct[b]. Those bytes are
    the ones most easily hijacked by an unrelated leak of the same byte -- which is
    exactly why POI restriction matters on hardware cores.
    """
    cb = CT[lo:hi, b].astype(np.int32)
    cs = CT[lo:hi, SHIFT[b]].astype(np.int32)
    s9 = INV[np.bitwise_xor(cb[:, None], G[None, :])].astype(np.int32)
    return HW[np.bitwise_xor(s9, cs[:, None])]


MODELS = {"first_sbox": (model_first_sbox, "key"),
          "last_hd":    (model_last_hd,    "rk10")}
def moving_avg(X, k, axis=-1):
    if k <= 1:
        return X
    pad = [(0, 0)] * X.ndim
    pad[axis] = (k, 0)
    c = np.cumsum(np.pad(X, pad, mode="edge"), axis=axis, dtype=np.float64)
    s1 = [slice(None)] * X.ndim
    s2 = [slice(None)] * X.ndim
    s1[axis] = slice(k, None)
    s2[axis] = slice(None, -k)
    return (c[tuple(s1)] - c[tuple(s2)]) / k


# ---------------------------------------------------------------- CPA engine
class CPA:
    """Streaming correlation accumulator: O(1) memory in the number of traces."""

    def __init__(self, samples, model, ma=1):
        self.S, self.ma = samples, ma
        self.fn = MODELS[model][0]
        self.Sht = np.zeros((16, 256, samples))
        self.Sh = np.zeros((16, 256))
        self.Sh2 = np.zeros((16, 256))
        self.St = np.zeros(samples)
        self.St2 = np.zeros(samples)
        self.n = 0

    def update(self, T, PT, CT, lo, hi):
        F = np.asarray(T[lo:hi], dtype=np.float32)
        if self.ma > 1:
            F = moving_avg(F, self.ma).astype(np.float32)
        self.St += F.sum(0, dtype=np.float64)
        self.St2 += (F.astype(np.float64) ** 2).sum(0)
        for b in range(16):
            h = self.fn(PT, CT, lo, hi, b)
            self.Sht[b] += (h.T @ F).astype(np.float64)
            self.Sh[b] += h.sum(0)
            self.Sh2[b] += (h ** 2).sum(0)
        self.n += hi - lo

    def rho(self, b):
        """|correlation| for every key hypothesis x every sample -> (256, S)."""
        n = self.n
        num = n * self.Sht[b] - self.Sh[b][:, None] * self.St[None, :]
        dh = np.sqrt(np.maximum(n * self.Sh2[b] - self.Sh[b] ** 2, 1e-30))[:, None]
        dt = np.sqrt(np.maximum(n * self.St2 - self.St ** 2, 1e-30))[None, :]
        return np.abs(num / (dh * dt))

    # ----- POI selection -----------------------------------------------------
    def poi_candidates(self, floor):
        """Where each byte peaks, and whether that peak means anything yet.

        A byte's argmax is only informative once its peak rises above the noise
        floor -- below it, argmax is just the maximum of 256 x S noise values and
        lands on a uniformly random sample. Returns (argmax, peak_rho, trusted).
        """
        arg, peak = [], []
        for b in range(16):
            R = self.rho(b)
            k = int(R.max(1).argmax())
            s = int(R[k].argmax())
            arg.append(s)
            peak.append(float(R[k, s]))
        return arg, peak, [p > floor for p in peak]

    def find_poi(self, floor=0.0):
        """Locate the leak WITHOUT using the key.

        Primary statistic, per sample s:

            score[s] = sum_b ( max_k |rho[b,k,s]| - mean_k |rho[b,k,s]| )

        i.e. how far the best key hypothesis stands out from the field of 256, summed
        over all 16 bytes. At a real leak every byte has ONE hypothesis pulling away,
        so the sum is large; at a nuisance leak only the bytes that happen to alias
        onto it contribute. The mean subtraction is what makes this work -- it removes
        any per-sample gain or noise term that lifts ALL hypotheses together, which is
        precisely what a big unrelated leak does.

        Do NOT use max|rho| to pick a POI on this chip. It ranks the ciphertext-readout
        ghosts ABOVE the real leak (measured: samples 289/325/253/217 all outrank 57),
        and its top pick recovers zero key bytes. Trace variance is worse still -- the
        real POI lands at rank 417 of 595.

        Cross-checked with a plurality vote over per-byte argmax, counting only bytes
        whose peak clears `floor`. The vote is the weaker of the two (it needs more
        traces to settle) so it is reported, not relied upon.

        Returns a dict: poi, z (confidence), votes, n_trusted, argmax, score.
        """
        score = np.zeros(self.S)
        arg, peak = [], []
        for b in range(16):
            R = self.rho(b)
            score += R.max(0) - R.mean(0)
            k = int(R.max(1).argmax())
            s = int(R[k].argmax())
            arg.append(s)
            peak.append(float(R[k, s]))
        poi = int(score.argmax())
        rest = np.delete(score, poi)
        z = float((score[poi] - rest.mean()) / (rest.std() + 1e-30))
        trusted = [p > floor for p in peak]
        voters = [s for s, t in zip(arg, trusted) if t]
        return dict(poi=poi, z=z, votes=voters.count(poi), n_trusted=len(voters),
                    argmax=arg, peak=peak, trusted=trusted, score=score)

    def evaluate(self, target, poi=None, half=0, per_byte_poi=None):
        """Score every byte. Returns (hits, rows, guessing_entropy_bits).

        poi          : single shared sample index (hardware cores), or None = all samples
        half         : half-width of the window around the POI
        per_byte_poi : list of 16 sample indices (software cores, byte-serial)
        """
        hits, rows, ranks = 0, [], []
        for b in range(16):
            R = self.rho(b)
            if per_byte_poi is not None and per_byte_poi[b] is not None:
                c = per_byte_poi[b]
                sl = slice(max(0, c - half), min(self.S, c + half + 1))
            elif poi is not None:
                sl = slice(max(0, poi - half), min(self.S, poi + half + 1))
            else:
                sl = slice(None)
            pk = R[:, sl].max(1)
            g = int(pk.argmax())
            ok = g == target[b]
            hits += ok
            srt = np.sort(pk)
            rank = int((pk > pk[target[b]]).sum()) + 1
            ranks.append(rank)
            rows.append(dict(byte=b, guess=g, true=target[b], rho=float(pk[target[b]]),
                             rho_best=float(pk[g]),
                             margin=float(srt[-1] / max(srt[-2], 1e-30)),
                             rank=rank, ok=bool(ok)))
        ge = float(np.log2(np.prod([float(r) for r in ranks])))
        return hits, rows, ge


def detection_floor(n, S, k=256):
    """Smallest |rho| distinguishable from noise when the LARGEST of k*S
    correlation estimates is taken -- k hypotheses x S samples.

    Under H0 each rho-hat is ~ N(0, 1/n), so the maximum of M = k*S of them sits
    at about sqrt(2 ln M)/sqrt(n).  A statistic at or below this is a FAILED
    MEASUREMENT, not a small signal.

    k defaults to 256 (AES byte CPA) so every existing caller is unchanged. Pass
    the real hypothesis count for anything else -- the AEAD cores search 4 or 8
    hypotheses per position, not 256 -- and pass the number of samples the search
    ACTUALLY maximised over, not the trace length.  Both factors matter: the bar
    moves with ln(k*S), so quoting one search's floor next to another search's rho
    is how a pure-noise maximum gets reported as a signal.

    M is the multiplicity of the specific statistic being judged, so it differs
    within one attack: a per-position best over k hypotheses x S samples uses
    M = k*S, the same attack's global maximum over P positions uses M = P*k*S,
    and the correct hypothesis' own peak (hypothesis fixed, max over samples
    only) uses M = S.  Compute the one that matches the number you are printing.

    This is the EXPECTED null maximum, not a p<0.05 threshold: noise clears it
    about half the time, so "above the floor" means "not obviously noise", never
    "significant".  Below the floor is the strong direction -- a failed
    measurement, which must not be extrapolated from or compared across platforms.
    """
    return float(np.sqrt(2 * np.log(k * S)) / np.sqrt(n))
