"""
ChipWhisperer (Husky scope + CW305 FPGA) support, platform-aware.

Two target platforms share the same PROACT design and the same UART/SPI software,
but the CLOCK and the programming differ:

  * ASIC  -- the fabricated chip on the CW308/PCB needs an external clock, so the
             Husky GENERATES the target clock on HS2 (clkgen, default 50 MHz).
  * FPGA  -- the CW305 (Artix-7) runs the PROACT bitstream and provides the clock
             from its OWN on-board PLL (output 1, 50 MHz). HS2 is disabled and the
             scope samples the external target clock (extclk x4). You must upload
             the PROACT bitstream (PROACT_top.bit) to the CW305 first.

`chipwhisperer` is imported lazily so this module imports without it installed.
The CW305/PLL sequence mirrors the professor's bring-up snippet; it follows the
standard ChipWhisperer 6 API and must be confirmed on the bench.
"""
import sys
from typing import Dict, Optional

HUSKY_SPI_CS_GPIO = 3           # Husky GPIO3 = SPI select (per the PCB wiring)
DEFAULT_CLOCK_HZ = 50_000_000
# CW305 FPGA IDs by board variant
CW305_FPGA_IDS = {"CW305_A100": "100t", "CW305_A35": "35t"}

# Per-core ADC gain (mode, dB). The hardware AES cores need a MODEST gain because
# their last round leaks in the first ~100 samples, which clip first. The Sw-RV
# target runs software AES whose leakage sits far from that leading edge, so it
# wants a higher gain -- at low/10 dB a bench capture used only 28% of the ADC
# range with zero clipping, which costs SNR (and therefore traces) for nothing.
RECOMMENDED_GAIN = {
    "aes1": ("low", 10.0),
    "aes2": ("low", 10.0),
    "ascon": ("low", 10.0),
    "xoodyak": ("low", 10.0),
    "swrv": ("low", 20.0),      # ~3x amplitude vs 10 dB; verify clip_percent() == 0
}


def recommended_samples(trigger_cycles: int, adc_mul: int = 4, margin: float = 1.15) -> int:
    """Samples needed to cover a whole trigger window.

    The scope digitizes `adc_mul` samples per target clock cycle, so a window of
    `trigger_cycles` (what the on-chip timer counts while trigger_Out is high)
    needs `trigger_cycles * adc_mul` samples. Setting fewer SILENTLY TRUNCATES the
    capture: e.g. a 773-cycle window at adc_mul=4 needs 3092 samples, so a 1200-
    sample capture keeps only the first 300 cycles (39%) and everything leaking
    later -- for byte-serial software AES, most of the key bytes -- is never
    digitized and can never be recovered, no matter how many traces are taken.
    """
    return int(trigger_cycles * adc_mul * margin)


