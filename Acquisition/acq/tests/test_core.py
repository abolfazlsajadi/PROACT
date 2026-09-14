"""Unit tests for the hardware-independent core (L1.10): config, inputs, store,
checkpoint. Run: $HOME/.proact-venv/bin/python -m acq.tests.test_core
"""
import os, sys, tempfile, shutil
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from acq.config import AcqConfig, parse_count
from acq.inputs import InputGen
from acq.store import TraceStore, SCALE
from acq.native import open_native, SCHEMA
from acq import checkpoint as ckpt
from acq.run import _warmup
from acq.validate import preflight

ok = 0; fail = 0


class CheckFailure(RuntimeError):
    """Make a failed legacy check fail under both pytest and ``python -m``."""


def check(cond, name):
    global ok, fail
    if cond: ok += 1; print(f"  PASS {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")
        # RuntimeError is deliberate: several negative-path tests expect and
        # catch AssertionError/SystemExit/ValueError around the operation under
        # test.  Raising one of those here could turn check(False) into a pass.
        raise CheckFailure(name)


def test_parse():
    check(parse_count("10000") == 10000, "parse plain")
    check(parse_count("100k") == 100_000, "parse k")
    check(parse_count("5M") == 5_000_000, "parse M")
    try:
        AcqConfig(target="aes1", traces=1, chunk=0).validate()
        check(False, "zero checkpoint chunk refused")
    except ValueError:
        check(True, "zero checkpoint chunk refused")


def test_inputs_reproducible():
    cfg = AcqConfig(target="aes1", traces=100, key_policy="random",
                    input_policy="random", seed=12345)
    g1 = InputGen(cfg); g2 = InputGen(cfg)
    check(g1.inp(7) == g2.inp(7), "input reproducible by index")
    check(g1.key(7) == g2.key(7), "key reproducible by index")
    check(g1.inp(7) != g1.inp(8), "different index -> different input")
    check(g1.key(7) != g1.inp(7), "key and input differ")
    cfgf = AcqConfig(target="aes1", traces=10, key_policy="fixed", input_policy="fixed",
                     fixed_input=bytes(range(16)))
    gf = InputGen(cfgf)
    check(gf.inp(0) == gf.inp(9) == bytes(range(16)), "fixed input constant")
    check(gf.key(0) == gf.key(9) == bytes(range(16)), "fixed key constant")


def test_tvla_schedule():
    cfg = AcqConfig(target="aes1", traces=26, tvla=True, tvla_per_class=13,
                    tvla_order="block", tvla_block_size=10, seed=2026)
    cfg.validate()
    g1 = InputGen(cfg); g2 = InputGen(cfg)
    labels = [g1.tvla_group(i) for i in range(cfg.traces)]
    check(labels.count(0) == labels.count(1) == 13, "TVLA exact global balance")
    check(labels == [g2.tvla_group(i) for i in range(cfg.traces)],
          "TVLA schedule deterministic")
    check(all(labels[s:s + 10].count(0) == labels[s:s + 10].count(1)
              for s in range(0, 26, 10)), "TVLA every full/partial block balanced")
    check(all(g1.inp(i) == cfg.fixed_input for i, x in enumerate(labels) if x == 0),
          "TVLA group 0 uses fixed input")
    check(len({g1.inp(i) for i, x in enumerate(labels) if x == 1}) == 13,
          "TVLA group 1 inputs vary")

    alt_cfg = AcqConfig(target="aes1", traces=10, tvla=True, tvla_per_class=5,
                        tvla_order="alternate", seed=7)
    alt = InputGen(alt_cfg)
    check([alt.tvla_group(i) for i in range(10)] == [0, 1] * 5,
          "strict TVLA alternates fixed/random")


class _FakeWarmTarget:
    out_len = 16
    def __init__(self):
        self.inputs = []

    def run(self, key, inp):
        self.inputs.append(inp)
        return bytes(16)

    def expected(self, key, inp):
        return bytes(16)

    def close(self):
        pass

    def open(self):
        pass

    def select(self, key):
        pass

    def configure_trigger(self):
        pass


class _FakeUI:
    def note(self, message):
        pass


def test_tvla_warmup():
    cfg = AcqConfig(target="aes1", traces=20, tvla=True, tvla_per_class=10,
                    tvla_order="block", seed=8)
    gen = InputGen(cfg); target = _FakeWarmTarget()
    result = _warmup(target, gen, 7, cfg.traces, _FakeUI())
    check(result["completed"] == 7 and len(target.inputs) == 7,
          "warm-up counts successful discarded operations")
    check(target.inputs[0] == cfg.fixed_input and target.inputs[1] != cfg.fixed_input,
          "TVLA warm-up directly exercises fixed/random beyond stored indices")


class _PreflightTarget:
    def run(self, key, inp):
        return bytes(a ^ b for a, b in zip(key, inp))

    def expected(self, key, inp):
        return bytes(a ^ b for a, b in zip(key, inp))


