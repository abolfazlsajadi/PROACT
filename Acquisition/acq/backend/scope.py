"""Generic oscilloscope backend over pyvisa (L1.2, L1.11).

Implements the Backend contract for a bench scope triggered by the chip's TRIGGER
pin. The implemented command sets are limited to Keysight InfiniiVision 3000 X-Series
and Tektronix 4/5/6 Series MSO. Written from those vendor programmer manuals.

*** NOT hardware-verified: no oscilloscope is connected. The SCPI is standard and the
    structure matches HuskyBackend so the rest of the framework needs no change, but
    do not label this hardware-tested until it is run on a real instrument. ***

WIRING (what the user must do physically):
  * Feed the per-core TRIGGER output (chip PIN, the cfg trigger) into the scope's
    external-trigger input (Tek 'Aux In', Keysight rear 'Ext Trig') OR, recommended,
    into a spare analog channel (e.g. CH2) via 50 Ohm coax.
  * Feed the power signal (Husky's measure point / a current probe / shunt amp) into
    CH1. Keep the trigger input high-Z (1 MOhm) for a raw 3.3 V CMOS edge.
  * Set --trigger-source to AUX/EXT or CH2 accordingly.

KEY CORRECTNESS RULE (from research): use NORMAL/single-shot trigger, never AUTO --
AUTO free-runs and stores untriggered noise records that look valid and poison the
dataset. capture() must raise TimeoutError on a missed trigger so the loop retries.
"""
from __future__ import annotations
import time
import re
from typing import Optional
import numpy as np
from .base import Backend


_CONTROL_TIMEOUT_MS = 8_000
_AUTO_TRANSFER_BYTES_PER_SECOND = 250_000
_KEYSIGHT_3000_X_MODEL = re.compile(r"(?:DSO|MSO)X3[0-9]{3}[A-Z]*")
_TEK_456_MODELS = frozenset({
    "MSO44", "MSO46", "MSO44B", "MSO46B",
    "MSO54", "MSO56", "MSO58", "MSO54B", "MSO56B", "MSO58B", "MSO58LP",
    "MSO64", "MSO64B", "MSO66B", "MSO68B", "LPD64",
})
# Original bench-format 5 Series instruments have no independent AUX input.
# Tek documents AUX on 4 Series, 5 Series B, MSO58LP, and 6 Series instruments.
_TEK_AUX_MODELS = _TEK_456_MODELS - {"MSO54", "MSO56", "MSO58"}


