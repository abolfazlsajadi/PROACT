"""Hardware-free round-trip tests for HDF5 and CSV exports."""
import csv
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

import acq.config as config_module
from acq.config import AcqConfig
from acq.exporters import export_csv, export_h5, export_selected
from acq.inputs import InputGen
from acq.native import open_native
from acq.store import TraceStore


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.old_data = config_module.DATA
        config_module.DATA = self.directory

    def tearDown(self):
        config_module.DATA = self.old_data
        shutil.rmtree(self.directory, ignore_errors=True)

    def make_dataset(self, *, random_key=False, suffix="_export",
                     groups=None, blocks=None):
        cfg = AcqConfig(
            target="aes1", traces=5, suffix=suffix, seed=41,
            key_policy="random" if random_key else "fixed")
        gen = InputGen(cfg)
        store = TraceStore(cfg, samples=4, out_len=16, key_varies=random_key)
        for index in range(3):
            trace = np.array([index / 100, -index / 100, 0.125, -0.125], np.float32)
            store.append(
                index, trace, gen.key(index), gen.inp(index), bytes(range(16)),
                group=(groups[index] if groups is not None else 255),
                block=(blocks[index] if blocks is not None else -1))
        store.checkpoint(3)
        store.close()
        return cfg

    def test_h5_contains_only_done_rows_and_exact_native_values(self):
        import h5py

        cfg = self.make_dataset()
        path = export_h5(cfg.base)
        native = np.load(cfg.base + "_traces.npy", mmap_mode="r")
        with h5py.File(path, "r") as h5:
            self.assertEqual(h5["traces"].shape, (3, 4))
            self.assertEqual(h5["input"].shape, (3, 16))
            self.assertEqual(h5["output"].shape, (3, 16))
            self.assertEqual(h5["plaintext"].id, h5["input"].id)
            self.assertEqual(h5["ciphertext"].id, h5["output"].id)
            self.assertEqual(h5["key"].shape, (16,))
            np.testing.assert_array_equal(h5["traces"][:], native[:3])
            self.assertEqual(int(h5.attrs["done"]), 3)
            self.assertEqual(int(h5.attrs["n_alloc"]), 5)
            self.assertEqual(h5.attrs["waveform_unit"], "normalized_adc_code")
            self.assertGreater(float(h5.attrs["scale_counts_per_capture_unit"]), 0)

    def test_h5_preserves_per_trace_keys(self):
        import h5py

        cfg = self.make_dataset(random_key=True, suffix="_random_key")
        path = export_h5(cfg.base)
        with open_native(cfg.base) as meta, h5py.File(path, "r") as h5:
            self.assertEqual(h5["key"].shape, (3, 16))
            np.testing.assert_array_equal(h5["key"][:], meta["key"][:3])

    def test_h5_exports_a_valid_zero_row_dataset(self):
        import h5py

        cfg = AcqConfig(target="aes1", traces=5, suffix="_empty_export", seed=43)
        store = TraceStore(cfg, samples=4, out_len=16, key_varies=False)
        store.close()
        path = export_h5(cfg.base)
        with h5py.File(path, "r") as h5:
            self.assertEqual(int(h5.attrs["done"]), 0)
            self.assertEqual(h5["traces"].shape, (0, 4))
            self.assertEqual(h5["input"].shape, (0, 16))
            self.assertEqual(h5["output"].shape, (0, 16))
            self.assertEqual(h5["group"].shape, (0,))
            self.assertEqual(h5["block"].shape, (0,))
            self.assertEqual(h5["ts"].shape, (0,))

    def test_csv_round_trip_and_sidecar(self):
        cfg = self.make_dataset()
        csv_path, sidecar_path = export_csv(cfg.base)
        native = np.load(cfg.base + "_traces.npy", mmap_mode="r")
        with open(csv_path, newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][:5], [
            "trace_index", "key_hex", "plaintext_hex", "output_hex",
            "scale_counts_per_capture_unit"])
        self.assertEqual(rows[1][1], bytes(range(16)).hex())
        first_sample = rows[0].index("sample_0000_raw")
        np.testing.assert_array_equal(
            np.asarray(rows[1][first_sample:], dtype=np.int16), native[0])
        with open(sidecar_path, encoding="utf-8") as stream:
            sidecar = json.load(stream)
        self.assertEqual(sidecar["rows"], 3)
        self.assertEqual(sidecar["allocated_rows"], 5)
        self.assertFalse(sidecar["complete"])
        self.assertEqual(sidecar["samples"], 4)
        self.assertEqual(sidecar["waveform_unit"], "normalized_adc_code")

    def test_native_selection_is_noop(self):
        cfg = self.make_dataset()
        self.assertEqual(export_selected(cfg.base, ("native",)), [])
        self.assertFalse(os.path.exists(cfg.base + ".h5"))
        self.assertFalse(os.path.exists(cfg.base + ".csv"))

    def test_optional_tvla_row_fields_are_preserved(self):
        import h5py

        cfg = self.make_dataset(
            suffix="_tvla_fields", groups=[0, 1, 0], blocks=[0, 0, 1])
        values = dict(
            group=np.array([0, 1, 0], dtype=np.uint8),
            block=np.array([0, 0, 1], dtype=np.int64),
            ts=np.asarray(np.load(cfg.base + "_ts.npy", mmap_mode="r")[:3]).copy())

        h5_path = export_h5(cfg.base)
        csv_path, sidecar_path = export_csv(cfg.base)
        with h5py.File(h5_path, "r") as h5:
            np.testing.assert_array_equal(h5["group"][:], values["group"][:3])
            np.testing.assert_array_equal(h5["block"][:], values["block"][:3])
            np.testing.assert_array_equal(h5["ts"][:], values["ts"][:3])
        with open(csv_path, newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0][5:8], ["group", "block", "ts"])
        self.assertEqual(rows[1][5:7], ["0", "0"])
        self.assertEqual(float(rows[1][7]), values["ts"][0])
        with open(sidecar_path, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["optional_row_columns"],
                             ["group", "block", "ts"])


if __name__ == "__main__":
    unittest.main()
