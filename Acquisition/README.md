# PROACT trace-acquisition framework

Unified, resumable, fast side-channel trace acquisition for six selectable PROACT
workloads — **Sw-RV AES**, **masked Sw-RV AES**, **AES1**, **AES2**, **Xoodyak** and
**ASCON** — on a ChipWhisperer-Husky, with a strictly simulated Keysight/Tektronix
oscilloscope backend. The acquisition protocol has bounded prior ASIC/Husky evidence;
this source-only public composition is verified offline. A physical scope is not yet
verified. The unmasked and masked programs share the same physical Ibex core.

The public clone supplies the `proact_host` Python package and all CPA/TVLA model
sources. Built controller and Sw-RV firmware are separate artifacts and are not stored
in this repository.

## Quick start (new PC)

```bash
./install.sh          # creates .venv, installs only the missing libraries
./run.sh              # interactive wizard  (or: ./run.sh --target aes1 --traces 10000)
```

`install.sh --check` reports what is missing without installing and exits nonzero when
the environment is not ready. `install.sh --system` installs into the current Python
instead of a venv. Python 3.10 or newer is required because the pinned VISA stack
does not support older Python releases. Both launchers are valid POSIX shell scripts,
so invoking them through `sh` is supported.

For a live run, set `PROACT_REPO` to a full checkout containing the built controller
and Sw-RV images, or set the individual paths:

```bash
export PROACT_CONTROLLER_VMEM=/path/to/main.vmem
export PROACT_SWRV_FW_DIR=/path/to/unmasked-sw-rv-vmem-directory
export PROACT_MASKED_FW_DIR=/path/to/masked-sw-rv-vmem-directory
```

AES1, AES2, Xoodyak and ASCON require the controller image. `sw_rv` additionally
requires the unmasked imem/dmem pair; `sw_rv_masked` requires the pinned masked pair.
Missing artifacts fail before an instrument is opened.

**Hardware needed:** the PROACT board's MCP2200 UART, plus either a
ChipWhisperer-Husky or a supported oscilloscope. Scope capture can use Husky HS2 for
the board clock or `--scope-clock-source external` with another clock generator.
The wizard suggests a stable `/dev/serial/by-id/...` path when one is available, and
the live transport can otherwise identify the MCP2200. Close the PROACT GUI / WaveForms
first (they hold the devices). The trigger jumper should be on **"trigger cfg"** for
per-core and AEAD narrow triggers.

## Usage

```bash
./run.sh                                                  # wizard: pick core/key/input/count/backend
./run.sh --target aes1    --traces 100k --key fixed --input random
./run.sh --target aes1    --traces 100k --clock-mhz 50 --baud auto
./run.sh --target aes1    --traces 100k --port /dev/serial/by-id/usb-Microchip_MCP2200_...
./run.sh --target xoodyak --traces 1M   --trigger 0x11   # narrow AEAD trigger (key->nonce, ~10cy)
./run.sh --target sw_rv   --traces 5M                     # re-run to RESUME from the last checkpoint
./run.sh --target sw_rv_masked --traces 100k --warmup 1k --format h5
./run.sh --target sw_rv_masked --traces 10k --tvla        # 20k total, then TVLA
./run.sh --target sw_rv_masked --traces 100k --auto-cpa   # first-order diagnostic only
./run.sh --target aes2    --traces 500k --estimate        # disk-space estimate, no capture
./run.sh --target aes1    --traces 100k --format h5
./run.sh --target aes1    --traces 10k --format h5 --format csv
./run.sh --target aes1    --traces 10k --auto-cpa --warmup 1000
./run.sh --target aes1    --traces 10k --tvla              # 10k/class = 20k total
./run.sh --target aes1    --traces 10k --tvla --auto-cpa   # CPA uses random group only
./run.sh --target aes1    --backend scope --scope-clock-source husky \
  --scope-resource TCPIP0::192.168.1.50::hislip0::INSTR
```