class ScopeBackend(Backend):
    name = "scope"

    def __init__(self, resource: Optional[str] = None, dialect: str = "auto",
                 channel: int = 1, trig_source: str = "EXTernal", trig_level: float = 1.5,
                 clock_source: str = "husky", clock_driver_factory=None,
                 transfer_timeout_ms: Optional[int] = None):
        # Keysight is the primary target (per user); Tektronix is also supported.
        # trig_source default EXTernal (rear Ext-Trig) -- use CH2 if you route the
        # trigger into a spare channel instead.
        try:
            import pyvisa
        except Exception as e:
            raise SystemExit(f"pyvisa not available: {e}")
        self.pyvisa = pyvisa
        self.resource = resource
        self.dialect = dialect
        self.ch = channel
        self.trig_source = trig_source
        self.trig_level = trig_level
        self.clock_source = clock_source
        if transfer_timeout_ms is not None and (
                isinstance(transfer_timeout_ms, bool) or
                int(transfer_timeout_ms) != transfer_timeout_ms or
                int(transfer_timeout_ms) <= 0):
            raise ValueError("scope transfer timeout must be a positive integer in ms")
        self._requested_transfer_timeout_ms = (
            None if transfer_timeout_ms is None else int(transfer_timeout_ms))
        self._clock_driver_factory = clock_driver_factory
        self._clock_driver = None
        self._clock_status = {}
        self.inst = None
        self.rm = None
        self._pre = None            # cached (ymult, yoff, yzero) so we never re-query per shot
        self._npts = None
        self._requested_sample_interval_s = None
        self._observed_sample_interval_s = None
        self._observed_sample_count = None
        self._arm_timeout_s = 2.0
        self._last_clip = False
        self._idn = ""
        self._model = ""
        self._applied_trigger_source = None
        self._observed_trigger_level_v = None
        self._observed_acquisition_sample_rate_hz = None
        self._last_trigger_index_samples = None
        self._nominal_trigger_index_samples = None
        self._horizontal_metadata = {}
        self._vertical_metadata = {}
        self._immutable_capture_state = None

    # ------------------------------------------------------------------ connect
    def _open(self):
        rm = self.pyvisa.ResourceManager("@py")
        self.rm = rm
        res = self.resource
        if res is None:
            candidates = [r for r in rm.list_resources() if "INSTR" in r]
            if not candidates:
                raise SystemExit("no VISA instruments found; pass --scope-resource")
            if len(candidates) != 1:
                raise SystemExit(
                    "multiple VISA instruments found; pass the exact --scope-resource")
            res = candidates[0]
        self.inst = rm.open_resource(res)
        self.inst.timeout = _CONTROL_TIMEOUT_MS
        # For message-based ::INSTR (USBTMC/VXI-11/HiSLIP) termination is handled by
        # the protocol (END/EOI); setting read_termination there can TRUNCATE binary
        # waveform reads. Only a raw ::SOCKET needs explicit '\n' framing.
        if res.upper().endswith("SOCKET"):
            self.inst.read_termination = "\n"
            self.inst.write_termination = "\n"
        else:
            self.inst.write_termination = "\n"
        self.inst.write("*CLS")
        idn = self.inst.query("*IDN?")
        vendor = str(idn).upper()
        if self.dialect == "auto":
            if "KEYSIGHT" in vendor or "AGILENT" in vendor:
                self.dialect = "keysight"
            elif "TEKTRONIX" in vendor:
                self.dialect = "tek"
            else:
                raise RuntimeError(
                    "scope: cannot auto-detect a supported Keysight/Tektronix "
                    "dialect from *IDN?; pass --scope-dialect explicitly only "
                    "after checking the instrument programmer manual")
        elif self.dialect == "keysight" and not (
                "KEYSIGHT" in vendor or "AGILENT" in vendor):
            raise RuntimeError(
                "scope: requested Keysight dialect does not match instrument *IDN?")
        elif self.dialect == "tek" and "TEKTRONIX" not in vendor:
            raise RuntimeError(
                "scope: requested Tektronix dialect does not match instrument *IDN?")
        self._model = _require_supported_scope_model(idn, self.dialect)
        return idn

    # ------------------------------------------------------------------ configure
    def configure(self, samples, offset, gain_db, clock_hz, adc_mul):
        """Start the board clock, then configure the waveform instrument."""
        self._configure_board_clock(clock_hz, adc_mul)
        try:
            return self._configure_scope(samples, offset, gain_db, clock_hz, adc_mul)
        except BaseException:
            self.close()
            raise

    def _configure_board_clock(self, clock_hz, adc_mul):
        self._close_clock_driver()
        source = getattr(self, "clock_source", "external")
        self.clock_source = source
        if source == "external":
            self._clock_status = {
                "source": "external",
                "target_clock_MHz": float(clock_hz) / 1e6,
                "verified": False,
            }
            return
        if source != "husky":
            raise ValueError("scope clock source must be husky or external")
        from .clock import HuskyClockOutput
        factory = getattr(self, "_clock_driver_factory", None) or HuskyClockOutput
        driver = factory()
        self._clock_driver = driver
        try:
            self._clock_status = dict(driver.configure(clock_hz, adc_mul))
        except BaseException:
            self._close_clock_driver()
            raise

    def _configure_scope(self, samples, offset, gain_db, clock_hz, adc_mul):
        idn = self._open()
        self._idn = str(idn).strip()
        n = max(int(samples), 100)
        self._npts = n
        # Both supported setup policies place the trigger at one horizontal
        # division (10% of the record).  Use that configured location as the
        # cross-session alignment reference.  A configure-time preamble is an
        # observation of one asynchronous acquisition and can move by a
        # fractional sample after reconnecting, so it must not define the
        # durable reference used by resumed rows.
        self._nominal_trigger_index_samples = n * 0.1
        self._requested_sample_interval_s = 1.0 / float(clock_hz * adc_mul)
        self._horizontal_metadata = {}
        self._vertical_metadata = {}
        d = self.dialect
        c = self.ch
        srate = clock_hz * adc_mul
        if d == "tek":
            w = self.inst.write
            trigger_source, trigger_kind = _tek_trigger_source(self.trig_source)
            if trigger_kind == "aux" and self._model not in _TEK_AUX_MODELS:
                raise RuntimeError(
                    f"scope: Tektronix {self._model} has no supported AUX trigger input; "
                    "connect the trigger to a spare analog channel such as CH2")
            if trigger_kind == "channel" and _channel_number(trigger_source) == c:
                raise ValueError(
                    "scope measurement channel and trigger channel must be different")
            self._applied_trigger_source = trigger_source
            # Tek records SCPI errors in its event queue only when their event
            # classes are enabled.  Force and verify all device events before any
            # setup command so a stale DESE 0 cannot hide a rejected command.
            w("DESE 255")
            _require_scope_integer(
                self.inst.query("DESE?"), 255,
                "Tektronix device-event status enable mask")
            w("HEADer OFF")
            w("ACQuire:STATE STOP")
            self.inst.query("*OPC?")
            _require_scope_integer(
                self.inst.query("ACQuire:STATE?"), 0,
                "Tektronix stopped state before setup")
            w(f"DISPlay:WAVEView1:CH{c}:STATE 1")
            trigger_channel = (
                _channel_number(trigger_source) if trigger_kind == "channel" else None)
            if trigger_channel is not None:
                w(f"DISPlay:WAVEView1:CH{trigger_channel}:STATE 1")
                # A raw CMOS trigger must never inherit a 50-ohm front-panel
                # termination: that can load or damage the target output.
                w(f"CH{trigger_channel}:COUPling DC")
                w(f"CH{trigger_channel}:TERmination 1000000")
            # These acquisition modes persist across front-panel sessions.  Pin
            # them off so one RUN command always means one ordinary waveform.
            w("HORizontal:FASTframe:STATE OFF")
            w("ACQuire:FASTAcq:STATE OFF")
            w("HORizontal:MODE MANual")
            w("HORizontal:DELay:MODe OFF")
            w("HORizontal:POSition 10")
            w(f"HORizontal:MODE:SAMPLERate {srate:g}")
            w(f"HORizontal:MODE:RECOrdlength {n}")
            w("ACQuire:MODe SAMple")
            w(f"DATa:SOUrce CH{c}"); w("DATa:ENCdg SRIbinary"); w("DATa:WIDth 1")
            w("DATa:STARt 1"); w(f"DATa:STOP {n}")
            # single-shot NORMAL edge trigger from the chip trigger pin
            w("TRIGger:A:TYPe EDGE")
            w(f"TRIGger:A:EDGE:SOUrce {trigger_source}")
            w("TRIGger:A:EDGE:SLOpe RISe")
            w("TRIGger:A:EDGE:COUPling DC")
            w("TRIGger:A:MODe NORMal")
            if trigger_kind == "channel":
                w(f"TRIGger:A:LEVel:{trigger_source} {self.trig_level:g}")
                trigger_level_query = f"TRIGger:A:LEVel:{trigger_source}?"
            elif trigger_kind == "aux":
                w(f"TRIGger:AUXLevel {self.trig_level:g}")
                trigger_level_query = "TRIGger:AUXLevel?"
            else:
                trigger_level_query = None
            w("ACQuire:SEQuence:NUMSEQuence 1")
            w("ACQuire:STOPAfter SEQuence")
            self.inst.query("*OPC?")
            _require_tek_error_free(self.inst)
            observed_rate = _require_scope_number(
                self.inst.query("HORizontal:MODE:SAMPLERate?"), srate,
                "Tektronix sample rate", relative_tolerance=0.02)
            _require_scope_integer(
                self.inst.query("HORizontal:MODE:RECOrdlength?"), n,
                "Tektronix record length")
            _require_scope_integer(
                self.inst.query("DATa:STOP?"), n,
                "Tektronix transfer stop")
            _require_scope_setting(
                self.inst.query("ACQuire:MODe?"), "SAMPLE",
                "Tektronix acquisition mode")
            _require_scope_setting(
                self.inst.query("HORizontal:FASTframe:STATE?"), "OFF",
                "Tektronix FastFrame state")
            _require_scope_setting(
                self.inst.query("ACQuire:FASTAcq:STATE?"), "OFF",
                "Tektronix FastAcq state")
            _require_scope_setting(
                self.inst.query("DATa:SOUrce?"), f"CH{c}",
                "Tektronix waveform source")
            _require_scope_setting(
                self.inst.query("DATa:ENCdg?"), "SRIBINARY",
                "Tektronix waveform encoding")
            _require_scope_integer(
                self.inst.query("DATa:WIDth?"), 1,
                "Tektronix waveform width")
            _require_scope_integer(
                self.inst.query("DATa:STARt?"), 1,
                "Tektronix transfer start")
            _require_scope_setting(
                self.inst.query("HORizontal:DELay:MODe?"), "OFF",
                "Tektronix horizontal delay mode")
            observed_position = _require_scope_number(
                self.inst.query("HORizontal:POSition?"), 10.0,
                "Tektronix trigger position", absolute_tolerance=0.1)
            self._observed_acquisition_sample_rate_hz = observed_rate
            self._horizontal_metadata = {
                "scope_horizontal_delay_mode_observed": "OFF",
                "scope_horizontal_position_percent_observed": observed_position,
                "scope_trigger_position_policy": "10_percent_pretrigger",
            }
            _require_scope_setting(
                self.inst.query("TRIGger:A:EDGE:SOUrce?"), trigger_source,
                "Tektronix trigger source")
            _require_scope_setting(
                self.inst.query("TRIGger:A:TYPe?"), "EDGE",
                "Tektronix trigger type")
            _require_scope_setting(
                self.inst.query("TRIGger:A:EDGE:SLOpe?"), "RISE",
                "Tektronix trigger slope")
            _require_scope_setting(
                self.inst.query("TRIGger:A:MODe?"), "NORMAL",
                "Tektronix trigger mode")
            _require_scope_setting(
                self.inst.query("ACQuire:STOPAfter?"), "SEQUENCE",
                "Tektronix stop-after mode")
            _require_scope_integer(
                self.inst.query("ACQuire:SEQuence:NUMSEQuence?"), 1,
                "Tektronix sequence count")
            if trigger_level_query is not None:
                self._observed_trigger_level_v = _require_scope_float(
                    self.inst.query(trigger_level_query), self.trig_level,
                    "Tektronix trigger level")
            self._vertical_metadata.update(
                _read_tek_channel_metadata(self.inst, c, "measurement"))
            if trigger_channel is not None:
                self._vertical_metadata.update(
                    _read_tek_channel_metadata(
                        self.inst, trigger_channel, "trigger"))
            self._vertical_metadata.update(
                _read_tek_trigger_metadata(
                    self.inst, trigger_kind, trigger_channel))
            self._refresh_waveform_metadata()
            _require_tek_error_free(self.inst)
        else:  # Keysight InfiniiVision 3000 X-Series
            w = self.inst.write
            _require_keysight_points(n)
            trigger_source, trigger_kind = _keysight_trigger_source(self.trig_source)
            if trigger_kind == "channel" and _channel_number(trigger_source) == c:
                raise ValueError(
                    "scope measurement channel and trigger channel must be different")
            self._applied_trigger_source = trigger_source
            w(":STOP")
            self.inst.query("*OPC?")
            stopped = int(self.inst.query(":OPERegister:CONDition?"))
            if stopped & 0x08:
                raise RuntimeError("scope: Keysight did not stop before setup")
            w(f":CHANnel{c}:DISPlay 1")
            trigger_channel = (
                _channel_number(trigger_source) if trigger_kind == "channel" else None)
            if trigger_channel is not None:
                w(f":CHANnel{trigger_channel}:DISPlay 1")
                w(f":CHANnel{trigger_channel}:COUPling DC")
                w(f":CHANnel{trigger_channel}:IMPedance ONEMeg")
            else:
                w(":EXTernal:PROBe 1")
                w(":EXTernal:UNITs VOLT")
                w(":EXTernal:RANGe 8")
            w(f":WAVeform:SOURce CHANnel{c}")
            w(":WAVeform:FORMat BYTE")
            w(":WAVeform:POINts:MODE RAW")
            w(f":WAVeform:POINts {n}")
            w(":ACQuire:MODE RTIMe")
            w(":ACQuire:TYPE NORMal")
            # InfiniiVision ACQuire:SRATe is query-only. Set the time span that
            # corresponds to the requested transfer interval instead, then verify
            # the actual interval in the waveform preamble.
            requested_range_s = n * self._requested_sample_interval_s
            w(":TIMebase:MODE MAIN")
            w(f":TIMebase:RANGe {requested_range_s:.12g}")
            w(":TIMebase:REFerence LEFT")
            w(":TIMebase:POSition 0")
            w(":TRIGger:SWEep NORMal")
            w(":TRIGger:MODE EDGE")
            w(f":TRIGger:EDGE:SOURce {trigger_source}")
            w(":TRIGger:EDGE:SLOPe POSitive")
            w(":TRIGger:EDGE:COUPling DC")
            w(":TRIGger:EDGE:REJect OFF")
            w(":TRIGger:HFReject OFF")
            w(":TRIGger:NREJect OFF")
            w(f":TRIGger:EDGE:LEVel {self.trig_level:g}")
            trigger_level_query = ":TRIGger:EDGE:LEVel?"
            self.inst.query("*OPC?")
            _require_keysight_error_free(self.inst)
            _require_scope_setting(
                self.inst.query(":WAVeform:POINts:MODE?"), "RAW",
                "Keysight waveform points mode")
            _require_scope_setting(
                self.inst.query(":WAVeform:SOURce?"), f"CHANnel{c}",
                "Keysight waveform source")
            _require_scope_setting(
                self.inst.query(":WAVeform:FORMat?"), "BYTE",
                "Keysight waveform format")
            _require_scope_integer(
                self.inst.query(":WAVeform:POINts?"), n,
                "Keysight waveform points")
            _require_scope_setting(
                self.inst.query(":ACQuire:TYPE?"), "NORMAL",
                "Keysight acquisition mode")
            _require_scope_setting(
                self.inst.query(":ACQuire:MODE?"), "RTIME",
                "Keysight realtime acquisition mode")
            _require_scope_setting(
                self.inst.query(":TIMebase:MODE?"), "MAIN",
                "Keysight timebase mode")
            observed_range = _require_scope_number(
                self.inst.query(":TIMebase:RANGe?"), requested_range_s,
                "Keysight timebase range", relative_tolerance=0.02)
            _require_scope_setting(
                self.inst.query(":TIMebase:REFerence?"), "LEFT",
                "Keysight timebase reference")
            observed_position = _require_scope_number(
                self.inst.query(":TIMebase:POSition?"), 0.0,
                "Keysight timebase position", absolute_tolerance=1e-12)
            self._observed_acquisition_sample_rate_hz = _required_calibration_float(
                self.inst.query(":ACQuire:SRATe?"),
                "Keysight acquisition sample rate", positive=True)
            self._horizontal_metadata = {
                "scope_timebase_range_s_observed": observed_range,
                "scope_horizontal_reference_observed": "LEFT",
                "scope_horizontal_position_s_observed": observed_position,
                "scope_trigger_position_policy": (
                    "Keysight LEFT reference, zero position; one-division pretrigger"),
            }
            _require_scope_setting(
                self.inst.query(":TRIGger:EDGE:SOURce?"), trigger_source,
                "Keysight trigger source")
            _require_scope_setting(
                self.inst.query(":TRIGger:MODE?"), "EDGE",
                "Keysight trigger mode")
            _require_scope_setting(
                self.inst.query(":TRIGger:EDGE:SLOPe?"), "POSITIVE",
                "Keysight trigger slope")
            _require_scope_setting(
                self.inst.query(":TRIGger:SWEep?"), "NORMAL",
                "Keysight trigger sweep")
            if trigger_level_query is not None:
                self._observed_trigger_level_v = _require_scope_float(
                    self.inst.query(trigger_level_query), self.trig_level,
                    "Keysight trigger level")
            self._vertical_metadata.update(
                _read_keysight_channel_metadata(self.inst, c, "measurement"))
            if trigger_channel is not None:
                self._vertical_metadata.update(
                    _read_keysight_channel_metadata(
                        self.inst, trigger_channel, "trigger"))
            self._vertical_metadata.update(
                _read_keysight_trigger_metadata(
                    self.inst, trigger_kind, trigger_channel))
            self._refresh_waveform_metadata()
            _require_keysight_error_free(self.inst)
        self._validate_observed_timing(n)
        self._immutable_capture_state = self._read_immutable_capture_state()
        self._require_error_free("immutable-state baseline readback")
        return idn

    def _refresh_waveform_metadata(self):
        """Cache voltage calibration and instrument-observed timing.

        Timing query failures are retained as unavailable metadata rather than
        replaced by the request.  The later exact waveform-length preflight still
        rejects any record-length substitution before storage begins.
        """
        self._observed_sample_interval_s = None
        self._observed_sample_count = None
        if self.dialect == "tek":
            ym = _required_calibration_float(
                self.inst.query("WFMOutpre:YMUlt?"), "Tektronix YMULT",
                positive=True)
            yo = _required_calibration_float(
                self.inst.query("WFMOutpre:YOFf?"), "Tektronix YOFF")
            yz = _required_calibration_float(
                self.inst.query("WFMOutpre:YZEro?"), "Tektronix YZERO")
            self._pre = (ym, yo, yz)
            self._observed_sample_interval_s = _required_calibration_float(
                self.inst.query("WFMOutpre:XINcr?"), "Tektronix X increment",
                positive=True)
            points = _required_calibration_float(
                self.inst.query("WFMOutpre:NR_Pt?"), "Tektronix point count",
                positive=True)
            self._observed_sample_count = int(round(points))
            xzero = _required_calibration_float(
                self.inst.query("WFMOutpre:XZEro?"), "Tektronix X zero")
            pt_off = _required_calibration_float(
                self.inst.query("WFMOutpre:PT_Off?"), "Tektronix trigger point")
            self._horizontal_metadata.update(
                scope_preamble_xzero_s=xzero,
                scope_preamble_pt_off_index=pt_off,
                scope_trigger_index_from_preamble=(
                    pt_off - xzero / self._observed_sample_interval_s))
            return

        raw = self.inst.query(":WAVeform:PREamble?")
        pre = [field.strip() for field in str(raw).split(",")]
        if len(pre) < 10:
            raise RuntimeError(
                "scope: Keysight waveform preamble has fewer than 10 fields")
        # [format,type,points,count,xincr,xorig,xref,yincr,yorig,yref]
        _require_scope_integer(pre[0], 0, "Keysight preamble BYTE format")
        _require_scope_integer(pre[1], 0, "Keysight preamble NORMAL type")
        _require_scope_integer(pre[3], 1, "Keysight preamble acquisition count")
        yincr = _required_calibration_float(
            pre[7], "Keysight Y increment", positive=True)
        yorig = _required_calibration_float(pre[8], "Keysight Y origin")
        yref = _required_calibration_float(pre[9], "Keysight Y reference")
        self._pre = (yincr, yorig, yref)
        observed_interval = _required_calibration_float(
            pre[4], "Keysight X increment", positive=True)
        observed_points = _required_calibration_float(
            pre[2], "Keysight point count", positive=True)
        xorigin = _required_calibration_float(pre[5], "Keysight X origin")
        xreference = _required_calibration_float(pre[6], "Keysight X reference")
        self._observed_sample_interval_s = observed_interval
        self._observed_sample_count = int(round(observed_points))
        self._horizontal_metadata.update(
            scope_preamble_xorigin_s=xorigin,
            scope_preamble_xreference_index=xreference,
            scope_trigger_index_from_preamble=(
                xreference - xorigin / observed_interval))

    def _validate_observed_timing(self, requested_points):
        if self._observed_sample_count != int(requested_points):
            raise RuntimeError(
                "scope: instrument record-length readback mismatch "
                f"(requested {int(requested_points)}, observed "
                f"{self._observed_sample_count})")
        expected = float(self._requested_sample_interval_s)
        observed = float(self._observed_sample_interval_s)
        if abs(observed - expected) > expected * 0.02:
            raise RuntimeError(
                "scope: waveform sample interval differs from the requested 4x clock "
                f"rate by more than 2% (requested {expected:.6g} s, "
                f"observed {observed:.6g} s)")

    def set_samples(self, samples):
        n = max(int(samples), 100)
        self._npts = n
        self._nominal_trigger_index_samples = n * 0.1
        if self.dialect == "tek":
            self.inst.write(f"HORizontal:MODE:RECOrdlength {n}")
            self.inst.write(f"DATa:STOP {n}")
        else:
            _require_keysight_points(n)
            self.inst.write(f":WAVeform:POINts {n}")
            requested_range_s = n * self._requested_sample_interval_s
            self.inst.write(f":TIMebase:RANGe {requested_range_s:.12g}")
        self.inst.query("*OPC?")
        if self.dialect == "tek":
            _require_tek_error_free(self.inst)
            _require_scope_integer(
                self.inst.query("HORizontal:MODE:RECOrdlength?"), n,
                "Tektronix record length")
            _require_scope_integer(
                self.inst.query("DATa:STOP?"), n,
                "Tektronix transfer stop")
            self._observed_acquisition_sample_rate_hz = _required_calibration_float(
                self.inst.query("HORizontal:MODE:SAMPLERate?"),
                "Tektronix acquisition sample rate", positive=True)
        else:
            _require_keysight_error_free(self.inst)
            _require_scope_integer(
                self.inst.query(":WAVeform:POINts?"), n,
                "Keysight waveform points")
            observed_range = _require_scope_number(
                self.inst.query(":TIMebase:RANGe?"), requested_range_s,
                "Keysight timebase range", relative_tolerance=0.02)
            self._horizontal_metadata["scope_timebase_range_s_observed"] = observed_range
            self._observed_acquisition_sample_rate_hz = _required_calibration_float(
                self.inst.query(":ACQuire:SRATe?"),
                "Keysight acquisition sample rate", positive=True)
        self._refresh_waveform_metadata()
        self._validate_observed_timing(n)
        self._immutable_capture_state = self._read_immutable_capture_state()
        self._require_error_free("sample-count immutable-state readback")

    # ------------------------------------------------------------------ acquire
    def arm(self):
        self._last_trigger_index_samples = None
        deadline = time.monotonic() + float(getattr(self, "_arm_timeout_s", 2.0))
        if self.dialect == "tek":
            self.inst.write("ACQuire:STATE RUN")     # single SEQuence
            while True:
                try:
                    state = str(self.inst.query("TRIGger:STATE?")).strip().upper()
                except Exception as exc:
                    raise RuntimeError(
                        "scope: Tektronix arm-ready status query failed") from exc
                # HEADER OFF normally returns READY/ARMED. Some firmware retains
                # a command prefix, so compare the final token as well.
                state = state.rsplit(" ", 1)[-1]
                # ARMED means the scope is still collecting pretrigger data.
                # Only READY certifies that an immediately following chip edge
                # can be accepted as the trigger.
                if state == "READY":
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"scope: Tektronix did not become trigger-ready (state={state})")
                time.sleep(0.001)
        else:
            # Clear any earlier trigger event. A stopped acquisition alone is not
            # proof that the upcoming single shot received a trigger.
            self.inst.query(":TER?")
            # AER is a sticky read-to-clear Arm Event Register.  Clear a delayed
            # event from any earlier arm before issuing this acquisition.
            self.inst.query(":AER?")
            self.inst.write(":SINGle")               # arm one acquisition, NORMAL trig
            while True:
                try:
                    armed = int(self.inst.query(":AER?"))
                except Exception as exc:
                    raise RuntimeError(
                        "scope: Keysight arm-event status query failed") from exc
                if armed == 1:
                    try:
                        condition = int(
                            self.inst.query(":OPERegister:CONDition?"))
                    except Exception as exc:
                        raise RuntimeError(
                            "scope: Keysight run-state query failed after arm event") from exc
                    if condition & 0x08:
                        return
                    raise TimeoutError(
                        "scope: Keysight acquisition triggered or stopped before "
                        "target execution")
                if time.monotonic() >= deadline:
                    raise TimeoutError("scope: Keysight did not become trigger-ready")
                time.sleep(0.001)

    def capture(self, timeout=5.0):
        """Wait for the acquisition to COMPLETE (a real trigger); raise TimeoutError
        on a missed trigger. Then read normalized float32 ADC codes."""
        t0 = time.monotonic()
        if self.dialect == "tek":
            while True:
                state = str(self.inst.query("ACQuire:STATE?")).strip().upper()
                state = state.rsplit(" ", 1)[-1]
                if state in ("0", "OFF"):
                    break
                if state not in ("1", "ON"):
                    raise RuntimeError(
                        f"scope: invalid Tektronix acquisition-state readback {state!r}")
                if time.monotonic() - t0 > timeout:
                    raise TimeoutError("scope: no trigger (ACQuire:STATE never cleared)")
                time.sleep(0.001)
            acquisitions = _numeric_token(self.inst.query("ACQuire:NUMACq?"))
            if acquisitions != round(acquisitions) or acquisitions < 0:
                raise RuntimeError(
                    "scope: invalid Tektronix triggered acquisition count readback")
            if int(acquisitions) != 1:
                raise TimeoutError("scope: Tektronix stopped without one trigger")
            self._last_trigger_index_samples = self._read_capture_trigger_index()
            raw = self._query_waveform(
                "CURVe?", datatype="b", is_big_endian=False)
            ym, yo, yz = self._pre
            self._last_clip = bool(np.any((raw <= -128) | (raw >= 127)))
            # Use the same normalized ADC-code convention as ChipWhisperer.
            # The cached preamble retained below supplies an exact affine map
            # back to physical volts for exported metadata.
            v = raw.astype(np.float32) / 256.0
        else:  # KEYSIGHT InfiniiVision (primary): after :SINGle, OPER:COND Run
               # (bit 3 = 8) stays set while armed/acquiring and clears when the
               # single acquisition stops.  TER is then required below to prove
               # that the stop followed a trigger rather than another stop cause.
            last_status_error = None
            while True:
                try:
                    cond = int(self.inst.query(":OPERegister:CONDition?"))
                    last_status_error = None
                except Exception as exc:
                    # A failed status query is not evidence that a trigger arrived.
                    # Keep waiting and fail closed at the deadline instead of reading
                    # a stale waveform as a valid capture.
                    last_status_error = exc
                    cond = None
                if cond is not None and (cond & 0x08) == 0:
                    break
                if time.monotonic() - t0 > timeout:
                    detail = (f"; last status error: {last_status_error}"
                              if last_status_error is not None else "")
                    raise TimeoutError(
                        "scope: no confirmed trigger (Keysight Run bit never cleared)" +
                        detail)
                time.sleep(0.001)
            try:
                triggered = int(str(self.inst.query(":TER?")).strip())
            except Exception as exc:
                raise RuntimeError(
                    "scope: Keysight trigger-event query failed after completion") from exc
            if triggered != 1:
                raise TimeoutError(
                    "scope: Keysight acquisition stopped without a confirmed trigger")
            self._last_trigger_index_samples = self._read_capture_trigger_index()
            raw = self._query_waveform(":WAVeform:DATA?", datatype="B")
            yincr, yorig, yref = self._pre
            self._last_clip = bool(np.any((raw <= 0) | (raw >= 255)))
            # BYTE data is unsigned. Center on code 128 so every record fits the
            # native +/-0.5 capture-unit representation independent of YREF.
            v = (raw.astype(np.float32) - 128.0) / 256.0
        return v

    def _transfer_timeout_for_points(self, points=None):
        """Return a conservative waveform-transfer timeout in milliseconds."""
        if self._requested_transfer_timeout_ms is not None:
            return self._requested_transfer_timeout_ms
        count = int(self._npts if points is None else points)
        estimated_ms = 2_000 + int(np.ceil(
            max(0, count) * 1_000 / _AUTO_TRANSFER_BYTES_PER_SECOND))
        return max(_CONTROL_TIMEOUT_MS, estimated_ms)

    def _query_waveform(self, command, *, datatype, **kwargs):
        """Use a size-aware timeout only for the potentially large binary transfer."""
        previous_timeout = self.inst.timeout
        self.inst.timeout = self._transfer_timeout_for_points()
        try:
            return self.inst.query_binary_values(
                command, datatype=datatype, container=np.ndarray, **kwargs)
        finally:
            self.inst.timeout = previous_timeout

    def _require_error_free(self, operation):
        if self.dialect == "tek":
            _require_tek_error_free(self.inst, operation=operation)
        else:
            _require_keysight_error_free(self.inst, operation=operation)

    def _read_immutable_capture_state(self):
        """Read settings which must not change while rows share one dataset."""
        c = int(self.ch)
        source = self._applied_trigger_source
        trigger_channel = _channel_number(source)
        trigger_kind = (
            "channel" if trigger_channel is not None else
            ("aux" if self.dialect == "tek" else "external"))
        if self.dialect == "tek":
            state = {
                "model": self._model,
                "sample_rate_hz": _numeric_token(
                    self.inst.query("HORizontal:MODE:SAMPLERate?")),
                "record_length": _numeric_token(
                    self.inst.query("HORizontal:MODE:RECOrdlength?")),
                "horizontal_delay_mode": _source_token(
                    self.inst.query("HORizontal:DELay:MODe?")),
                "horizontal_position_percent": _numeric_token(
                    self.inst.query("HORizontal:POSition?")),
                "acquisition_mode": _source_token(
                    self.inst.query("ACQuire:MODe?")),
                "fastframe": _source_token(
                    self.inst.query("HORizontal:FASTframe:STATE?")),
                "fastacq": _source_token(
                    self.inst.query("ACQuire:FASTAcq:STATE?")),
                "sequence_count": _numeric_token(
                    self.inst.query("ACQuire:SEQuence:NUMSEQuence?")),
                "stop_after": _source_token(
                    self.inst.query("ACQuire:STOPAfter?")),
                "data_source": _source_token(self.inst.query("DATa:SOUrce?")),
                "data_encoding": _source_token(self.inst.query("DATa:ENCdg?")),
                "data_width": _numeric_token(self.inst.query("DATa:WIDth?")),
                "data_start": _numeric_token(self.inst.query("DATa:STARt?")),
                "data_stop": _numeric_token(self.inst.query("DATa:STOP?")),
                "trigger_type": _source_token(self.inst.query("TRIGger:A:TYPe?")),
                "trigger_source": _source_token(
                    self.inst.query("TRIGger:A:EDGE:SOUrce?")),
                "trigger_slope": _source_token(
                    self.inst.query("TRIGger:A:EDGE:SLOpe?")),
                "trigger_coupling": _source_token(
                    self.inst.query("TRIGger:A:EDGE:COUPling?")),
                "trigger_mode": _source_token(self.inst.query("TRIGger:A:MODe?")),
            }
            level_query = (f"TRIGger:A:LEVel:{source}?"
                           if trigger_kind == "channel" else "TRIGger:AUXLevel?")
            state["trigger_level_v"] = _numeric_token(self.inst.query(level_query))
            state.update(_read_tek_channel_metadata(self.inst, c, "measurement"))
            if trigger_channel is not None:
                state.update(_read_tek_channel_metadata(
                    self.inst, trigger_channel, "trigger"))
            state.update(_read_tek_trigger_metadata(
                self.inst, trigger_kind, trigger_channel))
            return state

        state = {
            "model": self._model,
            "waveform_points_mode": _source_token(
                self.inst.query(":WAVeform:POINts:MODE?")),
            "waveform_source": _source_token(
                self.inst.query(":WAVeform:SOURce?")),
            "waveform_format": _source_token(
                self.inst.query(":WAVeform:FORMat?")),
            "waveform_points": _numeric_token(
                self.inst.query(":WAVeform:POINts?")),
            "acquisition_type": _source_token(
                self.inst.query(":ACQuire:TYPE?")),
            "acquisition_mode": _source_token(
                self.inst.query(":ACQuire:MODE?")),
            "sample_rate_hz": _numeric_token(self.inst.query(":ACQuire:SRATe?")),
            "timebase_mode": _source_token(self.inst.query(":TIMebase:MODE?")),
            "timebase_range_s": _numeric_token(
                self.inst.query(":TIMebase:RANGe?")),
            "timebase_reference": _source_token(
                self.inst.query(":TIMebase:REFerence?")),
            "timebase_position_s": _numeric_token(
                self.inst.query(":TIMebase:POSition?")),
            "trigger_mode": _source_token(self.inst.query(":TRIGger:MODE?")),
            "trigger_source": _source_token(
                self.inst.query(":TRIGger:EDGE:SOURce?")),
            "trigger_slope": _source_token(
                self.inst.query(":TRIGger:EDGE:SLOPe?")),
            "trigger_sweep": _source_token(self.inst.query(":TRIGger:SWEep?")),
            "trigger_level_v": _numeric_token(
                self.inst.query(":TRIGger:EDGE:LEVel?")),
        }
        state.update(_read_keysight_channel_metadata(self.inst, c, "measurement"))
        if trigger_channel is not None:
            state.update(_read_keysight_channel_metadata(
                self.inst, trigger_channel, "trigger"))
        state.update(_read_keysight_trigger_metadata(
            self.inst, trigger_kind, trigger_channel))
        return state

    def verify_capture_state(self):
        """Fail closed if errors or immutable scope settings changed mid-campaign.

        This method is safe to call between records or at a checkpoint: it performs
        queries only, never arms, stops, or changes waveform memory.
        """
        if self.inst is None or self._immutable_capture_state is None:
            raise RuntimeError("scope: immutable capture state is unavailable")
        self._require_error_free("capture/checkpoint verification")
        current = self._read_immutable_capture_state()
        self._require_error_free("capture/checkpoint verification")
        changed = sorted(
            key for key in self._immutable_capture_state
            if not _immutable_value_equal(self._immutable_capture_state[key], current.get(key)))
        changed.extend(sorted(set(current) - set(self._immutable_capture_state)))
        if changed:
            raise RuntimeError(
                "scope: immutable capture settings changed during the campaign: " +
                ", ".join(changed))
        return dict(current)

    def _read_capture_trigger_index(self):
        """Read the trigger location for the waveform that just completed.

        Scope sampling is generally asynchronous to the target clock.  In
        particular, Tektronix XZERO is acquisition-specific, so a configure-time
        preamble cannot describe every stored row.
        """
        expected_points = int(self._npts)
        expected_interval = float(self._requested_sample_interval_s)
        if self.dialect == "tek":
            interval = _required_calibration_float(
                self.inst.query("WFMOutpre:XINcr?"),
                "Tektronix per-capture X increment", positive=True)
            points = _required_calibration_float(
                self.inst.query("WFMOutpre:NR_Pt?"),
                "Tektronix per-capture point count", positive=True)
            xzero = _required_calibration_float(
                self.inst.query("WFMOutpre:XZEro?"),
                "Tektronix per-capture X zero")
            pt_off = _required_calibration_float(
                self.inst.query("WFMOutpre:PT_Off?"),
                "Tektronix per-capture trigger point")
            trigger_index = pt_off - xzero / interval
        else:
            raw = self.inst.query(":WAVeform:PREamble?")
            pre = [field.strip() for field in str(raw).split(",")]
            if len(pre) < 10:
                raise RuntimeError(
                    "scope: Keysight per-capture waveform preamble has fewer than 10 fields")
            _require_scope_integer(pre[0], 0, "Keysight per-capture BYTE format")
            _require_scope_integer(pre[1], 0, "Keysight per-capture NORMAL type")
            _require_scope_integer(pre[3], 1, "Keysight per-capture acquisition count")
            points = _required_calibration_float(
                pre[2], "Keysight per-capture point count", positive=True)
            interval = _required_calibration_float(
                pre[4], "Keysight per-capture X increment", positive=True)
            xorigin = _required_calibration_float(
                pre[5], "Keysight per-capture X origin")
            xreference = _required_calibration_float(
                pre[6], "Keysight per-capture X reference")
            trigger_index = xreference - xorigin / interval
        if int(round(points)) != expected_points:
            raise RuntimeError(
                "scope: per-capture preamble point count changed before waveform read")
        if abs(interval - expected_interval) > expected_interval * 0.02:
            raise RuntimeError(
                "scope: per-capture sample interval changed before waveform read")
        if not np.isfinite(trigger_index) or not -1.0 <= trigger_index <= expected_points:
            raise RuntimeError(
                f"scope: invalid per-capture trigger index {trigger_index!r}")
        nominal = float(self._nominal_trigger_index_samples)
        if abs(trigger_index - nominal) > 1.0 + 1e-9:
            raise RuntimeError(
                "scope: per-capture trigger position moved by more than one sample "
                f"(configured {nominal:.6g}, observed {trigger_index:.6g})")
        return float(trigger_index)

    @property
    def last_capture_clipped(self):
        return self._last_clip

    @property
    def last_trigger_index_samples(self):
        """Trigger location in the most recently returned waveform, in samples."""
        return self._last_trigger_index_samples

    @property
    def clock_status(self):
        return dict(getattr(self, "_clock_status", {}))

    @property
    def storage_metadata(self):
        clock_source = getattr(self, "clock_source", "external")
        metadata = {
            "waveform_unit": "normalized_scope_adc_code",
            "scope_idn": self._idn,
            "scope_model": self._model,
            "scope_dialect": self.dialect,
            "scope_raw_width_bits": 8,
            "scope_trigger_source_requested": getattr(
                self, "trig_source", "unknown"),
            "scope_trigger_source_applied": getattr(
                self, "_applied_trigger_source", None),
            "scope_trigger_level_v": float(getattr(self, "trig_level", 0.0)),
            "board_clock_source": clock_source,
            "board_clock_verification": (
                "chipwhisperer_clock_status"
                if clock_source == "husky" else "external_unverified"),
            "scope_per_row_trigger_index_recorded": True,
            "scope_fractional_phase_alignment_applied": False,
            "scope_native_waveform_processing": "raw_normalized_adc_codes_unaligned",
            "scope_transfer_timeout_ms": self._transfer_timeout_for_points(),
        }
        if getattr(self, "_nominal_trigger_index_samples", None) is not None:
            metadata["scope_alignment_reference_trigger_index_samples"] = float(
                self._nominal_trigger_index_samples)
        observed_trigger_level = getattr(self, "_observed_trigger_level_v", None)
        if observed_trigger_level is not None:
            metadata["scope_trigger_level_observed_v"] = float(observed_trigger_level)
        if getattr(self, "_npts", None) is not None:
            metadata["requested_sample_count"] = int(self._npts)
        if getattr(self, "_requested_sample_interval_s", None) is not None:
            metadata["requested_sample_interval_s"] = float(
                self._requested_sample_interval_s)
        if getattr(self, "_observed_sample_interval_s", None) is not None:
            metadata["observed_sample_interval_s"] = float(
                self._observed_sample_interval_s)
        if getattr(self, "_observed_sample_count", None) is not None:
            metadata["observed_sample_count"] = int(self._observed_sample_count)
        if getattr(self, "_observed_acquisition_sample_rate_hz", None) is not None:
            metadata["scope_acquisition_sample_rate_hz_observed"] = float(
                self._observed_acquisition_sample_rate_hz)
        metadata.update(getattr(self, "_horizontal_metadata", {}))
        metadata.update(getattr(self, "_vertical_metadata", {}))
        if (getattr(self, "_observed_sample_interval_s", None) is not None or
                getattr(self, "_observed_sample_count", None) is not None):
            metadata["timing_observation_source"] = "scope_waveform_preamble"
        if self._pre is None:
            return metadata
        if self.dialect == "tek":
            ymult, yoff, yzero = self._pre
            metadata.update(
                scope_raw_encoding="signed_int8",
                scope_volts_per_capture_unit=float(256.0 * ymult),
                scope_volts_offset=float(yzero - yoff * ymult),
                scope_voltage_formula=(
                    "volts = capture_unit * scope_volts_per_capture_unit + "
                    "scope_volts_offset"))
        else:
            yincr, yorig, yref = self._pre
            metadata.update(
                scope_raw_encoding="unsigned_uint8_centered_at_128",
                scope_volts_per_capture_unit=float(256.0 * yincr),
                scope_volts_offset=float((128.0 - yref) * yincr + yorig),
                scope_voltage_formula=(
                    "volts = capture_unit * scope_volts_per_capture_unit + "
                    "scope_volts_offset"))
        return metadata

    @property
    def trig_count(self):
        return 0     # a scope does not report a trigger width; 0 = unknown (fine)

    def close(self):
        try:
            if self.inst is not None:
                self.inst.write("ACQuire:STATE STOP" if self.dialect == "tek" else ":STOP")
        except Exception:
            pass
        try:
            self.inst.close()
        except Exception:
            pass
        try:
            self.rm.close()
        except Exception:
            pass
        self.inst = None
        self.rm = None
        self._close_clock_driver()

    def _close_clock_driver(self):
        driver = getattr(self, "_clock_driver", None)
        self._clock_driver = None
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


