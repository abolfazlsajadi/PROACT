"""Hardware-free checks for the dedicated masked Sw-RV target."""
from __future__ import annotations

import hashlib
import csv
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

import acq.config as config_module
from acq.analysis import _load_aes_engine, run_cpa
from acq.config import AcqConfig
from acq.exporters import export_csv, export_h5
from acq.inputs import InputGen
from acq.native import _firmware_identity, capture_signature
from acq.store import TraceStore
from acq.targets import make_target
from acq.targets.sw_rv_masked import MaskedSwRVTarget


KEY = bytes(range(16))
PT = bytes(range(16, 32))
CT = bytes.fromhex("07feef74e1d5036e900eee118e949293")


class FakeTransport:
    def __init__(self):
        self.events = []

    def select(self, core):
        self.events.append(("select", core))

    def set_decrypt(self, value):
        self.events.append(("decrypt", value))

    def set_key(self, value):
        self.events.append(("key", bytes(value)))

    def seed_rng(self, value):
        self.events.append(("seed", int(value)))

    def load_swrv_program(self, imem, dmem, base):
        self.events.append(("load", len(imem), len(dmem), int(base)))

    def set_plaintext(self, value):
        self.events.append(("plaintext", bytes(value)))

    def run_and_read(self):
        self.events.append(("run",))
        return 0, CT