Options: `--clock-mhz` (default 50), `--baud auto|N`, `--port`,
`--key fixed|random`, `--input fixed|random`, `--trigger` (AES: auto|core|
firmware; AEAD: triggercfg hex), `--samples` (0 = auto-size), `--offset`, `--gain`,
`--suffix`, `--chunk`, `--seed`, `--backend husky|scope`, `--reset` (force reprogram),
`--scope-resource`, `--scope-clock-source husky|external`,
`--scope-dialect auto|keysight|tek`, `--scope-channel`,
`--scope-trigger-source`, `--scope-trigger-level`,
`--scope-transfer-timeout-ms` (default: automatically sized for the record),
`--format native|h5|csv` (repeatable), `--allow-nofit`, `-y` (skip confirm),
`--warmup N`, `--tvla`, `--tvla-order block|alternate`, `--auto-cpa`, `--estimate`,
`--wizard`.

The live wizard asks for target frequency first. The ASIC UART divisor is fixed at
27 by the RTL reset value and the supported controller does not change it, so changing
the target clock changes the UART wire rate. `--baud auto` scales the bench-proven
host setting of 115200 baud at 50 MHz: 25 MHz selects 57600 baud. At 50 MHz, the
divisor-implied rate is 115740.740741 baud, a 0.4672% mismatch from the proven host
setting. An explicit baud is accepted only within 2% of the implied rate.

For Husky capture the requested clock is applied and checked before UART opens.
Oscilloscope capture also defaults to `--scope-clock-source husky`: Husky drives HS2
at the requested frequency, verifies its reported clock and remains connected while
the oscilloscope records the waveform. Choose `external` only when another clock
source is already connected and set correctly; that frequency is declared in the
metadata but cannot be verified by this program.

## What it does, end to end

configure the selected board clock → open UART at the derived speed → reset and upload
controller firmware if needed → verify each core
against a software reference (KAT) → pre-run validation (trigger fires, no clipping,
outputs consistent) → auto-size the capture window → checkpointed capture loop with a
live progress bar → watchdog retry/recover on transient faults → atomic checkpoints →
final summary. Re-running the same command **resumes** from the last checkpoint.

For scope capture, the instrument error queue and immutable acquisition, trigger,
channel and waveform state are verified before the dataset opens, before every chunk
checkpoint and before the final checkpoint. A failed verification withholds the new
`done` marker, so rows beyond the last verified checkpoint never become authoritative.
Preflight keeps only one reference waveform and compares large records in bounded
blocks, avoiding a 20-record memory spike at multi-million-sample record lengths.

Controller and software-core firmware identities are SHA-256 pinned for the complete
campaign. The files are re-hashed before and after every reload or reprogram path;
changed host firmware stops the run and cannot be recorded under the old identity.

For AES and Sw-RV, trigger `auto` follows the selected core and `core` explicitly pins
the trigger mux to it. `firmware` selects the real controller software-trigger input
(CFGSEL 0); the supported controller never pulses that bit, so preflight will
normally refuse it with “trigger never fired.” It is retained for compatibility with
firmware that deliberately implements a controller-generated trigger.

`--warmup N` runs exactly N successful core operations before recording in every
capture session, including a resumed session and after fault recovery reopens or
reprograms the controller. Their reference outputs are checked and the operations are
discarded. Count, duration, reason and resume point are recorded in metadata.

Every captured row is checked before storage. NaN/Inf, constant, clipped and repeated
stale records are discarded; 20 consecutive waveform-quality rejects stop the run
with an instrument-focused error. These failures never trigger UART reopen or
controller reprogramming. Storage errors also fail immediately while preserving the
native checkpoint.

## Automatic analysis

`--tvla` interprets `--traces` as a count **per class**: `--traces 10k --tvla`
records 10,000 fixed-input and 10,000 random-input traces (20,000 total), then saves
the full first-order Welch t curve and group means/variances in `_tvla_arrays.npz`, a
per-sample CSV and a JSON report. Group, acquisition block and elapsed timestamp are
stored beside every native trace.