def _has(inst, q):
    try:
        inst.query(q); return True
    except Exception:
        return False


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) and result > 0 else None


def _required_calibration_float(value, field, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"scope: invalid {field} calibration value") from exc
    if not np.isfinite(result) or (positive and result <= 0):
        raise RuntimeError(f"scope: invalid {field} calibration value")
    return result


def _optional_positive_float(inst, command):
    try:
        return _positive_float(inst.query(command))
    except Exception:
        return None


def _source_token(value):
    """Canonicalize a SCPI source/mode response, including HEADER ON replies."""
    token = str(value).strip().upper().rsplit(" ", 1)[-1]
    return re.sub(r"[^A-Z0-9]", "", token)


def _require_supported_scope_model(idn, dialect):
    """Return the normalized model from *IDN? or reject an unsupported family."""
    fields = [field.strip().upper() for field in str(idn).split(",")]
    if len(fields) < 2 or not fields[1]:
        raise RuntimeError(f"scope: malformed instrument *IDN? response {idn!r}")
    model = re.sub(r"[^A-Z0-9]", "", fields[1])
    if dialect == "keysight":
        supported = _KEYSIGHT_3000_X_MODEL.fullmatch(model) is not None
        family = "Keysight/Agilent InfiniiVision 3000 X-Series"
    else:
        supported = model in _TEK_456_MODELS
        family = "Tektronix 4/5/6 Series MSO"
    if not supported:
        raise RuntimeError(
            f"scope: unsupported instrument model {fields[1]!r}; "
            f"this backend is limited to {family}")
    return model


