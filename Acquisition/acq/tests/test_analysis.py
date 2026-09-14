"""Hardware-free tests for TVLA, exact-dataset CPA, and AEAD adapters."""
import os
import shutil
import tempfile

import numpy as np

from acq.analysis import (_load_aes_engine, _round0_from_round10, run_cpa,
                          run_tvla)
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.store import TraceStore


ok = 0
fail = 0


class CheckFailure(RuntimeError):
    """Make a failed legacy check fail under both pytest and ``python -m``."""


def check(condition, name):
    global ok, fail
    if condition:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")
        raise CheckFailure(name)


def _with_data_dir(fn):
    import acq.config as config
    directory = tempfile.mkdtemp(prefix="proact-analysis-test-")
    old = config.DATA
    config.DATA = directory
    try:
        fn(directory)
    finally:
        config.DATA = old
        shutil.rmtree(directory, ignore_errors=True)


def test_round_key_inverse():
    _, _, round10_key = _load_aes_engine()
    for key in (bytes(16), bytes(range(16)), bytes(reversed(range(16)))):
        check(_round0_from_round10(round10_key(key)) == key,
              f"AES round-10 inversion {key.hex()[:8]}")


def test_tvla_and_group1_cpa():
    def body(_directory):
        from proact_host.validation import aes128_encrypt_block

        cfg = AcqConfig(target="aes1", traces=44, tvla=True, tvla_per_class=22,
                        auto_cpa=True, tvla_order="block", tvla_block_size=10,
                        suffix="_analysis_ut", seed=91)
        gen = InputGen(cfg)
        store = TraceStore(cfg, samples=8, out_len=16, key_varies=False)
        rng = np.random.default_rng(7)
        for i in range(40):
            group = gen.tvla_group(i)
            inp = gen.inp(i)
            wave = rng.normal(0, 0.002, 8).astype(np.float32)
            if group == 0:
                wave[3] += 0.02
            store.append(i, wave, gen.key(i), inp,
                         aes128_encrypt_block(gen.key(i), inp),
                         group=group, block=gen.tvla_block(i))
        # Poison allocated rows after done. Neither analysis may consume them.
        store.mm[40:] = np.int16(32000)
        store.checkpoint(40)
        store.close()

        tvla = run_tvla(cfg.base, chunk=7)
        check(tvla["source"]["done"] == 40, "TVLA honors authoritative done")
        check(tvla["groups"]["n_fixed"] == tvla["groups"]["n_random"] == 20,
              "TVLA uses exact stored group counts")
        check(tvla["peak_sample"] == 3 and tvla["max_abs_t"] > 4.5,
              "TVLA detects the injected first-order sample")
        check(tvla["peak_coordinate"]["nominal_target_cycle_from_capture_start"] == 0.75,
              "TVLA reports nominal target-cycle coordinates")
        check(all(os.path.exists(path) for path in tvla["outputs"].values()),
              "TVLA saves NPZ CSV and JSON")
        with np.load(tvla["outputs"]["arrays"]) as arrays:
            check((arrays["variance_fixed"] >= 0).all() and
                  (arrays["variance_random"] >= 0).all(),
                  "TVLA clips round-off variance below zero")

        cpa = run_cpa(cfg.base, "aes1", random_group_only=True)
        check(cpa["source"]["selected_n"] == 20,
              "TVLA+CPA uses only random-input group")
        check(cpa["source"]["done"] == 40,
              "CPA honors authoritative done")
        check(cpa["minimum_trace_count_claimed"] is False,
              "CPA does not claim a minimum from one final point")
        with np.load(cpa["outputs"]["arrays"]) as arrays:
            check(arrays["peak_sample_by_guess"].shape == (16, 256),
                  "AES CPA saves every hypothesis peak sample")

    _with_data_dir(body)


def test_aead_positional_adapters():
    def body(_directory):
        # Importing the target module installs the selected checkout's host path.
        from acq.targets.aead import AD_FIXED, PT_FIXED
        from proact_host import aead_soft

        rng = np.random.default_rng(19)
        for core, encrypt in (("ascon", aead_soft.ascon128_encrypt),
                              ("xoodyak", aead_soft.xoodyak_encrypt)):
            cfg = AcqConfig(target=core, traces=24, auto_cpa=True,
                            suffix=f"_{core}_adapter_ut", seed=111)
            gen = InputGen(cfg)
            store = TraceStore(cfg, samples=65, out_len=32, key_varies=False)
            for i in range(cfg.traces):
                nonce = gen.inp(i)
                ct, tag = encrypt(gen.key(i), nonce, AD_FIXED, PT_FIXED)
                wave = rng.normal(0, 0.002, 65).astype(np.float32)
                store.append(i, wave, gen.key(i), nonce, ct + tag)
            store.checkpoint(cfg.traces)
            store.close()
            report = run_cpa(cfg.base, core)
            check(report["source"]["selected_n"] == cfg.traces,
                  f"{core} adapter streams exact native rows")
            check(report["result_kind"] == "positional_hypothesis_evaluation",
                  f"{core} result is labelled positional")
            check(report["full_key_recovery_attempted"] is False and
                  report["full_key_recovered"] is None,
                  f"{core} does not overclaim full-key recovery")
            check(report["ciphertext_reference"]["all_matched"] is True,
                  f"{core} ciphertext reference hard-check passes")
            check(report["source"]["window"] == [20, 64],
                  f"{core} uses validated specialized-model window")
            with np.load(report["outputs"]["diagnostic_arrays"]) as arrays:
                expected_shape = (64, 64) if core == "ascon" else (128, 16)
                check(arrays["peak_sample_by_guess"].shape == expected_shape,
                      f"{core} saves every positional-hypothesis peak sample")

    _with_data_dir(body)


if __name__ == "__main__":
    for test in (test_round_key_inverse, test_tvla_and_group1_cpa,
                 test_aead_positional_adapters):
        print(f"\n[{test.__name__}]")
        test()
    print(f"\n{ok} passed, {fail} failed")
    raise SystemExit(1 if fail else 0)
