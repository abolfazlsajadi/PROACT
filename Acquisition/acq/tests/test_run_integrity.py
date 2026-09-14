"""Hardware-free regression tests for the acquisition hot-loop fault boundary."""
from __future__ import annotations

import numpy as np
import pytest

import acq.config as config_module
import acq.exporters as exporters_module
import acq.run as run_module
import acq.space as space_module
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.store import TraceStore


SAMPLES = 100


class FakeUI:
    def __init__(self):
        self.notes = []
        self.updates = []
        self.summary_data = None
        self.close_count = 0

    def log(self, _message):
        pass

    def note(self, message):
        self.notes.append(str(message))

    def start(self, _total, _done):
        pass

    def update(self, count, stats):
        self.updates.append((count, dict(stats)))

    def summary(self, data):
        self.summary_data = dict(data)

    def close(self):
        self.close_count += 1


class FakeTarget:
    out_len = 16

    def __init__(self):
        self.open_count = 0
        self.close_count = 0
        self.run_count = 0
        self.reprogram_count = 0
        self.select_count = 0
        self.configure_trigger_count = 0
        self.run_failures_remaining = 0
        self.wrong_outputs_remaining = 0
        self.fail_on_run_numbers = set()

    def open(self):
        self.open_count += 1

    def close(self):
        self.close_count += 1

    def bringup_and_verify(self, _key):
        pass

    def select(self, _key):
        self.select_count += 1

    def configure_trigger(self):
        self.configure_trigger_count += 1

    def reprogram(self):
        self.reprogram_count += 1

    def run(self, key, inp):
        self.run_count += 1
        if self.run_failures_remaining:
            self.run_failures_remaining -= 1
            raise OSError("simulated transient target failure")
        if self.run_count in self.fail_on_run_numbers:
            raise OSError("simulated transient target failure")
        expected = self.expected(key, inp)
        if self.wrong_outputs_remaining:
            self.wrong_outputs_remaining -= 1
            return bytes([expected[0] ^ 1]) + expected[1:]
        return expected

    def expected(self, _key, inp):
        return bytes(inp[:self.out_len])


class FakeBackend:
    storage_metadata = {"waveform_unit": "test_capture_unit"}
    clock_status = {"locked": True, "clkgen_freq_MHz": 50,
                    "adc_freq_MHz": 200}

    def __init__(self, captures):
        self.captures = list(captures)
        self.last_capture_clipped = False
        self.arm_count = 0
        self.capture_count = 0
        self.close_count = 0

    def configure(self, **_kwargs):
        pass

    def set_samples(self, _samples):
        pass

    def arm(self):
        self.arm_count += 1

    def capture(self, timeout=5.0):
        assert timeout == 5.0
        if not self.captures:
            raise AssertionError("test did not supply enough fake captures")
        wave, clipped = self.captures.pop(0)
        self.last_capture_clipped = bool(clipped)
        self.capture_count += 1
        return np.asarray(wave, dtype=np.float32)

    def close(self):
        self.close_count += 1


class FakeStore:
    def __init__(self, cfg, *, append_error=None, checkpoint_error=None):
        self.N = cfg.traces
        self.S = SAMPLES
        self.base = cfg.base
        self.done = 0
        self.rows = []
        self.append_calls = 0
        self.checkpoints = []
        self.close_count = 0
        self.append_error = append_error
        self.checkpoint_error = checkpoint_error

    def start_recording_session(self):
        pass

    def last_trace_digest(self):
        return None

    def append(self, index, wave, key, inp, out, **metadata):
        self.append_calls += 1
        if self.append_error is not None:
            raise self.append_error
        self.rows.append((index, np.asarray(wave).copy(), key, inp, out,
                          dict(metadata)))
        self.done = index + 1

    def checkpoint(self, done, extra=None):
        self.checkpoints.append((done, dict(extra or {})))
        if self.checkpoint_error is not None:
            raise self.checkpoint_error

    def close(self):
        self.close_count += 1


def _wave(seed):
    wave = np.linspace(-0.25, 0.25, SAMPLES, dtype=np.float32)
    wave[seed % SAMPLES] += np.float32((seed + 1) / 1000)
    return wave