def _immutable_value_equal(expected, observed):
    if isinstance(expected, (int, float, np.number)) and not isinstance(expected, bool):
        try:
            return bool(np.isclose(float(expected), float(observed),
                                   rtol=1e-9, atol=1e-15))
        except (TypeError, ValueError):
            return False
    return expected == observed


def _require_scope_setting(observed, expected, field):
    have = _source_token(observed)
    want = _source_token(expected)
    aliases = {
        "EXTERNAL": {"EXT", "EXTERNAL"},
        "AUXILIARY": {"AUX", "AUXILIARY"},
        "NORMAL": {"NORM", "NORMAL"},
        "POSITIVE": {"POS", "POSITIVE"},
        "SEQUENCE": {"SEQ", "SEQUENCE"},
    }
    accepted = aliases.get(want, {want})
    # Channel names may be CH2, CHAN2, or CHANNEL2.
    channel = _channel_number(want)
    if channel is not None:
        accepted |= {f"CH{channel}", f"CHAN{channel}", f"CHANNEL{channel}"}
    if have not in accepted:
        raise RuntimeError(
            f"scope: {field} readback mismatch (requested {expected}, observed {observed})")


def _require_scope_float(observed, expected, field):
    token = str(observed).strip().rsplit(" ", 1)[-1]
    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"scope: invalid {field} readback {observed!r}") from exc
    tolerance = max(0.02, abs(float(expected)) * 0.01)
    if not np.isfinite(value) or abs(value - float(expected)) > tolerance:
        raise RuntimeError(
            f"scope: {field} readback mismatch (requested {expected:g} V, "
            f"observed {observed})")
    return value