The default `--tvla-order block` shuffles each exactly balanced 100-row block. This
is the recommended protocol because the bench has a measured odd/even acquisition
component. `--tvla-order alternate` gives the requested strict fixed,random order,
but the saved report marks it parity-confounded; do not use that run alone for a
leakage claim.

`--auto-cpa` selects the model by core and streams only this command's completed
native dataset:

- AES1/AES2: last-round register HD, hypotheses are round-10 subkey bytes.
- Sw-RV: first-round S-box HW, hypotheses are round-0 key bytes.
- Masked Sw-RV: the same first-round S-box model as a **first-order diagnostic**.
  Effective masking may suppress it. The automatic path does not run a second-order
  attack, and failure to recover is not proof of security.
- Xoodyak/ASCON: specialized round-1 positional models. They report positional
  hypothesis ranks and chance statistics; no full-key decoder runs automatically.

The AEAD automatic model currently accepts only Husky captures using triggercfg
`0x12`; its target-cycle window is derived from the recorded ADC multiplier and
offset. Other AEAD trigger modes may still be captured, but are not auto-scored.
`sw_rv` loads the configured **unmasked** tiny-AES program. `sw_rv_masked` is a
distinct target with pinned masked imem/dmem hashes, so it cannot silently fall back
to the unmasked image. Before boot it enables the on-chip RNG; before every attempted
AES operation it sends a fresh nonzero 32-bit seed from the host OS CSPRNG. This
includes KAT, preflight, warm-up, retries and stored capture operations. The policy
and firmware hashes are in the immutable capture identity. `--seed` controls the
reproducible plaintext/key schedule; it does not reuse masked RNG seeds.

CPA requires a fixed key and varying input. When combined with TVLA, CPA uses only
the stored random-input group and reports that smaller effective N. Results are final
point evaluations; the tool does not infer or claim a minimum trace count.

Scope datasets preserve raw, unaligned waveforms plus one trigger-position value per
row. Automatic CPA/TVLA evaluates the same native rows twice: the ordinary raw result
and a read-only fractional-alignment result under a `_scope_aligned` basename. The
corresponding `_cpa_raw_vs_scope_aligned.json` or
`_tvla_raw_vs_scope_aligned.json` binds both reports. Alignment uses the configured
10%-of-record reference, bounded linear interpolation and no wraparound; it never
rewrites native traces and does not establish a lower trace requirement by itself.

## Output formats

The native v2 dataset is the complete basename family: `_traces.npy`, `_meta.npz`,
`_input.npy`, `_out.npy`, `_group.npy`, `_block.npy`, `_ts.npy`, `_key.npy` when
keys vary, and `_scope_trigger_index.npy` for scope captures. **Keep or move all of
these files together.** Per-row sidecars are memmaps; the small atomic metadata file
holds `done`, an immutable capture signature, a relative manifest and a SHA-256
checkpoint digest over every authoritative trace and sidecar row plus `done`. Existing
pre-digest rows remain explicitly marked as an unverified legacy prefix. This makes
intermediate checkpoint work proportional to newly dirty pages instead of rewriting
millions of rows. The loader also reads legacy embedded-metadata datasets; a partial
legacy set is migrated once, atomically, before append.

Additional formats are produced only after every requested trace is safely
checkpointed:

- `--format h5` writes a chunked, compressed `.h5` containing `traces`, `input`,
  `output`, `key`, optional TVLA/scope-trigger fields, the ADC capture-unit scale,
  native integrity summary, instrument calibration and capture configuration.
  Compatibility aliases expose `plaintext`/`nonce` and `ciphertext` without
  duplicating the stored arrays.
- `--format csv` writes raw int16 samples as a wide `.csv` plus `_csv_meta.json`.
  CSV is intended for interchange and small captures. It is slow and very large:
  one million 596-sample AES records is roughly 3–4 GB. Prefer HDF5 for analysis.
  The JSON sidecar is the CSV completion marker and binds the exact CSV basename,
  byte length, SHA-256 and export generation. A missing or mismatched sidecar means
  that the CSV is incomplete.