class MaskedSwRVTests(unittest.TestCase):
    def make_target(self):
        cfg = SimpleNamespace(key_policy="fixed", trigger_mode="auto")
        target = MaskedSwRVTarget(cfg)
        target.t = FakeTransport()
        return target

    def test_registry_selects_distinct_masked_adapter(self):
        target = make_target(SimpleNamespace(target="sw_rv_masked"))
        self.assertIsInstance(target, MaskedSwRVTarget)
        masked = capture_signature(
            AcqConfig(target="sw_rv_masked", traces=1, seed=1).validate(), 4, 16)
        plain = capture_signature(
            AcqConfig(target="aes1", traces=1, seed=1).validate(), 4, 16)
        self.assertIn("target_rng_reseed_policy", masked)
        self.assertNotIn("target_rng_reseed_policy", plain)

    def test_configured_images_have_pinned_identity(self):
        expected = MaskedSwRVTarget.firmware_sha256
        for name, wanted in expected.items():
            with open(os.path.join(MaskedSwRVTarget.firmware_dir, name), "rb") as stream:
                self.assertEqual(hashlib.sha256(stream.read()).hexdigest(), wanted)
        identity = _firmware_identity("sw_rv_masked")
        self.assertEqual(identity["sw_rv_masked_imem"]["host_artifact_sha256"],
                         expected["sw_rv_imem.vmem"])
        self.assertEqual(identity["sw_rv_masked_dmem"]["host_artifact_sha256"],
                         expected["sw_rv_dmem.vmem"])

    def test_missing_masked_image_is_refused_before_hardware(self):
        with mock.patch.object(MaskedSwRVTarget, "firmware_dir", "/missing/masked-fw"):
            with self.assertRaisesRegex(RuntimeError, "firmware unavailable"):
                MaskedSwRVTarget(SimpleNamespace())

    def test_rng_enabled_before_boot_and_reseeded_before_each_operation(self):
        target = self.make_target()
        with mock.patch("acq.targets.sw_rv_masked.secrets.randbits",
                        side_effect=[0x11111111, 0x22222222, 0x33333333]):
            target.select(KEY)
            self.assertEqual(target.run(KEY, PT), CT)
            self.assertEqual(target.run(KEY, PT), CT)
        events = target.t.events
        load_index = next(i for i, event in enumerate(events) if event[0] == "load")
        self.assertIn(("seed", 0x11111111), events[:load_index])
        self.assertEqual([event[1] for event in events if event[0] == "seed"],
                         [0x11111111, 0x22222222, 0x33333333])
        self.assertEqual(events[load_index][0], "load")
        self.assertGreater(events[load_index][1], 0)
        self.assertGreater(events[load_index][2], 0)
        self.assertEqual(events[load_index][3], 0x08100000)
        run_indices = [i for i, event in enumerate(events) if event[0] == "run"]
        for index, seed in zip(run_indices, (0x22222222, 0x33333333)):
            self.assertEqual(events[index - 2], ("seed", seed))
            self.assertEqual(events[index - 1], ("plaintext", PT))

    def test_zero_entropy_draw_is_mapped_to_nonzero_lfsr_seed(self):
        with mock.patch("acq.targets.sw_rv_masked.secrets.randbits", return_value=0):
            self.assertEqual(MaskedSwRVTarget._fresh_rng_seed(), 1)

    def test_output_reference_is_software_aes(self):
        self.assertEqual(self.make_target().expected(KEY, PT), CT)

    def test_auto_cpa_is_labelled_first_order_diagnostic(self):
        """A synthetic first-order leak proves model routing and report caveats."""
        from proact_host.validation import aes128_encrypt_block

        _load_aes_engine()
        from analysis_models.aes.proact_sca import HW, SB

        directory = tempfile.mkdtemp(prefix="proact-masked-cpa-test-")
        old_data = config_module.DATA
        config_module.DATA = directory
        try:
            cfg = AcqConfig(target="sw_rv_masked", traces=128, auto_cpa=True,
                            suffix="_diagnostic_ut", seed=99)
            gen = InputGen(cfg)
            store = TraceStore(cfg, samples=32, out_len=16, key_varies=False)
            noise = np.random.default_rng(5)
            for index in range(cfg.traces):
                key, plaintext = gen.key(index), gen.inp(index)
                wave = noise.normal(0, 0.0002, 32).astype(np.float32)
                for byte in range(16):
                    leak = float(HW[SB[plaintext[byte] ^ key[byte]]]) * 0.03
                    wave[2 * byte:2 * byte + 2] += leak
                store.append(index, wave, key, plaintext,
                             aes128_encrypt_block(key, plaintext))
            store.checkpoint(cfg.traces)
            store.close()

            report = run_cpa(cfg.base, "sw_rv_masked")
            self.assertTrue(report["masked_first_order_diagnostic"])
            self.assertIn("no second-order", report["masking_caveat"])
            self.assertEqual(report["model"], "first_sbox")
            self.assertEqual(report["correct_bytes"], 16)
            self.assertFalse(report["minimum_trace_count_claimed"])
        finally:
            config_module.DATA = old_data
            shutil.rmtree(directory, ignore_errors=True)

    def test_tvla_metadata_and_h5_csv_exports_preserve_masked_target(self):
        import h5py
        from proact_host.validation import aes128_encrypt_block

        directory = tempfile.mkdtemp(prefix="proact-masked-export-test-")
        old_data = config_module.DATA
        config_module.DATA = directory
        try:
            cfg = AcqConfig(target="sw_rv_masked", traces=4, tvla=True,
                            tvla_per_class=2, warmup=3, suffix="_tvla_export_ut",
                            seed=199).validate()
            gen = InputGen(cfg)
            store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
            for index in range(cfg.traces):
                key, plaintext = gen.key(index), gen.inp(index)
                store.append(index, np.full(4, index / 1000, np.float32), key,
                             plaintext, aes128_encrypt_block(key, plaintext),
                             group=gen.tvla_group(index),
                             block=gen.tvla_block(index))
            store.checkpoint(cfg.traces)
            store.close()

            h5_path = export_h5(cfg.base)
            csv_path, sidecar_path = export_csv(cfg.base)
            with h5py.File(h5_path, "r") as h5:
                config = json.loads(h5.attrs["config_json"])
                self.assertEqual(config["target"], "sw_rv_masked")
                self.assertEqual(h5["plaintext"].id, h5["input"].id)
                self.assertEqual(h5["group"][:].tolist().count(0), 2)
                self.assertEqual(h5["group"][:].tolist().count(1), 2)
            with open(csv_path, newline="", encoding="utf-8") as stream:
                self.assertEqual(next(csv.reader(stream))[2], "plaintext_hex")
            with open(sidecar_path, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["rows"], 4)
        finally:
            config_module.DATA = old_data
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