def _run_offline(monkeypatch, tmp_path, captures, traces, *, append_error=None,
                 checkpoint_error=None):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig("aes1", traces, samples=SAMPLES, seed=123,
                    suffix="_hot_loop_test", chunk=10_000).validate()
    target = FakeTarget()
    backend = FakeBackend(captures)
    store = FakeStore(cfg, append_error=append_error,
                      checkpoint_error=checkpoint_error)
    ui = FakeUI()

    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(
        run_module, "preflight",
        lambda *_args: {"samples": SAMPLES, "trig_count": 44, "clip": 0})
    monkeypatch.setattr(run_module, "TraceStore", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(run_module.ckpt, "verify_resume", lambda *_args: None)
    monkeypatch.setattr(run_module.ckpt, "describe", lambda *_args: "fake store")
    monkeypatch.setattr(
        space_module, "estimate",
        lambda *_args, **_kwargs: {"need_b": 0, "free_b": 10**12})
    monkeypatch.setattr(space_module, "report_lines", lambda _estimate: [])
    monkeypatch.setattr(exporters_module, "estimate_selected_bytes",
                        lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(run_module.time, "sleep", lambda _seconds: None)

    return (cfg, target, backend, store, ui,
            lambda: run_module.run(cfg, ui=ui, selected_formats=()))


def test_hot_loop_rejects_nonfinite_constant_and_clipped_traces(monkeypatch,
                                                                 tmp_path):
    nonfinite = _wave(1)
    nonfinite[9] = np.nan
    constant = np.full(SAMPLES, 0.125, dtype=np.float32)
    clipped = _wave(2)
    valid = _wave(3)
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path,
        [(nonfinite, False), (constant, False), (clipped, True), (valid, False)],
        traces=1)

    result = invoke()

    assert result["done"] == 1
    assert result["fails"] == 3
    assert backend.arm_count == backend.capture_count == target.run_count == 4
    assert store.append_calls == len(store.rows) == 1
    np.testing.assert_array_equal(store.rows[0][1], valid)
    counters = store.checkpoints[-1][1]
    assert counters["quality_rejects"] == 3
    assert counters["nonfinite_rejects"] == 1
    assert counters["constant_rejects"] == 1
    assert counters["clipped_rejects"] == 1
    assert counters["stale_rejects"] == 0
    assert counters["consecutive_quality_rejects"] == 0
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1


def test_hot_loop_rejects_third_consecutive_identical_trace(monkeypatch,
                                                             tmp_path):
    repeated = _wave(11)
    replacement = _wave(12)
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path,
        [(repeated, False), (repeated.copy(), False),
         (repeated.copy(), False), (replacement, False)],
        traces=3)

    result = invoke()

    assert result["done"] == 3
    assert result["fails"] == 1
    assert backend.capture_count == target.run_count == 4
    assert store.append_calls == 3
    np.testing.assert_array_equal(store.rows[0][1], repeated)
    np.testing.assert_array_equal(store.rows[1][1], repeated)
    np.testing.assert_array_equal(store.rows[2][1], replacement)
    counters = store.checkpoints[-1][1]
    assert counters["quality_rejects"] == counters["stale_rejects"] == 1
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1


def test_output_mismatch_is_retried_counted_and_persisted(monkeypatch, tmp_path):
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path,
        [(_wave(17), False), (_wave(18), False)], traces=1)
    target.wrong_outputs_remaining = 1

    result = invoke()

    assert result["done"] == 1
    assert result["fails"] == 1
    assert result["output_mismatches"] == 1
    assert target.run_count == backend.capture_count == 2
    assert store.append_calls == 1
    counters = store.checkpoints[-1][1]
    assert counters["output_mismatches"] == 1
    assert ui.summary_data["out-mismatch"] == 1