def _numeric_token(observed):
    """Return a numeric value from HEADER OFF or prefixed SCPI responses."""
    token = str(observed).strip().rsplit(" ", 1)[-1]
    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"scope: invalid numeric readback {observed!r}") from exc
    if not np.isfinite(value):
        raise RuntimeError(f"scope: non-finite numeric readback {observed!r}")
    return value


def _required_scope_token(observed, field, allowed=None):
    token = _source_token(observed)
    if not token or (allowed is not None and token not in set(allowed)):
        expected = "" if allowed is None else f"; expected one of {sorted(allowed)}"
        raise RuntimeError(
            f"scope: invalid {field} readback {observed!r}{expected}")
    return token


def _keysight_impedance_ohms(observed, field):
    token = _required_scope_token(
        observed, field, {"FIFT", "FIFTY", "ONEM", "ONEMEG"})
    return 50.0 if token in {"FIFT", "FIFTY"} else 1_000_000.0


def _read_keysight_channel_metadata(inst, channel, role):
    """Read immutable analog-path state for a Keysight acquisition channel."""
    c = int(channel)
    prefix = f"scope_{role}_channel"
    displayed = _require_scope_integer(
        inst.query(f":CHANnel{c}:DISPlay?"), 1,
        f"Keysight {role} channel display state")
    bandwidth_limit = _numeric_token(inst.query(f":CHANnel{c}:BWLimit?"))
    if bandwidth_limit not in (0.0, 1.0):
        raise RuntimeError(
            f"scope: invalid Keysight {role} channel bandwidth-limit readback")
    result = {
        f"{prefix}_number": c,
        f"{prefix}_displayed": bool(displayed),
        f"{prefix}_coupling": _required_scope_token(
            inst.query(f":CHANnel{c}:COUPling?"),
            f"Keysight {role} channel coupling", {"AC", "DC"}),
        f"{prefix}_bandwidth_limit_enabled": bool(int(bandwidth_limit)),
        f"{prefix}_input_impedance_ohm": _keysight_impedance_ohms(
            inst.query(f":CHANnel{c}:IMPedance?"),
            f"Keysight {role} channel impedance"),
        f"{prefix}_probe_attenuation": _required_calibration_float(
            inst.query(f":CHANnel{c}:PROBe?"),
            f"Keysight {role} channel probe attenuation", positive=True),
        f"{prefix}_vertical_range_v": _required_calibration_float(
            inst.query(f":CHANnel{c}:RANGe?"),
            f"Keysight {role} channel range", positive=True),
        f"{prefix}_vertical_scale_v_per_div": _required_calibration_float(
            inst.query(f":CHANnel{c}:SCALe?"),
            f"Keysight {role} channel scale", positive=True),
        f"{prefix}_offset_v": _required_calibration_float(
            inst.query(f":CHANnel{c}:OFFSet?"),
            f"Keysight {role} channel offset"),
    }
    if role == "trigger" and (
            result[f"{prefix}_coupling"] != "DC" or
            result[f"{prefix}_input_impedance_ohm"] != 1_000_000.0):
        raise RuntimeError(
            "scope: Keysight analog trigger channel is not DC-coupled high-Z")
    return result