class ChipWhispererCapture:
    def __init__(self, samples: int = 5000, adc_mul: int = 4,
                 clock_hz: float = DEFAULT_CLOCK_HZ, platform: str = "asic",
                 uart_via_husky: bool = False, gain_db: float = 10.0,
                 gain_mode: str = "low"):
        self.samples = samples
        self.adc_mul = adc_mul
        self.clock_hz = clock_hz
        self.platform = platform          # "asic" or "fpga"
        self.uart_via_husky = uart_via_husky  # False => MCP2200 owns the UART
        # ADC gain. The default (low / 10 dB) is tuned for the HARDWARE AES cores:
        # high gain clips the leading samples, and the AES last round leaks in the
        # first ~100 samples -- clipping there silently destroys the CPA leakage.
        # 10 dB is bench-verified clip-free on the CW305 (clip% = 0.00 over 5000
        # AES-1 traces).
        #
        # That tuning is WRONG for the Sw-RV target: its software-AES leakage sits
        # hundreds of samples into the window, far from the clipping-critical
        # leading edge, so low gain merely wastes ADC range. A bench-measured Sw-RV
        # capture at low/10 dB used only 28% of full scale with 0% clipping and gave
        # rho ~= 0.07 (needing >10k traces); filling the range raises rho and cuts
        # the trace count roughly as 1/rho^2. Use RECOMMENDED_GAIN[core] (below),
        # then confirm clip_percent() stays 0.
        self.gain_db = gain_db
        self.gain_mode = gain_mode
        self.scope = None
        self.target = None

    # ------------------------------------------------------------ connection
    def connect(self, clock_hz: Optional[float] = None, platform: Optional[str] = None,
                bitstream: Optional[str] = None, fpga_id: str = "100t",
                force: bool = True):
        """Connect the Husky scope. For ASIC, generate the target clock on HS2.
        For FPGA, disable HS2 and (if `bitstream` given) program the CW305.

        Pass `bitstream=None` to ATTACH to an already-configured CW305 without
        touching the fabric. When a `bitstream` IS given, `force` (default True)
        controls whether it is uploaded even if the FPGA already holds a
        configuration -- keep it True unless you deliberately want to skip the
        upload for a board you know already holds this exact build."""
        try:
            import chipwhisperer as cw
        except ImportError as e:
            raise RuntimeError("chipwhisperer is not installed: pip install chipwhisperer") from e
        if platform:
            self.platform = platform
        self.scope = cw.scope()
        if self.scope is None:
            msg = "no ChipWhisperer scope found -- check the USB cable and that the Husky is powered."
            if sys.platform == "linux":
                msg += " Ensure the udev rules are installed (sudo bash tools/install_udev.sh, then replug)."
            raise RuntimeError(msg)
        try:
            self.scope.default_setup()
        except Exception as e:  # noqa: BLE001 -- known Husky/Trace default_setup bug
            if "clock" not in str(e):
                raise
        # common scope setup
        self.scope.adc.samples = self.samples
        self.scope.adc.offset = 0
        self.scope.adc.basic_mode = "rising_edge"
        self.scope.trigger.triggers = "tio4"      # PROACT trigger_Out on tio4
        # UART routing: by default DON'T let the Husky drive tio1/tio2 -- on this
        # bench the UART is the MCP2200, and driving these lines would contend on
        # the PROACT RX/TX. Set uart_via_husky=True to use the ChipWhisperer's
        # own serial instead.
        if self.uart_via_husky:
            self.scope.io.tio1 = "serial_rx"
            self.scope.io.tio2 = "serial_tx"
        else:
            try:
                self.scope.io.tio1 = "high_z"
                self.scope.io.tio2 = "high_z"
            except Exception:  # noqa: BLE001
                pass
        if hasattr(self.scope, "gain"):
            try:
                self.scope.gain.mode = self.gain_mode   # "low" avoids clipping the leading samples
                self.scope.gain.db = self.gain_db
            except Exception:  # noqa: BLE001 -- older CW-Lite gain API
                self.scope.gain.gain = 22

        if self.platform == "fpga":
            self.scope.io.hs2 = "disabled"          # CW305 provides its own clock
            if bitstream:
                self.program_fpga(bitstream, fpga_id, clock_hz or self.clock_hz, force=force)
            else:
                # Attaching to an ALREADY-programmed CW305: don't touch the FPGA,
                # just lock the Husky ADC to the external target clock. Without
                # this, default_setup()'s internal clkgen (~7 MHz) stays active
                # and the ADC samples at the wrong rate.
                self.sync_adc_extclk(clock_hz or self.clock_hz)
        else:                                        # ASIC: Husky drives the clock
            self.set_clock(clock_hz or self.clock_hz)
        return self

    def disconnect(self):
        for obj in (self.target, self.scope):
            try:
                if obj is not None:
                    obj.dis()
            except Exception:  # noqa: BLE001
                pass
        self.scope = None
        self.target = None

    @property
    def is_connected(self) -> bool:
        return self.scope is not None

    # ---------------------------------------------------- FPGA (CW305) program
    def program_fpga(self, bitstream: str, fpga_id: str = "100t",
                     freq_hz: float = DEFAULT_CLOCK_HZ, force: bool = True):
        """Upload the PROACT bitstream to a CW305 and bring up its PLL clock.
        Mirrors the professor's CW305 bring-up: VCCINT 1.0 V, PLL1 output 1 at
        `freq_hz`, HS2 disabled, scope ADC clocked from the external target clock.

        `force` must default to True: cw.target(force=False) skips the upload
        whenever the FPGA already holds *any* configuration, so a board left
        with a previous bitstream (the ChipWhisperer AES demo, say) silently
        kept running it while this call still reported success -- and every
        later UART command then failed with "no frame marker"."""
        import time
        import chipwhisperer as cw
        s = self.scope
        if s is None:
            raise RuntimeError("ChipWhisperer scope not connected")
        s.io.hs2 = "disabled"
        self.target = cw.target(s, cw.targets.CW305, bsfile=bitstream,
                                force=force, fpga_id=fpga_id)
        time.sleep(0.5)
        self.target.vccint_set(1.0)                  # 1.0 V core supply
        self.target.pll.pll_enable_set(True)         # use PLL, output 1 only
        self.target.pll.pll_outenable_set(False, 0)
        self.target.pll.pll_outenable_set(True, 1)
        self.target.pll.pll_outenable_set(False, 2)
        self.target.pll.pll_outfreq_set(freq_hz, 1)  # 50 MHz
        self.target.clkusbautooff = True             # reduce trace noise
        self.target.clksleeptime = 1
        self.clock_hz = freq_hz
        # Husky: sync the ADC to the EXTERNAL target clock (the CW305 PLL) at
        # adc_mul x it. (On the Husky use clkgen_src="extclk" + adc_mul --
        # the old adc_src="extclk_x4" is CW-Lite-only and leaves the ADC at the
        # internal default.) This gives adc_freq = freq_hz * adc_mul, locked.
        import time as _t
        _t.sleep(0.3)
        try:
            s.clock.clkgen_src = "extclk"       # switch source FIRST
            s.clock.clkgen_freq = freq_hz       # then tell it the external freq
            s.clock.adc_mul = self.adc_mul      # ADC at adc_mul x the target clock
            s.clock.reset_adc()
            _t.sleep(0.3)
        except Exception:  # noqa: BLE001
            pass
        return self.target

    # --------------------------------------------------------------- clocking
    def sync_adc_extclk(self, freq_hz: Optional[float] = None):
        """FPGA path when the CW305 is ALREADY programmed and running (no
        re-flash): just sync the Husky ADC to the external target clock at
        adc_mul x it. Same clock bring-up as program_fpga(), factored out so a
        capture can be taken on a board that is already up."""
        import time
        freq_hz = float(freq_hz or self.clock_hz)
        self.clock_hz = freq_hz
        s = self.scope
        if s is None:
            raise RuntimeError("ChipWhisperer scope not connected")
        s.io.hs2 = "disabled"
        s.clock.clkgen_src = "extclk"
        s.clock.clkgen_freq = freq_hz
        s.clock.adc_mul = self.adc_mul
        time.sleep(0.2)
        try:
            s.clock.reset_adc()
            time.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
        return bool(getattr(s.clock, "adc_locked", False))

    def set_clock(self, freq_hz: float):
        """ASIC path: generate `freq_hz` on HS2 from the Husky's internal
        oscillator, sampling at adc_mul x that."""
        import time
        self.clock_hz = freq_hz
        s = self.scope
        if s is None:
            raise RuntimeError("ChipWhisperer scope not connected")
        s.clock.clkgen_src = "system"
        s.clock.clkgen_freq = freq_hz
        s.clock.adc_mul = self.adc_mul
        s.io.hs2 = "clkgen"
        time.sleep(0.2)
        try:
            s.clock.reset_adc()
        except Exception:  # noqa: BLE001
            pass
        if not bool(getattr(s.clock, "clkgen_locked", True)):
            import warnings
            warnings.warn("ChipWhisperer clkgen did NOT lock at %.3f MHz -- "
                          "traces will be invalid" % (freq_hz / 1e6))

    def clock_status(self) -> Dict[str, object]:
        s = self.scope
        st = {
            "platform": self.platform,
            "target_clock_MHz": round(self.clock_hz / 1e6, 6),
        }
        if s is None:
            st["clock_source"] = "not connected"
            st["locked"] = False
            return st

        st["adc_freq_MHz"] = round(float(s.clock.adc_freq) / 1e6, 6)
        st["hs2"] = str(s.io.hs2)

        if self.platform == "fpga" and self.target is not None:
            try:
                st["pll_freq_MHz"] = round(float(self.target.pll.pll_outfreq_get(1)) / 1e6, 6)
                st["clock_source"] = "CW305 PLL (output 1)"
                st["locked"] = bool(getattr(s.clock, "adc_locked", False))
            except Exception:  # noqa: BLE001
                st["clock_source"] = "CW305 PLL"
        elif self.platform == "fpga":
            # attached to an already-programmed CW305 (no cw.target handle):
            # ADC is locked to the external target clock via sync_adc_extclk()
            st["clock_source"] = "CW305 clock (ADC synced via extclk)"
            st["locked"] = bool(getattr(s.clock, "adc_locked", False))
        else:
            try:
                st["clkgen_freq_MHz"] = round(float(s.clock.clkgen_freq) / 1e6, 6)
                st["clock_source"] = "Husky HS2 clkgen"
                st["locked"] = bool(s.clock.clkgen_locked)
            except Exception:  # noqa: BLE001
                st["clock_source"] = "Husky HS2"
        return st

    # ---------------------------------------------------------------- capture
    def arm(self):
        if self.scope is None:
            raise RuntimeError("ChipWhisperer scope not connected")
        self.scope.arm()

    def capture(self, timeout: float = 5.0):
        if self.scope is None:
            raise RuntimeError("ChipWhisperer scope not connected")
        if self.scope.capture(poll_done=True):
            raise TimeoutError("scope capture timed out (no trigger?)")
        return self.scope.get_last_trace()

    @staticmethod
    def trace_quality(traces) -> Dict[str, float]:
        """Report ADC usage for a batch of traces: are they clipped, or too small?

        The Husky returns samples in -0.5..+0.5 (full scale 1.0). Good captures
        fill most of that range WITHOUT clipping. Two failure modes, both of which
        cost key bytes:
          * clip_percent > 0  -- gain too high; the clipped samples lose leakage.
          * full_scale_percent very low (e.g. 28%) -- gain too low; ADC resolution
            is wasted, the correlation shrinks and the attack needs far more traces.
        Aim for roughly 70-90% of full scale with clip_percent == 0.
        """
        import numpy as np
        a = np.asarray(traces, dtype=float)
        if a.size == 0:
            return {"clip_percent": 0.0, "full_scale_percent": 0.0, "std": 0.0}
        return {
            "clip_percent": float(100.0 * np.mean(np.abs(a) > 0.49)),
            "full_scale_percent": float(100.0 * (a.max() - a.min()) / 1.0),
            "std": float(a.std()),
        }

    # ------------------------------------------ Husky as SPI/UART transport
    def husky_spi(self):
        """Husky-backed SPI (GPIO3 = CS) -- PCB option, bench-verify."""
        raise NotImplementedError(
            "Husky SPI routing (GPIO%d = CS) is a bench-verify feature." % HUSKY_SPI_CS_GPIO)