def test_resume_accumulates_faults_and_keeps_stale_digest(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "DATA", str(tmp_path))
    cfg = AcqConfig("aes1", 3, samples=SAMPLES, seed=123,
                    suffix="_resume_integrity", chunk=10_000).validate()
    gen = InputGen(cfg)
    repeated = _wave(41)
    replacement = _wave(42)

    initial = TraceStore(
        cfg, SAMPLES, 16, False,
        instrument_meta={"waveform_unit": "test_capture_unit"})
    initial.append(0, repeated, gen.key(0), gen.inp(0), gen.inp(0))
    initial.checkpoint(1, extra={
        "trig_count": 44,
        "fails": 7,
        "recoveries": 2,
        "quality_rejects": 3,
        "clipped_rejects": 1,
        "nonfinite_rejects": 1,
        "constant_rejects": 1,
        "stale_rejects": 0,
        "instrument_rejects": 1,
        "output_mismatches": 4,
    })
    initial.close()

    target = FakeTarget()
    backend = FakeBackend([
        (repeated.copy(), False),
        (repeated.copy(), False),
        (replacement, False),
    ])
    ui = FakeUI()
    monkeypatch.setattr(run_module, "make_target", lambda _cfg: target)
    monkeypatch.setattr(run_module, "_backend", lambda _cfg: backend)
    monkeypatch.setattr(
        run_module, "preflight",
        lambda *_args: {"samples": SAMPLES, "trig_count": 44, "clip": 0})
    monkeypatch.setattr(
        space_module, "estimate",
        lambda *_args, **_kwargs: {"need_b": 0, "free_b": 10**12})
    monkeypatch.setattr(space_module, "report_lines", lambda _estimate: [])
    monkeypatch.setattr(exporters_module, "estimate_selected_bytes",
                        lambda *_args, **_kwargs: 0)

    result = run_module.run(cfg, ui=ui, selected_formats=())

    assert result["done"] == 3
    assert result["fails"] == 8
    assert result["recoveries"] == 2
    assert result["output_mismatches"] == 4
    assert backend.capture_count == 3
    with np.load(cfg.base + "_meta.npz", allow_pickle=True) as meta:
        assert int(meta["fails"]) == 8
        assert int(meta["recoveries"]) == 2
        assert int(meta["quality_rejects"]) == 4
        assert int(meta["clipped_rejects"]) == 1
        assert int(meta["nonfinite_rejects"]) == 1
        assert int(meta["constant_rejects"]) == 1
        assert int(meta["stale_rejects"]) == 1
        assert int(meta["instrument_rejects"]) == 1
        assert int(meta["output_mismatches"]) == 4
    stored = np.load(cfg.base + "_traces.npy", mmap_mode="r")
    assert np.array_equal(stored[0], stored[1])
    assert not np.array_equal(stored[1], stored[2])


@pytest.mark.parametrize(
    "value",
    (np.asarray(1.5), np.asarray("2"), np.asarray(True), np.asarray([2])),
)
def test_fault_history_rejects_noninteger_counter_metadata(tmp_path, value):
    meta_path = tmp_path / "fault_meta.npz"
    np.savez(meta_path, fails=value)

    with pytest.raises(ValueError, match="'fails' must be an integer scalar"):
        run_module._fault_history(str(meta_path))


def test_storage_failure_fails_fast_preserves_error_and_closes_everything(
        monkeypatch, tmp_path):
    append_error = RuntimeError("simulated disk write failure")
    checkpoint_error = OSError("simulated emergency checkpoint failure")
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [(_wave(21), False)], traces=1,
        append_error=append_error, checkpoint_error=checkpoint_error)

    with pytest.raises(RuntimeError, match="simulated disk write failure"):
        invoke()

    assert store.append_calls == 1
    assert backend.capture_count == target.run_count == 1
    assert target.open_count == 1
    assert target.reprogram_count == 0
    assert len(store.checkpoints) == 1
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1
    assert any("emergency checkpoint also failed" in note for note in ui.notes)


@pytest.mark.parametrize("checkpoint_fails", (False, True))
def test_keyboard_interrupt_records_truthful_emergency_checkpoint_status(
        monkeypatch, tmp_path, checkpoint_fails):
    checkpoint_error = (OSError("simulated emergency checkpoint failure")
                        if checkpoint_fails else None)
    interrupt = KeyboardInterrupt()
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [(_wave(23), False)], traces=1,
        append_error=interrupt, checkpoint_error=checkpoint_error)

    with pytest.raises(KeyboardInterrupt) as caught:
        invoke()

    assert caught.value.proact_checkpoint_succeeded is (not checkpoint_fails)
    assert caught.value.proact_checkpoint_error == (
        str(checkpoint_error) if checkpoint_fails else None)
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1