Both exporters keep only rows below the native `done` checkpoint. An interrupted
capture remains native-only and exports automatically after a later completed resume.
Convert stored int16 values to the backend capture unit with
`capture_unit = sample_raw / scale_counts_per_capture_unit`. Husky capture units are
normalized ADC codes and do not carry a physical-volts calibration. The scope backend
stores `scope_volts_per_capture_unit` and `scope_volts_offset` in instrument metadata;
for those records use `volts = capture_unit * scope_volts_per_capture_unit +
scope_volts_offset`.

## Layout

```
acquire.py            CLI (rich + questionary + click)
acq/                  framework
  config.py           campaign description
  serial_ports.py     metadata-only USB UART discovery; never opens a port
  inputs.py           key/input policy (fixed/random), seeded & reproducible
  store.py            row memmaps + SHA-256-bound atomic checkpoint
  native.py           v2 sidecar/legacy dataset loader + capture identity
  exporters.py        atomic post-capture HDF5 and CSV exports
  analysis.py         exact-dataset CPA + first-order Welch TVLA
  alignment.py        read-only per-row scope phase correction for paired analysis
  checkpoint.py       resume + integrity
  validate.py         pre-run KAT + trigger/clip checks
  progress.py         plain (tqdm) / fancy (rich) UI
  space.py            disk-space estimate
  run.py              A-to-Z driver (loop, watchdog, summary)
  paths.py            public checkout + explicit external firmware resolution
  backend/husky.py    ChipWhisperer-Husky (verified)
  backend/scope.py    allowlisted Keysight/Tek pyvisa backend; strict simulation only
  targets/            adapters (aes1, aes2, sw_rv, sw_rv_masked, AEAD)
analysis_models/      public AES/ASCON/Xoodyak CPA model primitives
docs/                 scope guide and verification report
```

## Adding another core

Drop one adapter in `acq/targets/` (subclass `Target`, implement select / run /
expected / configure_trigger) and register it in `acq/targets/__init__.py`.

## Oscilloscope

`--backend scope` uses pyvisa for allowlisted Keysight InfiniiVision 3000 X-Series
and Tektronix 4/5/6 Series MSO models.
The normal bench path keeps Husky connected as the board clock source while the
oscilloscope supplies waveform samples. `--scope-clock-source external` is available
for a separately generated clock and is recorded as an unverified declaration.
Wiring, the exact model list, SCPI state checks and trigger handling are in
`docs/SCOPE_GUIDE.md`. Measurement-channel analog settings are read, persisted and
pinned, but the operator chooses them for the real probe/amplifier. The trigger path
is forced to its safe DC/high-Z state when controllable. Each completed shot records
its trigger index; native rows remain raw and paired analysis creates separate aligned
artifacts. The binary-transfer timeout grows with record size or can be overridden by
`--scope-transfer-timeout-ms`. **No physical scope has been verified.** Nonzero scope
`--offset` remains refused because horizontal delay is not implemented and read back.

## Status

`docs/ACQUISITION_REPORT.md` — what is implemented, what is hardware-verified (the
six targets in the bounded 2026-09-11 Husky run, historical resume/watchdog evidence),
and what is not testable without a scope.

## Tests

```bash
PY="$(cat .venv_path)"
"$PY" -m pytest -q acq/tests -W error
PROACT_ACQ_PYTHON="$PY" "$PY" tools/verify_cli_matrix.py
sh -n install.sh run.sh
```

Current public acceptance: the warning-clean pytest suite passes **257 tests**, including
strict simulated scope capture through integrity-checked storage, exports and paired
raw/aligned TVLA and CPA. The CLI matrix passes **235/235 configurations**, including
explicit scope-transfer-timeout and narrow-record disk-estimate cases. These offline
checks accessed no physical oscilloscope; simulator results are not physical-scope
validation. The integrated public host suite separately passes **1,513 tests** with
**43 explicit skips** for design/firmware artifacts omitted from this repository.
