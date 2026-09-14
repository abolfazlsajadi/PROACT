"""Pre-acquisition validation (L1.8).

Never start a large campaign after only opening the link. This runs a short battery
on the real hardware and REFUSES the campaign if anything is wrong:
  * KAT: chip output == software reference (done in Target.bringup_and_verify).
  * trigger fires and its width is sane for the selected mode (detects a wrong jumper
    or an inactive trigger source).
  * captured waveform is non-empty, right length, not clipping.
  * repeated transactions are consistent (fixed key plus fixed input -> identical
    output; varying key or input -> outputs actually change).
Returns the measured trigger width and a chosen sample count (auto-size).
"""
from __future__ import annotations
import numpy as np


class ValidationError(RuntimeError):
    pass


def _fallback_clipping(wave, block_samples=1_000_000):
    """Check normalized samples without allocating a record-sized temporary."""
    flat = np.asarray(wave).reshape(-1)
    step = max(1, int(block_samples))
    for start in range(0, flat.size, step):
        block = flat[start:start + step]
        if np.any(block > 0.499) or np.any(block < -0.499):
            return True
    return False


def _wave_differs(reference, wave, block_samples=1_000_000):
    """Compare records in bounded blocks instead of allocating one huge mask."""
    left = np.asarray(reference).reshape(-1)
    right = np.asarray(wave).reshape(-1)
    if left.size != right.size:
        return True
    step = max(1, int(block_samples))
    return any(
        not np.array_equal(left[start:start + step], right[start:start + step])
        for start in range(0, left.size, step))


def preflight(target, backend, gen, cfg, n=20):
    """Run n test captures; validate; return dict(trig_count, samples, clip)."""
    key0 = gen.key(0)
    # 1. warm up so trig_count settles, and confirm the trigger fires at all
    fired = 0
    for _ in range(6):
        try:
            backend.arm()
            target.run(key0, gen.inp(0))
            backend.capture(timeout=3.0)
            fired += 1
        except TimeoutError:
            pass
    if fired == 0:
        raise ValidationError(
            "trigger never fired. Check: jumper on the right pin (cfg for per-core), "
            "cfgsel points at an active source, and the core actually runs.")
    tc = backend.trig_count

    # 2. capture n real traces, check length / clipping / correctness / consistency
    outs = []; groups = []; clip = 0; correct = 0
    reference_wave = None
    waveform_varies = False
    length = None
    for i in range(n):
        k = gen.key(i)
        # A shuffled TVLA block can begin with several equal labels.  Preflight
        # deliberately alternates its *unstored* test inputs so it always proves
        # both fixed and random transactions work without consuming or changing
        # the stored schedule/resume indices.
        group = (i & 1) if gen.is_tvla else None
        inp = gen.input_for_group(i, group) if gen.is_tvla else gen.inp(i)
        backend.arm()
        out = target.run(k, inp)
        wave = np.asarray(backend.capture(timeout=3.0))
        if wave.ndim != 1 or wave.size == 0:
            raise ValidationError("captured waveform must be a non-empty 1-D record")
        if length is None:
            length = len(wave)
            reference_wave = wave.copy()
        elif len(wave) != length:
            raise ValidationError("inconsistent trace length across captures")
        elif not waveform_varies and _wave_differs(reference_wave, wave):
            waveform_varies = True
        outs.append(out); groups.append(group)
        correct += (out == target.expected(k, inp))
        backend_clip = getattr(backend, "last_capture_clipped", None)
        clip += int(_fallback_clipping(wave) if backend_clip is None
                    else bool(backend_clip))
    if cfg.samples and length != cfg.samples:
        raise ValidationError(
            f"instrument returned {length} samples, but {cfg.samples} were requested. "
            "Choose a record length accepted by the instrument or use --samples 0.")
    if correct != n:
        raise ValidationError(f"crypto output wrong in {n-correct}/{n} preflight runs")
    if clip > 0:
        raise ValidationError(f"clipping in {clip}/{n} traces -- lower the gain")
    if not waveform_varies:
        raise ValidationError("captured waveform is constant (no signal / dead line)")
    # Consistency depends on both public input and key.  A fixed input does not
    # imply a fixed output when the acquisition deliberately varies the key.
    same = all(o == outs[0] for o in outs)
    if gen.is_tvla:
        fixed_out = [o for o, g in zip(outs, groups) if g == 0]
        random_out = [o for o, g in zip(outs, groups) if g == 1]
        if len(fixed_out) < 1 or len(random_out) < 1:
            raise ValidationError("TVLA preflight did not exercise both groups")
        if not all(o == fixed_out[0] for o in fixed_out):
            raise ValidationError("TVLA fixed-input preflight outputs differ -- desync?")
        if len(random_out) > 1 and all(o == random_out[0] for o in random_out):
            raise ValidationError("TVLA random-input preflight outputs are all identical")
        if all(o == fixed_out[0] for o in random_out):
            raise ValidationError("TVLA random-input output equals the fixed output every time")
    else:
        output_should_be_fixed = (
            cfg.key_policy == "fixed" and cfg.input_policy == "fixed")
        output_should_vary = (
            cfg.key_policy == "random" or cfg.input_policy == "random")
        if output_should_be_fixed and not same:
            raise ValidationError(
                "fixed key and fixed input but outputs differ -- desync?")
        if output_should_vary and same and n > 2:
            raise ValidationError(
                "varying key/input but every output is identical -- stalled core?")

    samples = (cfg.samples or max(100, int(tc * 1.25))
               if tc else (cfg.samples or length))
    return dict(trig_count=tc, samples=int(samples), clip=clip, length=length)