class _PreflightBackend:
    def __init__(self):
        self.count = 0

    def arm(self):
        pass

    def capture(self, timeout=3.0):
        self.count += 1
        return np.array([0.001 * self.count, 0.01, -0.01, 0.02], np.float32)

    @property
    def trig_count(self):
        return 4


def test_tvla_preflight_groups():
    cfg = AcqConfig(target="aes1", traces=200, tvla=True, tvla_per_class=100,
                    tvla_order="block", seed=4, samples=4)
    result = preflight(_PreflightTarget(), _PreflightBackend(), InputGen(cfg), cfg, n=20)
    check(result["samples"] == 4, "TVLA preflight explicitly tests both groups")


def test_preflight_all_key_input_policies():
    for key_policy, input_policy in (
            ("fixed", "fixed"), ("fixed", "random"),
            ("random", "fixed"), ("random", "random")):
        cfg = AcqConfig(target="aes1", traces=20,
                        key_policy=key_policy, input_policy=input_policy,
                        seed=41, samples=4)
        result = preflight(_PreflightTarget(), _PreflightBackend(),
                           InputGen(cfg), cfg, n=8)
        check(result["samples"] == 4,
              f"preflight accepts key={key_policy}, input={input_policy}")


def test_store_resume():
    d = tempfile.mkdtemp()
    try:
        import acq.config as C
        old = C.DATA; C.DATA = d
        cfg = AcqConfig(target="aes1", traces=50, key_policy="fixed",
                        input_policy="random", suffix="_ut", seed=7)
        gen = InputGen(cfg)
        st = TraceStore(cfg, samples=30, out_len=16, key_varies=False)
        rng = np.random.default_rng(1)
        for i in range(20):
            w = rng.standard_normal(30).astype(np.float32) * 0.1
            st.append(i, w, gen.key(i), gen.inp(i), bytes(range(16)))
        st.checkpoint(20); st.close()
        check(os.path.exists(cfg.base + "_meta.npz"), "meta written")
        # reopen -> resume at 20
        st2 = TraceStore(cfg, samples=30, out_len=16, key_varies=False)
        check(st2.done == 20, "resume at 20")
        ckpt.verify_resume(st2, gen)     # must not raise
        check(True, "resume integrity ok")
        # stored input matches regenerated
        check(list(st2.INP[5]) == list(gen.inp(5)), "stored input == regen")
        check(st2.GRP.shape == (50,) and st2.BLK.shape == (50,) and st2.TS.shape == (50,),
              "per-row group/block/timestamp arrays resume")
        # mismatch guard
        cfg2 = AcqConfig(target="aes1", traces=50, key_policy="fixed",
                         input_policy="random", suffix="_ut", seed=7)
        try:
            TraceStore(cfg2, samples=40, out_len=16, key_varies=False)  # wrong samples
            check(False, "mismatch refused")
        except SystemExit:
            check(True, "mismatch refused")
        C.DATA = old
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_atomic_checkpoint():
    d = tempfile.mkdtemp()
    try:
        import acq.config as C
        old = C.DATA; C.DATA = d
        cfg = AcqConfig(target="aes1", traces=10, suffix="_at")
        st = TraceStore(cfg, samples=8, out_len=16, key_varies=False)
        st.append(0, np.zeros(8, np.float32), bytes(16), bytes(16), bytes(16))
        try:
            st.append(2, np.zeros(8, np.float32), bytes(16), bytes(16), bytes(16))
            check(False, "out-of-order append refused")
        except ValueError:
            check(True, "out-of-order append refused")
        try:
            st.checkpoint(2)
            check(False, "checkpoint beyond done refused")
        except ValueError:
            check(True, "checkpoint beyond done refused")
        try:
            st.checkpoint(1, extra={"done": 9})
            check(False, "checkpoint extra cannot replace done")
        except ValueError:
            check(True, "checkpoint extra cannot replace done")
        st.checkpoint(1)
        check(not os.path.exists(cfg.base + "_meta.npz.tmp"), "no temp left after checkpoint")
        m = np.load(cfg.base + "_meta.npz"); check(int(m["done"]) == 1, "done persisted")
        C.DATA = old
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_v2_resume_safety_and_legacy_migration():
    d = tempfile.mkdtemp()
    try:
        import acq.config as C
        old_data = C.DATA; C.DATA = d
        cfg = AcqConfig(target="aes1", traces=10, tvla=True, tvla_per_class=5,
                        suffix="_v2", seed=77, gain_db=25.0)
        gen = InputGen(cfg)
        st = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
        for i in range(6):
            st.append(i, np.full(4, i / 1000, np.float32), gen.key(i), gen.inp(i),
                      bytes([i]) * 16, group=gen.tvla_group(i),
                      block=gen.tvla_block(i))
        st.mm[8] = 32000                  # dirty but non-authoritative tail row
        st.checkpoint(6); st.close()
        with np.load(cfg.base + "_meta.npz", allow_pickle=True) as meta:
            check(str(meta["schema"].item()) == SCHEMA,
                  "v2 native schema recorded")
            check("input" not in meta.files and "out" not in meta.files and
                  os.path.getsize(cfg.base + "_meta.npz") < 100_000,
                  "v2 checkpoint metadata stays small")
        with open_native(cfg.base) as native:
            check(native.traces.shape == (6, 4) and native["input"].shape == (10, 16),
                  "native loader clips traces to done and opens sidecars")
        resumed = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
        ckpt.verify_resume(resumed, gen)
        check(resumed.done == 6, "v2 resume validates stored TVLA identities")
        resumed.GRP[0] = 9
        try:
            ckpt.verify_resume(resumed, gen)
            check(False, "invalid TVLA group refused")
        except SystemExit:
            check(True, "invalid TVLA group refused")
        resumed.GRP[0] = gen.tvla_group(0); resumed.checkpoint(6); resumed.close()

        with open(cfg.base + "_meta.npz", "rb") as stream:
            before = stream.read()
        changed = AcqConfig(target="aes1", traces=10, tvla=True, tvla_per_class=5,
                            suffix="_v2", seed=77, gain_db=30.0)
        try:
            TraceStore(changed, samples=4, out_len=16, key_varies=False)
            check(False, "capture-signature change refused")
        except SystemExit:
            with open(cfg.base + "_meta.npz", "rb") as stream:
                unchanged = stream.read() == before
            check(unchanged,
                  "capture-signature change refused without metadata rewrite")

        orphan = AcqConfig(target="aes1", traces=2, suffix="_orphan", seed=1)
        np.lib.format.open_memmap(orphan.base + "_traces.npy", mode="w+",
                                  dtype=np.int16, shape=(2, 4)).flush()
        try:
            TraceStore(orphan, samples=4, out_len=16, key_varies=False)
            check(False, "orphan native pair refused")
        except SystemExit:
            check(True, "orphan native pair refused")

        legacy = AcqConfig(target="aes1", traces=8, suffix="_legacy", seed=12)
        lg = InputGen(legacy)
        np.lib.format.open_memmap(legacy.base + "_traces.npy", mode="w+",
                                  dtype=np.int16, shape=(8, 4)).flush()
        legacy_meta = legacy.to_meta()
        legacy_meta.update(done=3, n_alloc=8, samples=4, scale=SCALE,
                           key=np.frombuffer(legacy.fixed_key, np.uint8),
                           input=np.asarray([list(lg.inp(i)) for i in range(8)], np.uint8),
                           out=np.zeros((8, 16), np.uint8))
        np.savez(legacy.base + "_meta.npz", **legacy_meta)
        migrated = TraceStore(legacy, samples=4, out_len=16, key_varies=False)
        check(migrated.done == 3 and os.path.exists(legacy.base + "_input.npy"),
              "partial embedded legacy dataset migrates once to sidecars")
        migrated.close()
        with np.load(legacy.base + "_meta.npz", allow_pickle=True) as meta:
            check(str(meta["schema"].item()) == SCHEMA and "input" not in meta.files,
                  "legacy migration commits v2 metadata last")
        with np.load(legacy.base + "_meta.npz", allow_pickle=True) as meta:
            future = {name: meta[name] for name in meta.files}
        future["schema"] = "future-v99"
        np.savez(legacy.base + "_meta.npz", **future)
        try:
            TraceStore(legacy, samples=4, out_len=16, key_varies=False)
            check(False, "unknown native schema refused")
        except SystemExit:
            check(True, "unknown native schema refused")
        C.DATA = old_data
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_checkpoint_scaling_smoke():
    d = tempfile.mkdtemp()
    try:
        import acq.config as C
        old_data = C.DATA; C.DATA = d
        cfg = AcqConfig(target="aes1", traces=200_000, suffix="_scale", seed=3)
        gen = InputGen(cfg)
        st = TraceStore(cfg, samples=1, out_len=16, key_varies=False)
        initial = os.path.getsize(cfg.base + "_meta.npz")
        for i in range(2):
            st.append(i, np.array([0.001], np.float32), gen.key(i), gen.inp(i),
                      bytes(16))
            st.checkpoint(i + 1)
        final = os.path.getsize(cfg.base + "_meta.npz")
        st.close()
        check(max(initial, final) < 100_000 and abs(initial - final) < 2_000,
              "200k-row checkpoint metadata remains constant-size")
        C.DATA = old_data
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for f in (test_parse, test_inputs_reproducible, test_tvla_schedule,
              test_tvla_warmup, test_tvla_preflight_groups,
              test_preflight_all_key_input_policies,
              test_store_resume, test_atomic_checkpoint,
              test_v2_resume_safety_and_legacy_migration,
              test_checkpoint_scaling_smoke):
        print(f"\n[{f.__name__}]"); f()
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)
