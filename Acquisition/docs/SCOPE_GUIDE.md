# Capturing with an oscilloscope

This guide explains the supported instruments, wiring, trigger synchronization and
the checks performed by `acq/backend/scope.py`. The backend has passed strict,
deterministic Keysight and Tektronix simulations. **No physical oscilloscope has been
tested yet.** Simulation verifies our command/state logic; it cannot validate the
firmware, probe path, analog bandwidth or SNR of a real bench.

## 1. Supported instruments

The backend deliberately rejects untested model families after reading `*IDN?`:

- Keysight/Agilent InfiniiVision 3000 X-Series models whose model field is
  `DSOX3xxx...` or `MSOX3xxx...`.
- Tektronix `MSO44`, `MSO46`, `MSO44B`, `MSO46B`, `MSO54`, `MSO56`, `MSO58`,
  `MSO54B`, `MSO56B`, `MSO58B`, `MSO58LP`, `MSO64`, `MSO64B`, `MSO66B`,
  `MSO68B` and `LPD64`.

Tek AUX triggering is allowed only on the capable models in that list. The original
`MSO54`, `MSO56` and `MSO58` have no supported independent AUX input; connect the
trigger to a spare analog channel such as CH2. A newer or different model must be
added to the allowlist only after its programmer manual and simulator coverage are
updated.

`--scope-dialect auto` selects the vendor from `*IDN?`. An explicit dialect must
still match the reported vendor and model. If VISA discovery finds more than one
instrument, the program refuses to guess; pass the exact `--scope-resource`.

## 2. Physical wiring and analog setup

The usual PROACT bench has three signal paths:

- **Husky HS2 → PROACT board clock.** This is the default with
  `--scope-clock-source husky`. The acquisition program configures the requested
  clock, verifies Husky's reported state and holds that connection open for the run.
- **Power/leakage signal → measurement channel**, normally CH1.
- **Chip `Trigger_cfg_out` → trigger input.** Put the board jumper on **trigger cfg**
  and connect PIN 13 either to a spare analog channel or to Keysight Ext Trig / a
  supported Tek AUX input.

Keep a raw CMOS trigger input **DC-coupled and high-Z (1 MOhm)**. Do not terminate an
unbuffered trigger pin into 50 Ohm. The backend enforces and reads back DC plus 1 MOhm
when an analog channel is used for the trigger. On Keysight Ext Trig it enforces 1x
probe attenuation, volts, 8 V range, DC coupling and disabled reject filters; that
input is fixed at 1 MOhm. On Tek AUX it enforces DC trigger coupling and requires a
reported 1x attached-probe gain. Tek does not expose equivalent AUX range,
termination and bandwidth controls in the supported command set, so the backend does
not invent them.

Set the **measurement channel** deliberately on the front panel before acquisition:

- DC coupling and full available bandwidth for the baseline;
- 1 MOhm unless the amplifier is explicitly designed to drive 50 Ohm;
- sample mode with averaging, high-resolution acquisition and math filtering off;
- enough vertical range and a suitable offset to avoid either ADC rail.

The program reads, stores and pins the measurement channel's coupling, impedance,
probe factor, range/scale, offset and bandwidth state for the dataset. It does not
choose these probe-dependent values for you. A later change is detected before a
checkpoint is published. `--gain` controls Husky only.

With `--scope-clock-source external`, another generator must already be connected and
verified by the operator. `--clock-mhz` still determines UART and requested sample
timing, but the source is recorded as an unverified external declaration.

## 3. VISA connection

Python 3.10 or newer is required by the pinned VISA stack. The backend uses `pyvisa`
with `pyvisa-py`; NI-VISA is not required. Typical resources are:

| link | VISA resource |
|---|---|
| LAN HiSLIP | `TCPIP0::<ip>::hislip0::INSTR` |
| LAN VXI-11 | `TCPIP0::<ip>::inst0::INSTR` |
| LAN raw socket | `TCPIP0::<ip>::5025::SOCKET` |
| USBTMC | `USB0::<vid>::<pid>::<serial>::INSTR` |

LAN is usually easiest with `pyvisa-py`. USBTMC needs `pyusb`, libusb and a suitable
udev rule. Keysight/Agilent vendor IDs commonly include `0x2A8D`/`0x0957`; Tektronix
uses `0x0699`.

```python
import pyvisa

rm = pyvisa.ResourceManager("@py")
inst = rm.open_resource("TCPIP0::192.168.1.50::hislip0::INSTR")
inst.timeout = 8000  # milliseconds
inst.write("*CLS")
print(inst.query("*IDN?"))
```

Do not set `read_termination` on `::INSTR` sessions; that can truncate a binary
waveform. The backend applies newline framing only to raw `::SOCKET` sessions.

## 4. Configuration and single-shot trigger protocol

The program always uses **NORMAL edge triggering**, never AUTO. It also forces one
ordinary sample-mode acquisition: Keysight real-time/NORMAL and Tek sequence count
one with FastFrame/FastAcq off. It fixes trigger type, positive/rising slope, source,
coupling, level and acquisition mode, then reads each setting back. A mismatch or
instrument error aborts before a dataset is opened.

The measurement channel and trigger channel must differ. `EXTernal` maps to Keysight
Ext Trig and to Tek AUX. Use `CHANnel2`/`CH2` for an analog trigger channel.

For every record:

1. **Keysight:** clear old trigger/arm events, send `:SINGle`, require a fresh
   `:AER? = 1`, and require the Run bit in `:OPERegister:CONDition?` to still be set
   before the target runs. Completion requires that Run clears and `:TER? = 1`.
2. **Tektronix:** send `ACQuire:STATE RUN` and wait for `TRIGger:STATE? = READY`;
   `ARMED` is not ready. Completion requires stopped acquisition state and exactly
   one acquisition in `ACQuire:NUMACq?`.
