"""
High-level experiment orchestration -- the single entry point that ties the
whole workflow together so a user needs only a few lines:

    from proact_host.experiment import PROACTExperiment
    exp = PROACTExperiment(platform="asic", target="aes1", traces=1000,
                           output="results/aes1")
    exp.prepare()
    exp.capture()
    exp.save()

The CLI uses this orchestration class. The GUI has its own worker loop and
shares the transport, capture, storage and validation modules. The acquisition
order is configure inputs -> arm scope -> run op -> wait done
-> read result -> validate -> store, repeated per trace.

A requested scope must connect successfully; failure aborts preparation and
closes acquired handles. Functional-only runs require explicit capture=False
and store outputs with an empty waveform. Missing hardware never silently
converts a requested waveform acquisition into a functional-only run.
"""
import secrets
import math
import time
from typing import Optional

from .storage import TraceStore
from .transport import ProactTarget, UartTransport
from .validation import validate_aes, validate_aead

_AEAD = ("ascon", "xoodyak")


class PROACTExperiment:
    def __init__(self, platform: str = "asic", target: str = "aes1",
                 traces: int = 1, output: str = "results/run",
                 key: Optional[bytes] = None, fixed_input: Optional[bytes] = None,
                 randomize: bool = True, decrypt: bool = False,
                 capture: bool = True, samples: int = 5000,
                 port: Optional[str] = None, clock_hz: float = 50e6,
                 bitstream: Optional[str] = None,
                 gain_db: Optional[float] = None, gain_mode: Optional[str] = None,
                 auto_samples: bool = True):
        if platform not in ("asic", "fpga"):
            raise ValueError("platform must be asic or fpga")
        if target.lower() not in ("aes1", "aes2", "ascon", "xoodyak", "swrv"):
            raise ValueError("unknown target")
        for name, value in (("traces", traces), ("samples", samples)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(clock_hz) or clock_hz <= 0:
            raise ValueError("clock_hz must be a positive finite number")
        def block(value, name):
            if value is None:
                return None
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"{name} must be bytes-like and contain exactly 16 bytes")
            owned = bytes(value)
            if len(owned) != 16:
                raise ValueError(f"{name} must contain exactly 16 bytes")
            return owned
        key = block(key, "key")
        fixed_input = block(fixed_input, "fixed_input")
        if decrypt and target.lower() in _AEAD:
            raise ValueError("AEAD hardware supports encryption only; use aead_soft for decryption")
        self.platform = platform
        self.target = target.lower()
        self.n = traces
        self.output = output
        self.key = bytes(key) if key is not None else bytes(range(16))
        self.fixed_input = bytes(fixed_input) if fixed_input is not None else None
        self.randomize = randomize and fixed_input is None
        self.decrypt = decrypt
        self.want_capture = capture
        self.samples = samples
        self.port = port
        self.clock_hz = clock_hz
        self.bitstream = bitstream
        # Gain defaults come from the existing per-core configuration. They are
        # starting settings, not a claim about an arbitrary board or dataset.
        self.gain_db = gain_db
        self.gain_mode = gain_mode
        self.auto_samples = auto_samples

        self.uart = None
        self.chip = None
        self.scope = None
        self.store: Optional[TraceStore] = None

    # -------------------------------------------------------------- prepare
    def prepare(self):
        """Connect UART (+scope if requested), select the target, open storage."""
        if self.uart is not None or self.scope is not None:
            raise RuntimeError("experiment is already prepared; close it before preparing again")
        try:
            self.uart = UartTransport(port=self.port).open()
            self.chip = ProactTarget(self.uart)
            self.chip.enable_sendback()
            self.chip.select(self.target)
            self.chip.set_key(self.key)
            if self.target in _AEAD:
                self.chip.set_nonce(bytes(16))
                self.chip.set_ad(bytes(16))
            self.chip.set_decrypt(self.decrypt)

            if self.want_capture:
                from .capture import ChipWhispererCapture, RECOMMENDED_GAIN
                mode, db = RECOMMENDED_GAIN.get(self.target, ("low", 10.0))
                self.scope = ChipWhispererCapture(
                    samples=self.samples, clock_hz=self.clock_hz,
                    platform=self.platform,
                    gain_db=self.gain_db if self.gain_db is not None else db,
                    gain_mode=self.gain_mode or mode,
                )
                self.scope.connect(clock_hz=self.clock_hz, platform=self.platform,
                                   bitstream=(self.bitstream if self.platform == "fpga" else None))

            meta = {
                "platform": self.platform, "target": self.target,
                "traces_requested": self.n, "decrypt": self.decrypt,
                "key": self.key.hex(), "randomize": self.randomize,
                "samples": self.samples, "trigger": "core (bit30 / Sw-RV status bit31)",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            # record the scope settings so a time axis / alignment can be reconstructed
            if self.scope is not None:
                try:
                    cs = self.scope.clock_status()
                    meta.update({"clock_hz": self.scope.clock_hz, "adc_mul": self.scope.adc_mul,
                                 "adc_freq_MHz": cs.get("adc_freq_MHz"),
                                 "clkgen_locked": cs.get("clkgen_locked")})
                except Exception:  # noqa: BLE001
                    pass
            self.store = TraceStore(self.output, meta)
            return self
        except BaseException:
            self.close()
            raise

    # -------------------------------------------------------------- capture
    def _next_input(self) -> bytes:
        if self.fixed_input is not None:
            return self.fixed_input
        if self.randomize:
            return secrets.token_bytes(16)
        return bytes(range(16))

    def _fit_samples_to_trigger_window(self):
        """Grow `samples` so the capture spans the WHOLE trigger window.

        The on-chip timer counts target-clock cycles while trigger_Out is high, and
        the scope digitizes adc_mul samples per cycle, so the window needs
        cycles*adc_mul samples. A too-small setting silently truncates the trace and
        the leakage past the cut is unrecoverable at ANY trace count -- the usual
        cause of "only some key bytes come out". Runs one throw-away operation to
        measure the window, before any trace is stored.
        """
        from .capture import recommended_samples
        try:
            pt = self._next_input()
            self.chip.set_plaintext(pt)
            if self.scope is not None:
                self.scope.arm()
            self.chip.run_and_read()
            if self.scope is not None:
                self.scope.capture()
            cycles = self.chip.get_timer()
        except Exception:  # noqa: BLE001 -- measurement is best-effort
            return
        if not cycles or cycles <= 0:
            return
        need = recommended_samples(cycles, adc_mul=self.scope.adc_mul)
        if need > self.samples:
            print(f"  [trigger window = {cycles} cycles x{self.scope.adc_mul} = "
                  f"{cycles * self.scope.adc_mul} samples; growing samples "
                  f"{self.samples} -> {need}]")
            self.samples = need
            try:
                self.scope.samples = need
                self.scope.scope.adc.samples = need
            except Exception:  # noqa: BLE001
                pass

    def capture(self, save_every: int = 50):
        """Run N operations, capturing a trace + result each, validating AES."""
        if self.chip is None:
            raise RuntimeError("call prepare() first")
        if not isinstance(save_every, int) or isinstance(save_every, bool) or save_every <= 0:
            raise ValueError("save_every must be a positive integer")
        if self.auto_samples and self.scope is not None:
            self._fit_samples_to_trigger_window()
        done = 0
        for i in range(self.n):
            pt = self._next_input()
            self.chip.set_plaintext(pt)
            try:
                if self.scope is not None:
                    self.scope.arm()
                mode, payload = self.chip.run_and_read()
                trace = self.scope.capture() if self.scope is not None else []
            except Exception as e:  # noqa: BLE001
                self.store.record_failure(i, str(e))
                continue

            out = payload if self.target in _AEAD else payload[:16]
            expected = None
            valid = None
            if self.target in ("aes1", "aes2", "swrv"):
                expected = None  # firmware software-reference compare
                valid = validate_aes(self.key, pt, out, decrypt=self.decrypt)
            elif self.target in _AEAD:
                valid = validate_aead(self.target, self.key, pt, payload, decrypt=self.decrypt)
            self.store.append(trace, pt, self.key, out, expected, valid)
            done += 1
            if done % save_every == 0:
                self.store.flush()
                print(f"  {done}/{self.n} captured")
        self.store.flush()
        return done

    # -------------------------------------------------------------- save
    def save(self) -> str:
        path = self.store.close()
        print(f"saved {self.store.count} traces -> {path}")
        return path

    def close(self):
        scope, uart = self.scope, self.uart
        self.scope = self.uart = self.chip = None
        try:
            if scope is not None:
                scope.disconnect()
        finally:
            if uart is not None:
                uart.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