def _read_tek_channel_metadata(inst, channel, role):
    """Read immutable analog-path state for a Tektronix acquisition channel."""
    c = int(channel)
    prefix = f"scope_{role}_channel"
    displayed = _require_scope_integer(
        inst.query(f"DISPlay:WAVEView1:CH{c}:STATE?"), 1,
        f"Tektronix {role} channel display state")
    result = {
        f"{prefix}_number": c,
        f"{prefix}_displayed": bool(displayed),
        f"{prefix}_coupling": _required_scope_token(
            inst.query(f"CH{c}:COUPling?"),
            f"Tektronix {role} channel coupling",
            {"AC", "DC", "DCR", "DCREJECT"}),
        f"{prefix}_bandwidth_hz": _required_calibration_float(
            inst.query(f"CH{c}:BANdwidth?"),
            f"Tektronix {role} channel bandwidth", positive=True),
        f"{prefix}_input_impedance_ohm": _required_calibration_float(
            inst.query(f"CH{c}:TERmination?"),
            f"Tektronix {role} channel termination", positive=True),
        f"{prefix}_external_attenuation_multiplier": _required_calibration_float(
            inst.query(f"CH{c}:PROBEFunc:EXTAtten?"),
            f"Tektronix {role} channel external attenuation", positive=True),
        f"{prefix}_attached_probe_gain": _required_calibration_float(
            inst.query(f"CH{c}:PRObe:GAIN?"),
            f"Tektronix {role} channel attached-probe gain", positive=True),
        f"{prefix}_vertical_scale_v_per_div": _required_calibration_float(
            inst.query(f"CH{c}:SCAle?"),
            f"Tektronix {role} channel scale", positive=True),
        f"{prefix}_offset_v": _required_calibration_float(
            inst.query(f"CH{c}:OFFSet?"),
            f"Tektronix {role} channel offset"),
    }
    if role == "trigger" and (
            result[f"{prefix}_coupling"] != "DC" or
            not np.isclose(result[f"{prefix}_input_impedance_ohm"], 1_000_000.0,
                           rtol=0.01, atol=1.0)):
        raise RuntimeError(
            "scope: Tektronix analog trigger channel is not DC-coupled high-Z")
    return result