3. Query the completed record's waveform preamble, require the exact point count and
   sample interval, calculate its trigger index, then fetch the binary waveform.

A stale event, premature/spurious trigger, missed trigger, unexpected stop, status
query failure, SCPI error or changed immutable setting fails closed. The row is not
accepted as valid data.

## 5. Record timing, phase and analysis

Both dialects place the nominal trigger at **10% of the record**. This deterministic
configured position is the durable reference across sessions. The preamble is
queried after every completed shot because an asynchronous scope can report a
fractional-sample trigger position that differs between rows. The observed position
must remain within one sample of the reference.

Native storage keeps the normalized int16 waveform **raw and unaligned** and stores
the original per-row position in `_scope_trigger_index.npy`. It also retains the
configure-time voltage calibration and timing metadata. Convert a stored code with:

```text
capture_unit = sample_raw / scale_counts_per_capture_unit
volts = capture_unit * scope_volts_per_capture_unit + scope_volts_offset
```

When `--tvla` or `--auto-cpa` is selected for a scope dataset, the program performs a
paired analysis over the same rows:

- the ordinary outputs use the authoritative raw traces;
- outputs based on bounded linear fractional-sample correction use the
  `_scope_aligned` name;
- `_tvla_raw_vs_scope_aligned.json` or `_cpa_raw_vs_scope_aligned.json` binds the two
  reports and their preprocessing provenance.

Alignment uses the recorded per-row trigger index, edge extension and no wraparound.
It is a read-only analysis view; it never changes the native trace file. The tool does
not claim that alignment reduces the required trace count until this is demonstrated
on physical captures.

`--samples 0` requests a 2,000-sample scope pilot. Keysight RAW lengths are restricted
to `100`, `250`, `500`; the 1/2/5 sequence from 1 k through 500 k; and `1 M`, `2 M`,
`4 M` or `8 M`. The returned point count must be exact.
Nonzero scope `--offset` is refused because horizontal delay is not implemented and
verified.

## 6. Checkpoint and export integrity

The scope error queue and all immutable acquisition, trigger, channel and waveform
settings are verified before native storage opens, before each periodic chunk
checkpoint and before the final checkpoint. If that verification fails, the program
withholds the new `done` marker; rows after the last verified checkpoint are not
authoritative.

Every new native checkpoint has a SHA-256 digest covering the trace rows, every
present row sidecar (including the trigger index) and the authoritative `done` value.
Older pre-digest rows are explicitly reported as an unverified legacy prefix rather
than retroactively claimed as verified. HDF5 exports carry the native integrity
summary. The CSV completion sidecar binds the exact CSV basename, byte size, SHA-256
and a generation identifier; an unmarked or mismatched CSV is incomplete.

## 7. Throughput and timeout

The current backend transfers one waveform per trigger. For large records, the
binary-transfer timeout is automatically increased from the record size while normal
control queries keep their shorter timeout. Override only the binary timeout with
`--scope-transfer-timeout-ms N` if a measured link requires it. The selected value is
stored in metadata.

Segmented/FastFrame batching is not implemented. One-shot VISA overhead can therefore
limit throughput, but each stored row remains tied to a separately confirmed trigger.

## 8. Run command

```bash
./run.sh --target aes1 --traces 2 --backend scope \
  --clock-mhz 50 --scope-clock-source husky \
  --scope-resource TCPIP0::192.168.1.50::hislip0::INSTR \
  --scope-dialect auto --scope-channel 1 \
  --scope-trigger-source CHANnel2 --scope-trigger-level 1.5 \
  --samples 2000 --suffix _scope_acceptance
```

Use a new suffix for the first physical check. If the trigger is on Ext/AUX, change
the source accordingly and confirm that the chosen Tek model supports AUX.

## 9. What the offline tests prove

`acq/tests/scope_simulator.py` provides strict Keysight and Tektronix state machines:
unknown SCPI is rejected; unsafe persistent modes start enabled; trigger, acquisition,
horizontal and analog state must be corrected and read back; stale, early, missing
and duplicate trigger events are exercised; point count, timing, per-shot phase,
calibration, waveform encoding, transfer timeout, cleanup and resume are checked.
Integration tests drive native storage, HDF5/CSV export, TVLA and CPA, including the
paired raw/aligned scope analysis.

The final warning-clean public suite passes **257 tests**. The separate CLI matrix passes
**235/235 configurations**, including explicit transfer-timeout and narrow-record
disk-estimate checks. Large-record preflight also verifies bounded retention: it keeps
one reference waveform instead of all 20 validation records. These results accessed
no physical oscilloscope.

## 10. First physical-scope acceptance

Start with two records and proceed only when all checks pass:

1. `*IDN?` matches an allowed model and the intended dialect/resource.
2. The display shows one visible trigger and one power waveform per operation.
3. Trigger and measurement channel coupling, impedance/probe factor, bandwidth and
   range match the real wiring.
4. Stored point count, interval, trigger index and voltage calibration match the
   scope preamble.
5. Both traces are finite, varying, non-clipped and have correct reference outputs.
6. The scope and clock handles stop and close cleanly. Re-running the completed
   command finalizes offline without reopening VISA.

Only this physical check can validate command compatibility for the exact firmware
and the analog signal quality.

Official command references:

- [Keysight InfiniiVision 3000 X-Series Programmer's Guide](https://www.keysight.com/us/en/assets/9023-40024/miscellaneous/3kT_X-Series_prog_guide.pdf)
- [Tektronix 4/5/6 Series MSO Programmer Manual](https://download.tek.com/manual/4-5-6_MSO_Programmer_077130526.pdf)
