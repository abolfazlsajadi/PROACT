"""Strict hardware-free SCPI simulators for oscilloscope integration tests.

These classes model only the commands the acquisition backend is allowed to use.
Unknown commands, wrong binary encodings, reads before a completed single shot, and
use after close fail immediately so tests cannot pass through permissive mocks.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np


class StrictScopeInstrument:
    def __init__(self, dialect="keysight", *, resource=None, idn=None,
                 sample_interval_s=5e-9, waveform_length_override=None,
                 freeze_after=None, clip=False, never_complete=False,
                 never_arm=False, ready_after_queries=0, status_error=None,
                 bad_calibration=False, setup_error=None,
                 stop_without_trigger=False, stale_arm_event=False,
                 spurious_trigger_on_arm=False, trigger_phases=(0.0,),
                 initial_running=True):
        if dialect not in ("keysight", "tek"):
            raise ValueError(dialect)
        self.dialect = dialect
        self.resource = resource or "TCPIP0::192.0.2.10::hislip0::INSTR"
        self.idn = idn or (
            "KEYSIGHT TECHNOLOGIES,DSOX3034T,SIM,1.0" if dialect == "keysight"
            else "TEKTRONIX,MSO54B,SIM,1.0")
        self.sample_interval_s = float(sample_interval_s)
        self.waveform_length_override = waveform_length_override
        self.freeze_after = freeze_after
        self.clip = bool(clip)
        self.never_complete = bool(never_complete)
        self.never_arm = bool(never_arm)
        self.ready_after_queries = int(ready_after_queries)
        self.status_error = status_error
        self.bad_calibration = bool(bad_calibration)
        self.setup_error = setup_error
        self.stop_without_trigger = bool(stop_without_trigger)
        self.spurious_trigger_on_arm = bool(spurious_trigger_on_arm)
        self.trigger_phases = tuple(float(value) for value in trigger_phases)
        if not self.trigger_phases or not np.isfinite(self.trigger_phases).all():
            raise ValueError("trigger_phases must contain finite values")
        self.current_trigger_phase = 0.0
        self.points = 100
        self.data_stop = 100
        self.points_mode = None
        self.timebase_mode = "MAIN"
        self.timebase_range_s = self.points * self.sample_interval_s
        self.timebase_reference = "CENTER"
        self.timebase_position_s = 0.0
        self.horizontal_delay_mode = "OFF"
        self.horizontal_position_percent = 50.0
        self.measure_channel = None
        self.waveform_format = None
        self.waveform_encoding = None
        self.waveform_width = None
        self.data_start = 1
        self.trigger_source = None
        self.trigger_type = "PULSEWIDTH"
        self.trigger_slope = "FALL"
        self.trigger_level = None
        self.trigger_normal = False
        self.trigger_coupling = "AC"
        self.trigger_reject = "LFREJECT"
        self.trigger_hf_reject = True
        self.trigger_noise_reject = True
        self.external_probe_attenuation = 10.0
        self.external_range_v = 80.0
        self.external_units = "AMPERE"
        self.aux_probe_gain = 1.0
        self.aux_probe_resistance_ohm = 1_000_000.0
        self.aux_probe_id_type = "NONE"
        self.acquisition_mode = "AVERAGE"
        self.keysight_realtime_mode = "SEGMENTED"
        self.tek_sequence_count = 7
        self.tek_fastframe = True
        self.tek_fastacq = True
        self.single_sequence = False
        self.armed = bool(initial_running)
        self.completed = not bool(initial_running)
        self.binary_reads = 0
        self.arm_count = 0
        self.trigger_state_queries = 0
        self.capture_count = 0
        self.acquisition_count = 0
        self.trigger_event = False
        self.arm_event = bool(stale_arm_event)
        self.device_event_status_enable = 0
        self.channel_display = {channel: False for channel in range(1, 9)}
        self.channel_coupling = {channel: "DC" for channel in range(1, 9)}
        self.channel_bandwidth_limit = {channel: False for channel in range(1, 9)}
        self.channel_bandwidth_hz = {channel: 1.0e9 for channel in range(1, 9)}
        self.channel_impedance_ohm = {channel: 1.0e6 for channel in range(1, 9)}
        self.channel_probe_attenuation = {channel: 1.0 for channel in range(1, 9)}
        self.channel_probe_gain = {channel: 1.0 for channel in range(1, 9)}
        self.channel_range_v = {channel: 0.8 for channel in range(1, 9)}
        self.channel_scale_v = {channel: 0.1 for channel in range(1, 9)}
        self.channel_offset_v = {channel: 0.0 for channel in range(1, 9)}
        self.closed = False
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.events = []
        self.binary_timeouts_ms = []
        self._wave = None
        self._frozen_wave = None

    def _check_open(self):
        if self.closed:
            raise RuntimeError("simulated scope used after close")

    def write(self, command):
        self._check_open()
        command = str(command)
        self.events.append(("write", command))
        if command == "*CLS":
            return
        if self.dialect == "keysight":
            match = re.fullmatch(r":CHANnel([1-8]):DISPlay 1", command)
            channel_setting = re.fullmatch(
                r":CHANnel([1-8]):(COUPling DC|IMPedance ONEMeg)", command)
            if match:
                self.channel_display[int(match.group(1))] = True
            elif channel_setting:
                channel = int(channel_setting.group(1))
                if channel_setting.group(2) == "COUPling DC":
                    self.channel_coupling[channel] = "DC"
                else:
                    self.channel_impedance_ohm[channel] = 1_000_000.0
            elif command == ":STOP":
                self.armed = False
                self.completed = True
            elif command.startswith(":WAVeform:SOURce CHANnel"):
                self.measure_channel = int(command.rsplit("CHANnel", 1)[1])
            elif command == ":WAVeform:FORMat BYTE":
                self.waveform_format = "BYTE"
            elif command == ":WAVeform:POINts:MODE RAW":
                self.points_mode = "RAW"
            elif command == ":TRIGger:MODE EDGE":
                self.trigger_type = "EDGE"
            elif command == ":TRIGger:EDGE:SLOPe POSitive":
                self.trigger_slope = "POSITIVE"
            elif command.startswith(":WAVeform:POINts "):
                self.points = int(command.rsplit(" ", 1)[1])
                self.data_stop = self.points
                self.sample_interval_s = self.timebase_range_s / self.points
            elif command == ":TIMebase:MODE MAIN":
                self.timebase_mode = "MAIN"
            elif command.startswith(":TIMebase:RANGe "):
                self.timebase_range_s = float(command.rsplit(" ", 1)[1])
                self.sample_interval_s = self.timebase_range_s / self.points
            elif command == ":TIMebase:REFerence LEFT":
                self.timebase_reference = "LEFT"
            elif command.startswith(":TIMebase:POSition "):
                self.timebase_position_s = float(command.rsplit(" ", 1)[1])
            elif command == ":TRIGger:SWEep NORMal":
                self.trigger_normal = True
            elif command == ":ACQuire:TYPE NORMal":
                self.acquisition_mode = "NORMAL"
            elif command == ":ACQuire:MODE RTIMe":
                self.keysight_realtime_mode = "RTIME"
            elif command == ":EXTernal:PROBe 1":
                self.external_probe_attenuation = 1.0
            elif command == ":EXTernal:UNITs VOLT":
                self.external_units = "VOLT"
            elif command == ":EXTernal:RANGe 8":
                self.external_range_v = 8.0
            elif command == ":TRIGger:EDGE:COUPling DC":
                self.trigger_coupling = "DC"
            elif command == ":TRIGger:EDGE:REJect OFF":
                self.trigger_reject = "OFF"
            elif command == ":TRIGger:HFReject OFF":
                self.trigger_hf_reject = False
            elif command == ":TRIGger:NREJect OFF":
                self.trigger_noise_reject = False
            elif command.startswith(":TRIGger:EDGE:SOURce "):
                self.trigger_source = command.rsplit(" ", 1)[1]
            elif command.startswith(":TRIGger:EDGE:LEVel "):
                self.trigger_level = float(command.rsplit(" ", 1)[1])
            elif command == ":SINGle":
                self._arm()
            else:
                raise AssertionError(f"unexpected Keysight write: {command}")
            return

        match = re.fullmatch(r"DISPlay:WAVEView1:CH([1-8]):STATE 1", command)
        channel_setting = re.fullmatch(
            r"CH([1-8]):(COUPling DC|TERmination 1000000)", command)
        if command == "DESE 255":
            self.device_event_status_enable = 255
        elif match:
            self.channel_display[int(match.group(1))] = True
        elif channel_setting:
            channel = int(channel_setting.group(1))
            if channel_setting.group(2) == "COUPling DC":
                self.channel_coupling[channel] = "DC"
            else:
                self.channel_impedance_ohm[channel] = 1_000_000.0
        elif command == "ACQuire:STATE STOP":
            self.armed = False
            self.completed = True
        elif command in ("HEADer OFF", "HORizontal:MODE MANual"):
            pass
        elif command == "TRIGger:A:TYPe EDGE":
            self.trigger_type = "EDGE"
        elif command == "TRIGger:A:EDGE:SLOpe RISe":
            self.trigger_slope = "RISE"
        elif command == "HORizontal:FASTframe:STATE OFF":
            self.tek_fastframe = False
        elif command == "ACQuire:FASTAcq:STATE OFF":
            self.tek_fastacq = False
        elif command == "ACQuire:MODe SAMple":
            self.acquisition_mode = "SAMPLE"
        elif command == "TRIGger:A:EDGE:COUPling DC":
            self.trigger_coupling = "DC"
        elif command == "DATa:ENCdg SRIbinary":
            self.waveform_encoding = "SRIBINARY"
        elif command == "DATa:WIDth 1":
            self.waveform_width = 1
        elif command == "DATa:STARt 1":
            self.data_start = 1
        elif command == "HORizontal:DELay:MODe OFF":
            self.horizontal_delay_mode = "OFF"
        elif command.startswith("HORizontal:POSition "):
            self.horizontal_position_percent = float(command.rsplit(" ", 1)[1])
        elif command.startswith("HORizontal:MODE:SAMPLERate "):
            rate = float(command.rsplit(" ", 1)[1])
            if rate <= 0:
                raise ValueError("sample rate")
            self.sample_interval_s = 1.0 / rate
        elif command.startswith("HORizontal:MODE:RECOrdlength "):
            self.points = int(command.rsplit(" ", 1)[1])
        elif command.startswith("DATa:SOUrce CH"):
            self.measure_channel = int(command.rsplit("CH", 1)[1])
        elif command.startswith("DATa:STOP "):
            if int(command.rsplit(" ", 1)[1]) != self.points:
                raise AssertionError("DATa:STOP differs from record length")
            self.data_stop = int(command.rsplit(" ", 1)[1])
        elif command.startswith("TRIGger:A:EDGE:SOUrce "):
            self.trigger_source = command.rsplit(" ", 1)[1]
        elif command == "TRIGger:A:MODe NORMal":
            self.trigger_normal = True
        elif command.startswith("TRIGger:A:LEVel:CH"):
            self.trigger_level = float(command.rsplit(" ", 1)[1])
        elif command.startswith("TRIGger:AUXLevel "):
            self.trigger_level = float(command.rsplit(" ", 1)[1])
        elif command == "ACQuire:STOPAfter SEQuence":
            self.single_sequence = True
        elif command == "ACQuire:SEQuence:NUMSEQuence 1":
            self.tek_sequence_count = 1
        elif command == "ACQuire:STATE RUN":
            self._arm()
        else:
            raise AssertionError(f"unexpected Tektronix write: {command}")

    def _arm(self):
        if not self.trigger_normal:
            raise AssertionError("single shot armed without NORMAL trigger mode")
        if self.dialect == "tek" and not self.single_sequence:
            raise AssertionError("Tektronix single sequence was not configured")
        self.armed = True
        self.completed = False
        self.arm_count += 1
        self.trigger_state_queries = 0
        self.acquisition_count = 0
        if self.dialect == "keysight" and not self.never_arm:
            self.arm_event = True
            if self.spurious_trigger_on_arm:
                self.trigger_event = True
                self.completed = True
                self.armed = False

    def trigger(self, key, inp):
        """Deliver the target edge and prepare one waveform if currently armed."""
        if not self.armed:
            return
        if self.stop_without_trigger:
            self.completed = True
            self.armed = False
            return
        self.capture_count += 1
        self.current_trigger_phase = self.trigger_phases[
            (self.capture_count - 1) % len(self.trigger_phases)]
        self.acquisition_count += 1
        self.trigger_event = True
        length = int(self.waveform_length_override or self.points)
        if self.freeze_after is not None and self.capture_count > self.freeze_after:
            if self._frozen_wave is None:
                self._frozen_wave = self._make_wave(length, key, inp, self.capture_count)
            wave = self._frozen_wave.copy()
        else:
            wave = self._make_wave(length, key, inp, self.capture_count)
        self._wave = wave
        if not self.never_complete:
            self.completed = True
            self.armed = False

    def _make_wave(self, length, key, inp, sequence):
        x = np.arange(length, dtype=np.int32)
        centered = ((x * 5 + sequence * 3) % 11) - 5
        centered += np.rint(5 * np.sin((x + sequence) / 9.0)).astype(np.int32)
        if bytes(inp) == bytes(16) and length > 12:
            centered[12] += 7
        for byte in range(min(16, max(0, length - 24))):
            centered[24 + byte] += (int(inp[byte]) ^ int(key[byte])).bit_count() - 4
        centered = np.clip(centered, -100, 100)
        if self.dialect == "keysight":
            raw = (centered + 128).astype(np.uint8)
            if self.clip and length:
                raw[0] = 255
        else:
            raw = centered.astype(np.int8)
            if self.clip and length:
                raw[0] = 127
        return raw

    def query(self, command):
        self._check_open()
        command = str(command)
        self.events.append(("query", command))
        if command == "*IDN?":
            return self.idn
        if command == "*OPC?":
            return "1"
        if command == "DESE?":
            return str(self.device_event_status_enable)
        if command == "*ESR?":
            return ("16" if self.setup_error and
                    (self.device_event_status_enable & 16) else "0")
        if command == "EVQty?":
            return ("1" if self.setup_error and
                    self.device_event_status_enable else "0")
        if command == "ALLEV?":
            return self.setup_error or "0,No events to report"
        if self.dialect == "keysight":
            match = re.fullmatch(
                r":CHANnel([1-8]):(DISPlay|BWLimit|COUPling|IMPedance|"
                r"PROBe|RANGe|SCALe|OFFSet)\?", command)
            if match:
                channel = int(match.group(1))
                field = match.group(2)
                values = {
                    "DISPlay": "1" if self.channel_display[channel] else "0",
                    "BWLimit": "1" if self.channel_bandwidth_limit[channel] else "0",
                    "COUPling": self.channel_coupling[channel],
                    "IMPedance": ("FIFT" if self.channel_impedance_ohm[channel] == 50
                                  else "ONEM"),
                    "PROBe": f"{self.channel_probe_attenuation[channel]:g}",
                    "RANGe": f"{self.channel_range_v[channel]:g}",
                    "SCALe": f"{self.channel_scale_v[channel]:g}",
                    "OFFSet": f"{self.channel_offset_v[channel]:g}",
                }
                return values[field]
            if command == ":WAVeform:PREamble?":
                yincr = "nan" if self.bad_calibration else "0.002"
                xorigin = -(self.points * 0.1 + self.current_trigger_phase) * \
                    self.sample_interval_s
                preamble_type = 0 if self.acquisition_mode == "NORMAL" else 2
                return (f"0,{preamble_type},{self.points},1,{self.sample_interval_s:.12g},"
                        f"{xorigin:.12g},0,{yincr},0.1,128")
            if command == ":SYSTem:ERRor?":
                if self.setup_error:
                    error, self.setup_error = self.setup_error, None
                    return error
                return '+0,"No error"'
            if command == ":WAVeform:POINts:MODE?":
                return self.points_mode or "NORMAL"
            if command == ":WAVeform:SOURce?":
                return f"CHAN{self.measure_channel}"
            if command == ":WAVeform:FORMat?":
                return self.waveform_format or "ASCII"
            if command == ":WAVeform:POINts?":
                return str(self.points)
            if command == ":TIMebase:MODE?":
                return self.timebase_mode
            if command == ":TIMebase:RANGe?":
                return f"{self.timebase_range_s:.12g}"
            if command == ":TIMebase:REFerence?":
                return self.timebase_reference
            if command == ":TIMebase:POSition?":
                return f"{self.timebase_position_s:.12g}"
            if command == ":ACQuire:SRATe?":
                return f"{1.0 / self.sample_interval_s:.12g}"
            if command == ":ACQuire:TYPE?":
                return self.acquisition_mode
            if command == ":ACQuire:MODE?":
                return self.keysight_realtime_mode
            if command == ":TER?":
                value = "1" if self.trigger_event else "0"
                self.trigger_event = False
                return value
            if command == ":OPERegister:CONDition?":
                if self.status_error is not None:
                    raise self.status_error
                return "0" if self.completed else "8"
            if command == ":AER?":
                value = "1" if self.arm_event else "0"
                self.arm_event = False
                return value
            if command == ":TRIGger:EDGE:SOURce?":
                source = str(self.trigger_source).upper()
                return "EXT" if source.startswith("EXT") else source.replace("ANNEL", "")
            if command == ":TRIGger:MODE?":
                return self.trigger_type
            if command == ":TRIGger:EDGE:SLOPe?":
                return self.trigger_slope
            if command == ":TRIGger:SWEep?":
                return "NORM" if self.trigger_normal else "AUTO"
            if command == ":TRIGger:EDGE:LEVel?":
                return f"{self.trigger_level:g}"
            if command == ":TRIGger:EDGE:COUPling?":
                return self.trigger_coupling
            if command == ":TRIGger:EDGE:REJect?":
                return self.trigger_reject
            if command == ":TRIGger:HFReject?":
                return "1" if self.trigger_hf_reject else "0"
            if command == ":TRIGger:NREJect?":
                return "1" if self.trigger_noise_reject else "0"
            if command == ":EXTernal:PROBe?":
                return f"{self.external_probe_attenuation:g}"
            if command == ":EXTernal:RANGe?":
                return f"{self.external_range_v:g}"
            if command == ":EXTernal:UNITs?":
                return self.external_units
            if command == ":EXTernal:BWLimit?":
                return "0"
            raise AssertionError(f"unexpected Keysight query: {command}")
        match = re.fullmatch(
            r"(?:DISPlay:WAVEView1:CH([1-8]):STATE|"
            r"CH([1-8]):(COUPling|BANdwidth|TERmination|"
            r"PROBEFunc:EXTAtten|PRObe:GAIN|SCAle|OFFSet))\?", command)
        if match:
            channel = int(match.group(1) or match.group(2))
            field = "STATE" if match.group(1) else match.group(3)
            values = {
                "STATE": "1" if self.channel_display[channel] else "0",
                "COUPling": self.channel_coupling[channel],
                "BANdwidth": f"{self.channel_bandwidth_hz[channel]:g}",
                "TERmination": f"{self.channel_impedance_ohm[channel]:g}",
                "PROBEFunc:EXTAtten": (
                    f"{self.channel_probe_attenuation[channel]:g}"),
                "PRObe:GAIN": f"{self.channel_probe_gain[channel]:g}",
                "SCAle": f"{self.channel_scale_v[channel]:g}",
                "OFFSet": f"{self.channel_offset_v[channel]:g}",
            }
            return values[field]
        values = {
            "WFMOutpre:YMUlt?": "nan" if self.bad_calibration else "0.002",
            "WFMOutpre:YOFf?": "-1.5", "WFMOutpre:YZEro?": "0.1",
            "WFMOutpre:XINcr?": f"{self.sample_interval_s:.12g}",
            "WFMOutpre:NR_Pt?": str(self.points),
            "WFMOutpre:XZEro?": f"{-self.current_trigger_phase * self.sample_interval_s:.12g}",
            "WFMOutpre:PT_Off?": str(int(round(
                self.points * self.horizontal_position_percent / 100.0))),
            "HORizontal:MODE:SAMPLERate?": f"{1.0 / self.sample_interval_s:.12g}",
            "HORizontal:MODE:RECOrdlength?": str(self.points),
            "ACQuire:MODe?": self.acquisition_mode,
            "HORizontal:FASTframe:STATE?": "ON" if self.tek_fastframe else "OFF",
            "ACQuire:FASTAcq:STATE?": "ON" if self.tek_fastacq else "OFF",
            "ACQuire:SEQuence:NUMSEQuence?": str(self.tek_sequence_count),
            "ACQuire:NUMACq?": str(self.acquisition_count),
            "DATa:SOUrce?": f"CH{self.measure_channel}",
            "DATa:ENCdg?": self.waveform_encoding or "ASCII",
            "DATa:WIDth?": str(self.waveform_width or 2),
            "DATa:STARt?": str(self.data_start),
            "DATa:STOP?": str(self.data_stop),
            "HORizontal:DELay:MODe?": self.horizontal_delay_mode,
            "HORizontal:POSition?": f"{self.horizontal_position_percent:g}",
            "TRIGger:A:EDGE:COUPling?": self.trigger_coupling,
            "AUXIn:PRObe:GAIN?": f"{self.aux_probe_gain:g}",
            "AUXIn:PRObe:RESistance?": f"{self.aux_probe_resistance_ohm:g}",
            "AUXIn:PRObe:ID:TYPe?": self.aux_probe_id_type,
        }
        if command in values:
            return values[command]
        if command == "TRIGger:STATE?":
            if self.never_arm:
                return "SAVE"
            self.trigger_state_queries += 1
            return ("ARMED" if self.trigger_state_queries <= self.ready_after_queries
                    else "TRIGGER:STATE READY")
        if command == "TRIGger:A:EDGE:SOUrce?":
            source = str(self.trigger_source).upper()
            return "TRIGGER:A:EDGE:SOURCE " + (
                "AUX" if source.startswith("AUX") else source)
        if command == "TRIGger:A:TYPe?":
            return self.trigger_type
        if command == "TRIGger:A:EDGE:SLOpe?":
            return self.trigger_slope
        if command == "TRIGger:A:MODe?":
            return "TRIGGER:A:MODE NORM" if self.trigger_normal else "AUTO"
        if command == "ACQuire:STOPAfter?":
            return "SEQUENCE" if self.single_sequence else "RUNSTOP"
        if command == "TRIGger:AUXLevel?" or (
                command.startswith("TRIGger:A:LEVel:CH") and command.endswith("?")):
            return f"{self.trigger_level:g}"
        if command == "ACQuire:STATE?":
            if self.status_error is not None:
                raise self.status_error
            return "0" if self.completed else "1"
        raise AssertionError(f"unexpected Tektronix query: {command}")

    def query_binary_values(self, command, *, datatype, container, **kwargs):
        self._check_open()
        self.events.append(("binary", command, datatype, dict(kwargs)))
        expected_command = ":WAVeform:DATA?" if self.dialect == "keysight" else "CURVe?"
        expected_type = "B" if self.dialect == "keysight" else "b"
        if command != expected_command or datatype != expected_type:
            raise AssertionError(
                f"wrong binary request {command!r}/{datatype!r}; expected "
                f"{expected_command!r}/{expected_type!r}")
        if self.dialect == "tek" and kwargs.get("is_big_endian") is not False:
            raise AssertionError("one-byte Tektronix read must explicitly select little endian")
        if container is not np.ndarray:
            raise AssertionError("binary waveform must be requested as numpy.ndarray")
        if not self.completed or self._wave is None:
            raise AssertionError("waveform read before a completed single shot")
        self.binary_timeouts_ms.append(self.timeout)
        self.binary_reads += 1
        return self._wave.copy()

    def close(self):
        self.events.append(("close",))
        self.closed = True


class StrictResourceManager:
    def __init__(self, instrument, resources=None):
        self.instrument = instrument
        self.resources = tuple(resources or (instrument.resource,))
        self.closed = False
        self.backend_argument = None
        self.opened_resource = None

    def list_resources(self):
        if self.closed:
            raise RuntimeError("resource manager used after close")
        return self.resources

    def open_resource(self, resource):
        if self.closed:
            raise RuntimeError("resource manager used after close")
        if resource not in self.resources:
            raise AssertionError(f"unexpected VISA resource: {resource}")
        self.opened_resource = resource
        return self.instrument

    def close(self):
        self.closed = True


def simulated_pyvisa(instrument, resources=None):
    manager = StrictResourceManager(instrument, resources=resources)

    def resource_manager(backend):
        if backend != "@py":
            raise AssertionError("scope backend did not force pyvisa-py")
        manager.backend_argument = backend
        return manager

    return SimpleNamespace(ResourceManager=resource_manager), manager