def _read_keysight_trigger_metadata(inst, trigger_kind, trigger_channel):
    """Read the configured InfiniiVision trigger-path electrical state."""
    result = {
        "scope_trigger_path_kind": trigger_kind,
        "scope_trigger_path_coupling": _required_scope_token(
            inst.query(":TRIGger:EDGE:COUPling?"),
            "Keysight trigger-path coupling", {"DC"}),
        "scope_trigger_path_reject": _required_scope_token(
            inst.query(":TRIGger:EDGE:REJect?"),
            "Keysight trigger-path reject", {"OFF"}),
        "scope_trigger_path_hf_reject": bool(_require_scope_integer(
            inst.query(":TRIGger:HFReject?"), 0,
            "Keysight trigger HF reject")),
        "scope_trigger_path_noise_reject": bool(_require_scope_integer(
            inst.query(":TRIGger:NREJect?"), 0,
            "Keysight trigger noise reject")),
    }
    if trigger_kind == "external":
        result.update(
            scope_trigger_external_input_impedance_ohm=1_000_000.0,
            scope_trigger_external_impedance_source="fixed_by_instrument",
            scope_trigger_external_probe_attenuation=_require_scope_number(
                inst.query(":EXTernal:PROBe?"), 1.0,
                "Keysight external trigger probe attenuation"),
            scope_trigger_external_range_v=_require_scope_number(
                inst.query(":EXTernal:RANGe?"), 8.0,
                "Keysight external trigger range"),
            scope_trigger_external_units=_required_scope_token(
                inst.query(":EXTernal:UNITs?"),
                "Keysight external trigger units", {"VOLT", "VOLTS"}),
            scope_trigger_external_bandwidth_limit_enabled=bool(
                _require_scope_integer(
                    inst.query(":EXTernal:BWLimit?"), 0,
                    "Keysight external trigger bandwidth limit")))
    elif trigger_channel is None:
        raise RuntimeError("scope: invalid Keysight trigger-path classification")
    return result