def test_recovery_limit_is_fatal_and_emergency_checkpoints(monkeypatch, tmp_path):
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [], traces=1)
    target.run_failures_remaining = 120 * 41

    with pytest.raises(RuntimeError, match="stopped after 41 controller recoveries"):
        invoke()

    assert target.run_count == 120 * 41
    assert backend.capture_count == 0
    assert len(store.checkpoints) == 1
    assert store.checkpoints[-1][0] == 0
    assert store.checkpoints[-1][1]["recoveries"] == 41
    assert backend.close_count == store.close_count == 1
    assert ui.close_count == 1


def test_consecutive_quality_rejects_abort_without_target_recovery(monkeypatch,
                                                                   tmp_path):
    captures = [(_wave(index), True)
                for index in range(run_module.MAX_CONSECUTIVE_QUALITY_REJECTS)]
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, captures, traces=1)

    with pytest.raises(RuntimeError, match=(
            r"20 consecutive waveform-quality rejects.*instrument triggering")):
        invoke()

    assert backend.arm_count == backend.capture_count == target.run_count == 20
    assert store.append_calls == 0
    assert target.open_count == 1
    assert target.select_count == target.configure_trigger_count == 0
    assert target.reprogram_count == 0
    counters = store.checkpoints[-1][1]
    assert counters["quality_rejects"] == counters["clipped_rejects"] == 20
    assert counters["consecutive_quality_rejects"] == 20
    assert counters["quality_abort_threshold"] == 20
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1


def test_transient_target_failures_still_use_existing_uart_recovery(monkeypatch,
                                                                    tmp_path):
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [(_wave(31), False)], traces=1)
    target.run_failures_remaining = 20

    result = invoke()

    assert result["done"] == 1
    assert result["fails"] == 20
    assert backend.arm_count == 21
    assert backend.capture_count == 1
    assert target.run_count == 21
    assert target.open_count == 2
    assert target.close_count == 2
    assert target.select_count == target.configure_trigger_count == 1
    assert target.reprogram_count == 0
    assert store.append_calls == 1
    assert backend.close_count == store.close_count == ui.close_count == 1


@pytest.mark.parametrize("raise_on_call", (5, 6))
def test_firmware_identity_change_aborts_uart_recovery(
        monkeypatch, tmp_path, raise_on_call):
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [], traces=1)
    target.run_failures_remaining = 20
    calls = 0

    def verify(_cfg):
        nonlocal calls
        calls += 1
        if calls == raise_on_call:
            raise RuntimeError("firmware host artifact changed during test")
        return {}

    monkeypatch.setattr(run_module, "verify_or_pin_firmware_identity", verify)
    with pytest.raises(RuntimeError, match="firmware host artifact changed"):
        invoke()

    assert target.run_count == 20
    assert target.reprogram_count == 0
    assert store.done == 0
    assert backend.close_count == store.close_count == ui.close_count == 1


def test_nonquality_fault_resets_quality_reject_streak(monkeypatch, tmp_path):
    clipped = [(_wave(index), True) for index in range(20)]
    valid = (_wave(90), False)
    _, target, backend, store, ui, invoke = _run_offline(
        monkeypatch, tmp_path, [clipped[0], *clipped[1:], valid], traces=1)
    # Run 1 reaches capture and is clipped. Run 2 fails in the target before
    # capture. The remaining 19 clipped records must therefore form a new streak.
    target.fail_on_run_numbers = {2}

    result = invoke()

    assert result["done"] == 1
    assert result["fails"] == 21
    assert backend.arm_count == target.run_count == 22
    assert backend.capture_count == 21
    assert target.open_count == 1
    assert target.reprogram_count == 0
    assert store.append_calls == 1
    counters = store.checkpoints[-1][1]
    assert counters["quality_rejects"] == counters["clipped_rejects"] == 20
    assert counters["consecutive_quality_rejects"] == 0
    assert backend.close_count == target.close_count == store.close_count == 1
    assert ui.close_count == 1
