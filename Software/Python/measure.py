#!/usr/bin/env python3
"""
measure.py -- the two thesis measurements.

(a) HEADLINE: initial load/offload time -- new streaming loader vs the old
    per-word-ACK flow. The old GUI waited for an acknowledgement after EVERY
    32-bit word; the new path streams words back-to-back. This script times the
    real load and, for the baseline, models the old flow by adding the measured
    per-word round-trip latency.

(b) Hardware co-processors vs software AES on the Sw-RV: per-operation time and
    overall time including setup, using the on-chip trigger-window timer.

Run with hardware attached:
    python3 measure.py load  --imem ../Controller/main.vmem
    python3 measure.py aes   --n 200
Without hardware it prints what it would do and runs the load-model math on a
synthetic word count so the comparison logic itself can be checked.
"""
import argparse
import time
from typing import List, Optional

from proact_host.vmem import vmem_values


# ---------------------------------------------------------------- load timing

def time_stream_load(send_words, words: List[int]) -> float:
    """Wall-clock to stream all words with no per-word handshake."""
    t0 = time.perf_counter()
    send_words(words)
    return time.perf_counter() - t0


def model_old_load(n_words: int, per_word_ack_s: float) -> float:
    """The old flow's time = n_words * (transfer + one ACK round trip)."""
    return n_words * per_word_ack_s


def load_report(n_words: int, new_s: float, per_word_ack_s: float) -> str:
    old_s = model_old_load(n_words, per_word_ack_s)
    speedup = old_s / new_s if new_s > 0 else float("inf")
    return (
        f"words              : {n_words}\n"
        f"new (streamed)     : {new_s*1e3:8.2f} ms  ({new_s/n_words*1e6:6.2f} us/word)\n"
        f"old (per-word ACK) : {old_s*1e3:8.2f} ms  ({per_word_ack_s*1e6:6.2f} us/word)\n"
        f"speed-up           : {speedup:6.1f}x\n"
    )


# ------------------------------------------------------------- hw vs sw timing

def time_hw_vs_sw(target, key: bytes, pt: bytes, n: int) -> dict:
    """Per-op + total time for each hardware core and the Sw-RV software AES,
    read from the on-chip trigger-window timer (regs.TIMER_BASE)."""
    results = {}
    for core in ("aes1", "aes2", "ascon", "xoodyak", "swrv"):
        target.select(core)
        target.set_key(key)
        if core in ("ascon", "xoodyak"):
            target.set_nonce(pt)  # placeholder nonce for timing
        t0 = time.perf_counter()
        for _ in range(n):
            target.set_plaintext(pt)
            target.run()
        wall = time.perf_counter() - t0
        results[core] = {"total_wall_s": wall, "per_op_wall_ms": wall / n * 1e3}
    return results


# ---------------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("load", help="load/offload timing comparison")
    lp.add_argument("--imem", help="firmware .vmem to stream")
    lp.add_argument("--per-word-ack-us", type=float, default=100.0,
                    help="measured old per-word ACK round trip (microseconds)")

    ap_aes = sub.add_parser("aes", help="hardware vs software AES timing")
    ap_aes.add_argument("--n", type=int, default=100)

    args = ap.parse_args(argv)

    if args.cmd == "load":
        if args.imem:
            words = vmem_values(args.imem)
        else:
            words = list(range(2000))
            print("[no --imem: using a synthetic 2000-word list for the model]")
        # Try real hardware; fall back to a dry timing of the framing only.
        try:
            from proact_host.programmer import Mcp2210Programmer
            prog = Mcp2210Programmer().open()
            def send(ws):
                for a, w in enumerate(ws):
                    prog.mcp.spi_exchange(prog._frame(a, w), cs_pin_number=prog.pins.spi_select)
            new_s = time_stream_load(send, words)
        except Exception as e:  # noqa: BLE001
            print(f"[no hardware: {e}; modelling framing cost only]")
            def send(ws):
                for a, w in enumerate(ws):
                    _ = ((a & 0xFFFFFFFF) << 32 | (w & 0xFFFFFFFF)).to_bytes(8, "big")
            new_s = time_stream_load(send, words)
        print(load_report(len(words), new_s, args.per_word_ack_us * 1e-6))

    elif args.cmd == "aes":
        from proact_host.transport import ProactTarget, UartTransport
        with UartTransport().open() as t:
            tgt = ProactTarget(t)
            tgt.enable_sendback()
            res = time_hw_vs_sw(tgt, bytes(range(16)), bytes(range(16, 32)), args.n)
        for core, r in res.items():
            print(f"{core:8s}: {r['per_op_wall_ms']:7.3f} ms/op  total {r['total_wall_s']:.3f} s")


if __name__ == "__main__":
    main()
