"""Acquisition driver (L1.9) -- wires config, target, backend, storage, checkpoint,
recovery, validation and progress into one A-to-Z run.

Flow: build target+backend -> bring up + KAT verify -> preflight validate + auto-size
-> (re)open store, resume if present -> hot loop with checkpointing, watchdog and
periodic in-run sanity -> final flush + summary.
"""
from __future__ import annotations
import json, os, time, secrets
from datetime import datetime, timezone
import numpy as np

from .inputs import InputGen
from .store import TraceStore, trace_digest as waveform_digest
from .native import exact_integer_scalar, verify_or_pin_firmware_identity
from . import checkpoint as ckpt
from .validate import preflight
from .progress import make_ui
from .backend.husky import HuskyBackend
from .targets import make_target


MAX_CONSECUTIVE_QUALITY_REJECTS = 20
CUMULATIVE_FAULT_COUNTERS = (
    "fails", "recoveries", "quality_rejects", "clipped_rejects",
    "nonfinite_rejects", "constant_rejects", "stale_rejects",
    "instrument_rejects", "output_mismatches",
)


class WaveformQualityError(ValueError):
    """A captured record is unusable, but the target transaction may be healthy."""


def _warmup(target, gen, count, start_index, ui):
    """Run exactly ``count`` successful target operations without recording them."""
    count = int(count)
    if count <= 0:
        return dict(requested=0, completed=0, failures=0, elapsed_s=0.0)
    ui.note(f"warm-up: {count:,} target operations will be discarded")
    begun = time.monotonic()
    completed = failures = consecutive = 0
    report_every = max(1, count // 10)
    while completed < count:
        index = int(start_index) + completed
        key = gen.key(index)
        # Warm-up indices intentionally live outside the stored row range.  Do
        # not ask tvla_group() for them; exercise both input classes directly.
        inp = (gen.input_for_group(index, completed & 1)
               if gen.is_tvla else gen.inp(index))
        try:
            out = target.run(key, inp)
            expected = target.expected(key, inp)
            if bytes(out[:target.out_len]) != bytes(expected[:target.out_len]):
                raise ValueError("warm-up output did not match the software reference")
            completed += 1
            consecutive = 0
            if completed == count or completed % report_every == 0:
                ui.note(f"warm-up: {completed:,}/{count:,} discarded operations")
        except Exception:
            failures += 1
            consecutive += 1
            if consecutive in (20, 60):
                verify_or_pin_firmware_identity(gen.cfg)
                try:
                    target.close()
                    target.open()
                    target.select(key)
                    target.configure_trigger()
                except Exception:
                    pass
                verify_or_pin_firmware_identity(gen.cfg)
            if consecutive >= 120:
                raise RuntimeError(
                    f"warm-up stopped after {completed:,}/{count:,} successful "
                    f"operations ({failures:,} failures)")
    return dict(requested=count, completed=completed, failures=failures,
                elapsed_s=time.monotonic() - begun)


def _warmup_history(meta_path):
    if not os.path.exists(meta_path):
        return []
    with np.load(meta_path, allow_pickle=True) as old:
        if "warmup_sessions_json" not in old.files:
            return []
        try:
            history = json.loads(str(old["warmup_sessions_json"].item()))
        except Exception as exc:
            raise ValueError("invalid persisted warm-up session history") from exc
    if not isinstance(history, list) or any(
            not isinstance(session, dict) for session in history):
        raise ValueError("persisted warm-up session history must be a list of objects")
    return history


def _fault_history(meta_path):
    """Load campaign-wide counters so a resumed checkpoint cannot erase faults."""
    counters = {name: 0 for name in CUMULATIVE_FAULT_COUNTERS}
    if not os.path.exists(meta_path):
        return counters
    with np.load(meta_path, allow_pickle=True) as old:
        for name in counters:
            if name not in old.files:
                continue
            value = exact_integer_scalar(old[name], name)
            if value < 0:
                raise ValueError(f"invalid negative persisted fault counter {name}={value}")
            counters[name] = value
    return counters


def _backend(cfg):
    if cfg.backend == "husky":
        return HuskyBackend()
    if cfg.backend == "scope":
        from .backend.scope import ScopeBackend
        return ScopeBackend(resource=cfg.scope_resource or None,
                            dialect=cfg.scope_dialect, channel=cfg.scope_channel,
                            trig_source=cfg.scope_trig_source,
                            trig_level=cfg.scope_trig_level,
                            clock_source=cfg.scope_clock_source,
                            transfer_timeout_ms=cfg.scope_transfer_timeout_ms)
    raise SystemExit(f"unknown backend {cfg.backend}")


def _prepare_capture(cfg, target, backend, gen, key0, ui):
    """Apply the measurement clock first, then open UART and run preflight."""
    try:
        verify_or_pin_firmware_identity(cfg)
        backend.configure(samples=cfg.samples or 2000, offset=cfg.offset,
                          gain_db=cfg.gain_db, clock_hz=cfg.clock_mhz * 1e6,
                          adc_mul=cfg.adc_mul)
        if cfg.backend == "husky":
            status = getattr(backend, "clock_status", {})
            observed = status.get("clkgen_freq_MHz")
            adc = status.get("adc_freq_MHz")
            detail = f"Husky clock applied: {observed:g} MHz" if observed is not None else (
                f"Husky clock request applied: {cfg.clock_mhz:g} MHz")
            if adc is not None:
                detail += f", ADC {adc:g} MHz"
            ui.note(detail)
        elif cfg.scope_clock_source == "husky":
            status = getattr(backend, "clock_status", {})
            observed = status.get("clkgen_freq_MHz")
            detail = (f"Husky board clock applied: {observed:g} MHz"
                      if observed is not None else
                      f"Husky board clock request applied: {cfg.clock_mhz:g} MHz")
            ui.note(detail + "; Husky remains open while the oscilloscope captures")
        else:
            ui.note(f"external board clock declared: {cfg.clock_mhz:g} MHz "
                    "(oscilloscope does not drive or verify it)")

        target.open()
        if getattr(cfg, 'force_reset', False):
            ui.note('--reset: reprogramming controller before start')
            verify_or_pin_firmware_identity(cfg)
            target.reprogram(); target.close(); target.open()
            verify_or_pin_firmware_identity(cfg)
        verify_or_pin_firmware_identity(cfg)
        target.bringup_and_verify(key0)
        verify_or_pin_firmware_identity(cfg)
        ui.note(f"{cfg.target}: verified against software reference")
        return preflight(target, backend, gen, cfg)
    except BaseException:
        try: backend.close()
        except Exception: pass
        try: target.close()
        except Exception: pass
        raise


def run(cfg, ui=None, fancy=None, selected_formats=()):
    """Capture while exclusively owning both the shared bench and dataset base."""
    cfg.validate()
    from .locking import bench_lock, dataset_lock
    with bench_lock(f"capture {cfg.target}"):
        with dataset_lock(cfg.base, f"capture {cfg.target}"):
            return _run_unlocked(
                cfg, ui=ui, fancy=fancy, selected_formats=selected_formats)


def _run_unlocked(cfg, ui=None, fancy=None, selected_formats=()):
    cfg.validate()
    verify_or_pin_firmware_identity(cfg)
    if ui is None:
        ui = make_ui(cfg, fancy=fancy)
    # Resolve the RNG seed ONCE and persist it, so a resumed run regenerates the
    # exact same key/input stream. On resume, reuse the seed stored in the dataset;
    # on a fresh run, pin a nonzero seed (from --seed or random) so it is recorded.
    meta_path = cfg.base + "_meta.npz"
    if os.path.exists(meta_path):
        with np.load(meta_path, allow_pickle=True) as existing:
            stored_seed = int(existing["seed"])
        if not cfg.seed:
            cfg.seed = stored_seed
    elif not cfg.seed:
        cfg.seed = secrets.randbits(63) | 1
    gen = InputGen(cfg)
    target = make_target(cfg)
    backend = _backend(cfg)
    key0 = gen.key(0)

    ui.log(f"\n=== ACQUIRE {cfg.target}  {cfg.traces:,} traces  "
           f"key={cfg.key_policy} input={cfg.input_policy} trigger={cfg.trigger_mode}"
           + (f"/0x{cfg.aead_trigger:02x}" if cfg.is_aead else "") + " ===")

    # Apply/configure the requested clock before opening UART.  The UART generator
    # has a fixed divisor, so changing the clock changes its line rate immediately.
    # Opening the serial link first would make every non-default-frequency run fail
    # its KAT before the requested clock was ever applied.
    v = _prepare_capture(cfg, target, backend, gen, key0, ui)
    # Keep the live handles inside a cleanup boundary until TraceStore exists;
    # sizing, resume-integrity, and disk failures happen before the hot-loop's
    # own finally block and otherwise leave the UART/instrument open.
    try:
        S = v["samples"]
        backend.set_samples(S)
        if v["trig_count"]:
            trigger_detail = (f"trig={v['trig_count']} "
                              f"({v['trig_count']/cfg.adc_mul:.0f} cy)")
        else:
            trigger_detail = "trigger fired; width unavailable from instrument"
        ui.note(f"preflight OK: {trigger_detail}, samples={S}, clip={v['clip']}")
        if os.path.exists(meta_path):
            with np.load(meta_path, allow_pickle=True) as existing:
                if (int(existing["done"]) > 0 and "trig_count" in existing.files and
                        int(existing["trig_count"]) != int(v["trig_count"])):
                    raise SystemExit(
                        "resume trigger-width mismatch: stored "
                        f"{int(existing['trig_count'])} samples, observed "
                        f"{int(v['trig_count'])}. Use the original trigger path or a "
                        "new --suffix.")

        # 2b. disk-space estimate + check now that S (samples) is known
        from . import space as _space
        est = _space.estimate(cfg, samples=S, out_len=target.out_len)
        for ln in _space.report_lines(est):
            ui.note("disk: " + ln)
        from .exporters import estimate_selected_bytes, human_bytes
        export_bytes = estimate_selected_bytes(
            cfg, selected_formats, samples=S, out_len=target.out_len)
        if (est["need_b"] + export_bytes >= est["free_b"] * 0.98 and
                not getattr(cfg, "allow_nofit", False)):
            raise SystemExit(
                f"insufficient disk after exact auto-sizing: native needs {est['need_h']} "
                f"and selected exports need about {human_bytes(export_bytes)}, but only "
                f"{est['free_h']} is free. Lower --traces/remove an export or use "
                "--allow-nofit after checking space.")

        # 3. storage + resume.  Scope rows are scientific source data: native
        # storage must retain its normal int16 encoding of the normalized
        # instrument waveform without phase correction. Fractional trigger
        # correction belongs to an explicitly named, reproducible post-capture
        # analysis view.
        instrument_meta = getattr(backend, "storage_metadata", None)
        if cfg.backend == "scope":
            if not isinstance(instrument_meta, dict):
                raise RuntimeError(
                    "scope backend did not provide instrument metadata")
            if instrument_meta.get(
                    "scope_fractional_phase_alignment_applied") is not False:
                raise RuntimeError(
                    "scope backend must return unaligned waveforms for native storage")
            if (instrument_meta.get("scope_per_row_trigger_index_recorded") is not True or
                    "scope_alignment_reference_trigger_index_samples" not in
                    instrument_meta):
                raise RuntimeError(
                    "scope backend did not provide the per-row alignment provenance")
            verifier = getattr(backend, "verify_capture_state", None)
            if not callable(verifier):
                raise RuntimeError(
                    "scope backend does not provide checkpoint state verification")
            # Query-only check after preflight and final sizing, before a
            # dataset can publish any authoritative row marker.
            verifier()
        store = TraceStore(cfg, samples=S, out_len=target.out_len,
                           key_varies=gen.key_varies,
                           instrument_meta=instrument_meta)
        ckpt.verify_resume(store, gen)
        prior_faults = _fault_history(meta_path)
        last_trace_digest = store.last_trace_digest()
        ui.note(ckpt.describe(store))
    except BaseException:
        try:
            backend.close()
        except Exception:
            pass
        try:
            target.close()
        except Exception:
            pass
        raise

    # 4. hot loop
    N = cfg.traces
    i = store.done
    fails = retries = prior_faults["fails"]
    recoveries = prior_faults["recoveries"]
    quality_rejects = prior_faults["quality_rejects"]
    clipped_rejects = prior_faults["clipped_rejects"]
    nonfinite_rejects = prior_faults["nonfinite_rejects"]
    constant_rejects = prior_faults["constant_rejects"]
    stale_rejects = prior_faults["stale_rejects"]
    instrument_rejects = prior_faults["instrument_rejects"]
    output_mismatches = prior_faults["output_mismatches"]
    consec = 0
    quality_consec = 0
    prog = ui
    start = i
    warmup_history = _warmup_history(meta_path)
    warmup_last = None
    identical_run = 0
    checkpoint_invalid_reason = None

    def checkpoint_extra():
        return dict(
            fails=fails, recoveries=recoveries, trig_count=v["trig_count"],
            quality_rejects=quality_rejects,
            clipped_rejects=clipped_rejects,
            nonfinite_rejects=nonfinite_rejects,
            constant_rejects=constant_rejects,
            stale_rejects=stale_rejects,
            instrument_rejects=instrument_rejects,
            output_mismatches=output_mismatches,
            consecutive_quality_rejects=quality_consec,
            quality_abort_threshold=MAX_CONSECUTIVE_QUALITY_REJECTS,
            warmup_sessions_json=json.dumps(warmup_history, sort_keys=True))

    def verified_checkpoint():
        """Verify scope state before publishing this session's done marker."""
        nonlocal checkpoint_invalid_reason
        if checkpoint_invalid_reason is not None:
            raise RuntimeError(
                "checkpoint withheld after scope state verification failed: " +
                checkpoint_invalid_reason)
        if cfg.backend == "scope":
            try:
                backend.verify_capture_state()
            except BaseException as exc:
                # Error registers can be read-to-clear. Latch the failure so
                # cleanup cannot retry and then commit the suspect rows.
                checkpoint_invalid_reason = str(exc)
                raise
        store.checkpoint(i, extra=checkpoint_extra())

    def prepare_recording_session(reason):
        nonlocal warmup_last
        if cfg.warmup:
            started_utc = datetime.now(timezone.utc).isoformat()
            warm_index = N + i + len(warmup_history) * cfg.warmup
            warmup_last = _warmup(target, gen, cfg.warmup, warm_index, ui)
            warmup_last.update(started_utc=started_utc, resume_from=int(i),
                               reason=reason)
            warmup_history.append(warmup_last)
        store.start_recording_session()

    try:
        # Warm up at every session that will append rows, including resume.  A
        # resumed board may have cooled or restarted since its previous session.
        # The operations exercise the core but are never armed/captured/stored.
        if i < N:
            prepare_recording_session("capture_session_start")
        ui.start(N, i)
        t0 = time.time()
        while i < N:
            k, inp = gen.key(i), gen.inp(i)
            trace_digest = None
            try:
                if cfg.key_policy == "random":
                    pass             # target.run sets the key when needed
                try:
                    backend.arm()
                except Exception as exc:
                    instrument_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        f"instrument arm/ready failed: {exc}") from exc
                out = target.run(k, inp)
                try:
                    w = np.asarray(
                        backend.capture(timeout=5.0), dtype=np.float32)
                except Exception as exc:
                    instrument_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        f"instrument capture/readout failed: {exc}") from exc
                if w.ndim != 1 or len(w) != S:
                    instrument_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        f"wrong trace shape {w.shape}; expected ({S},)")
                if not np.isfinite(w).all():
                    nonfinite_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        "trace contains NaN or infinite samples")
                if bool(getattr(backend, "last_capture_clipped", False)):
                    clipped_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        "instrument reports ADC-rail clipping")
                if float(np.max(w)) == float(np.min(w)):
                    constant_rejects += 1
                    quality_rejects += 1
                    raise WaveformQualityError(
                        "trace is constant across its capture window")
                trace_digest = waveform_digest(w)
                if trace_digest == last_trace_digest:
                    identical_run += 1
                    # One exact duplicate can occur on a short, coarsely quantized
                    # record. Three in a row strongly indicates a stale scope/Husky
                    # buffer; reject the third and every repeated retry.
                    if identical_run >= 2:
                        stale_rejects += 1
                        quality_rejects += 1
                        raise WaveformQualityError(
                            "three consecutive bit-identical traces")
                else:
                    identical_run = 0
                expected = target.expected(k, inp)
                if bytes(out[:target.out_len]) != bytes(expected[:target.out_len]):
                    output_mismatches += 1
                    raise ValueError("target output did not match software reference")
            except WaveformQualityError as e:
                fails += 1; retries += 1
                quality_consec += 1
                # These failures come from the measurement path.  Reopening UART
                # or reprogramming the controller cannot repair clipping, a stale
                # instrument buffer, or invalid samples, and would only disturb
                # an otherwise controlled capture campaign.
                consec = 0
                if quality_consec >= MAX_CONSECUTIVE_QUALITY_REJECTS:
                    raise RuntimeError(
                        "capture stopped after "
                        f"{quality_consec} consecutive waveform-quality rejects; "
                        f"last reject: {e}. Check instrument triggering, range, "
                        "coupling, and record freshness before resuming.")
                continue
            except Exception:
                quality_consec = 0
                fails += 1; retries += 1; consec += 1
                if consec in (20, 60):
                    reopened = False
                    verify_or_pin_firmware_identity(cfg)
                    try: target.close()
                    except Exception: pass
                    time.sleep(2)
                    try:
                        target.open(); target.select(k); target.configure_trigger()
                        reopened = True
                    except Exception:
                        pass
                    verify_or_pin_firmware_identity(cfg)
                    if reopened:
                        prepare_recording_session("target_reopen_after_faults")
                elif consec >= 120:
                    recoveries += 1
                    verify_or_pin_firmware_identity(cfg)
                    try: target.close()
                    except Exception: pass
                    recovered = False
                    try:
                        target.reprogram(); time.sleep(1)
                        target.open(); target.bringup_and_verify(k)
                        recovered = True
                    except Exception:
                        time.sleep(15)
                    verify_or_pin_firmware_identity(cfg)
                    if recovered:
                        consec = 0
                        prepare_recording_session("controller_reprogram_after_faults")
                    if recoveries > 40:
                        raise RuntimeError(
                            f"capture stopped after {recoveries} controller recoveries; "
                            "the partial dataset will be emergency-checkpointed")
                continue

            # Storage/configuration faults must fail fast. Keeping this outside the
            # acquisition retry handler prevents disk errors from being misdiagnosed
            # as board faults and triggering controller reprogramming.
            store.append(i, w, k, inp, out,
                         group=(gen.tvla_group(i) if gen.is_tvla else 255),
                         block=(gen.tvla_block(i) if gen.is_tvla else -1),
                         scope_trigger_index=getattr(
                             backend, "last_trigger_index_samples", None))
            last_trace_digest = trace_digest
            consec = 0
            quality_consec = 0
            i += 1
            prog.update(1, dict(
                done=i, fail=fails, retry=retries, rec=recoveries,
                quality=quality_rejects,
                trig=(v["trig_count"] if v["trig_count"] else "n/a")))

            # periodic checkpoint + in-run sanity (L4.2 seed)
            if i % cfg.chunk == 0:
                verified_checkpoint()
    finally:
        # Preserve an acquisition exception even if its emergency checkpoint also
        # fails, while still closing every live handle.  If acquisition itself was
        # healthy, a checkpoint failure remains fatal and is reported to the caller.
        import sys
        active_error = sys.exc_info()[1]
        checkpoint_error = None
        try:
            verified_checkpoint()
        except BaseException as exc:
            checkpoint_error = exc
        for closer in (store.close, prog.close, backend.close, target.close):
            try:
                closer()
            except Exception:
                pass
        if checkpoint_error is not None:
            if active_error is None:
                raise checkpoint_error
            try:
                ui.note(f"emergency checkpoint also failed: {checkpoint_error}")
            except Exception:
                pass
        if isinstance(active_error, KeyboardInterrupt):
            # The CLI must never claim that Ctrl-C data was checkpointed unless
            # this exact emergency checkpoint completed successfully.
            active_error.proact_checkpoint_succeeded = checkpoint_error is None
            active_error.proact_checkpoint_error = (
                str(checkpoint_error) if checkpoint_error is not None else None)

    el = time.time() - t0
    rate = (i - start) / max(el, 1e-9)
    ui.summary({
        "target": cfg.target,
        "valid traces": f"{i:,} / {N:,}",
        "samples/trace": S,
        "rate": f"{rate:.0f} tr/s  ({1e3/max(rate,1e-9):.2f} ms/trace)",
        "elapsed": f"{el:.0f} s",
        "fails / recoveries": f"{fails} / {recoveries}",
        "quality rejects": (
            f"{quality_rejects} (clip={clipped_rejects}, nonfinite={nonfinite_rejects}, "
            f"constant={constant_rejects}, stale={stale_rejects}, "
            f"instrument={instrument_rejects})"),
        "warm-up": (f"{warmup_last['completed']:,} discarded this session"
                    if warmup_last else "disabled or no rows remaining"),
        "out-mismatch": output_mismatches,
        "dataset": f"{store.base}_traces.npy",
    })
    return dict(done=i, target=cfg.target, rate=rate, fails=fails,
                recoveries=recoveries, output_mismatches=output_mismatches,
                samples=S)