def _read_tek_trigger_metadata(inst, trigger_kind, trigger_channel):
    """Read the programmable Tek trigger state without inventing AUX controls."""
    result = {
        "scope_trigger_path_kind": trigger_kind,
        "scope_trigger_path_coupling": _required_scope_token(
            inst.query("TRIGger:A:EDGE:COUPling?"),
            "Tektronix trigger-path coupling", {"DC"}),
    }
    if trigger_kind == "aux":
        resistance = _numeric_token(inst.query("AUXIn:PRObe:RESistance?"))
        if resistance < 0:
            raise RuntimeError(
                "scope: invalid Tektronix AUX probe resistance readback")
        result.update(
            # The 4/5/6 Series command set exposes the attached-probe gain but
            # does not document AUX equivalents of channel EXTAtten, range,
            # bandwidth, or termination controls.  Require direct 1x scaling
            # rather than recording an invented external-attenuation setting.
            scope_trigger_aux_attached_probe_gain=_require_scope_number(
                inst.query("AUXIn:PRObe:GAIN?"),
                1.0, "Tektronix AUX attached-probe gain"),
            scope_trigger_aux_probe_resistance_ohm=resistance,
            scope_trigger_aux_probe_id_type=_required_scope_token(
                inst.query("AUXIn:PRObe:ID:TYPe?"),
                "Tektronix AUX probe ID type"))
    elif trigger_channel is None:
        raise RuntimeError("scope: invalid Tektronix trigger-path classification")
    return result


def _require_scope_number(observed, expected, field, *,
                          relative_tolerance=0.0, absolute_tolerance=0.0):
    value = _numeric_token(observed)
    tolerance = max(float(absolute_tolerance),
                    abs(float(expected)) * float(relative_tolerance))
    if abs(value - float(expected)) > tolerance:
        raise RuntimeError(
            f"scope: {field} readback mismatch (requested {expected:g}, "
            f"observed {observed})")
    return value


def _require_scope_integer(observed, expected, field):
    value = _numeric_token(observed)
    if value != round(value) or int(round(value)) != int(expected):
        raise RuntimeError(
            f"scope: {field} readback mismatch (requested {int(expected)}, "
            f"observed {observed})")
    return int(round(value))


def _require_keysight_error_free(inst, operation="SCPI setup"):
    """Drain InfiniiVision's SCPI error queue and fail on the first error."""
    errors = []
    for _ in range(32):
        reply = str(inst.query(":SYSTem:ERRor?")).strip()
        try:
            code = int(reply.split(",", 1)[0])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"scope: invalid Keysight error-queue response {reply!r}") from exc
        if code == 0:
            break
        errors.append(reply)
    else:
        errors.append("error queue did not terminate")
    if errors:
        raise RuntimeError(
            f"scope: Keysight rejected {operation}: " + "; ".join(errors))


def _require_tek_error_free(inst, operation="SCPI setup"):
    """Inspect Tek's event queue directly, independent of persistent DESE masks."""
    quantity = _numeric_token(inst.query("EVQty?"))
    if quantity != round(quantity) or quantity < 0:
        raise RuntimeError(f"scope: invalid Tektronix event count {quantity!r}")
    if int(quantity):
        try:
            detail = str(inst.query("ALLEV?")).strip()
        except Exception as exc:
            detail = f"event detail unavailable: {exc}"
        raise RuntimeError(
            f"scope: Tektronix rejected {operation} "
            f"({int(quantity)} event(s)): {detail}")
    # Also check enabled IEEE-488.2 event bits. EVQty above is authoritative when
    # a prior user made DESE non-default, but ESR can expose transport/query faults.
    reply = str(inst.query("*ESR?")).strip()
    try:
        esr = int(reply.rsplit(" ", 1)[-1])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"scope: invalid Tektronix ESR response {reply!r}") from exc
    if esr & 0x3C:  # query, device, execution, or command error
        try:
            detail = str(inst.query("ALLEV?")).strip()
        except Exception as exc:
            detail = f"event detail unavailable: {exc}"
        raise RuntimeError(
            f"scope: Tektronix rejected {operation} (ESR {esr}): {detail}")


def _require_keysight_points(points):
    """Enforce the 3000 X RAW transfer lengths documented by Keysight."""
    n = int(points)
    allowed = {100, 250, 500, 8_000_000}
    decade = 1_000
    while decade <= 100_000:
        allowed.update((decade, 2 * decade, 5 * decade))
        decade *= 10
    allowed.update((1_000_000, 2_000_000, 4_000_000))
    if n not in allowed:
        lower = max((value for value in allowed if value < n), default=100)
        upper = min((value for value in allowed if value > n), default=8_000_000)
        raise ValueError(
            "Keysight InfiniiVision RAW waveform points must use a documented "
            f"length; {n} is unsupported (nearest {lower} or {upper})")
    return n


def _fractional_align_to_reference(trace, phase_delta):
    """Move a waveform's observed trigger to the configured reference index.

    ``phase_delta`` is constrained to one sample by the per-capture preamble
    validation.  Edge samples use nearest-value extension; no wraparound is
    introduced into a power trace.
    """
    values = np.asarray(trace, dtype=np.float32)
    delta = float(phase_delta)
    if not np.isfinite(delta) or abs(delta) > 1.0 + 1e-9:
        raise RuntimeError(f"scope: invalid fractional alignment delta {delta!r}")
    if abs(delta) <= 1e-12 or values.size < 2:
        return values.copy()
    amount = min(abs(delta), 1.0)
    aligned = np.empty_like(values)
    if delta > 0:
        aligned[:-1] = (1.0 - amount) * values[:-1] + amount * values[1:]
        aligned[-1] = values[-1]
    else:
        aligned[1:] = (1.0 - amount) * values[1:] + amount * values[:-1]
        aligned[0] = values[0]
    return aligned


def _channel_number(source):
    match = re.fullmatch(r"(?:CH|CHAN|CHANNEL)([1-8])",
                         str(source).strip().upper())
    return int(match.group(1)) if match else None


def _keysight_trigger_source(source):
    raw = str(source).strip().upper()
    channel = _channel_number(raw)
    if channel is not None:
        return f"CHANnel{channel}", "channel"
    if raw in ("EXT", "EXTERNAL", "AUX", "AUXILIARY"):
        return "EXTernal", "external"
    raise ValueError(
        "Keysight trigger source must be EXTernal or CHANnel1..8")


def _tek_trigger_source(source):
    raw = str(source).strip().upper()
    channel = _channel_number(raw)
    if channel is not None:
        return f"CH{channel}", "channel"
    if raw in ("EXT", "EXTERNAL", "AUX", "AUXILIARY"):
        return "AUXiliary", "aux"
    raise ValueError(
        "Tektronix trigger source must be AUXiliary or CH1..8")


def _acq_done_ks(inst):
    try:
        return int(inst.query(":OPERegister:CONDition?")) & (1 << 3) == 0
    except Exception:
        return False
