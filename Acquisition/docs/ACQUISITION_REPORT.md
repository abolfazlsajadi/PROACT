# PROACT acquisition framework — verification report

Generated 2026-08-25; updated 2026-09-14. This report separates **implemented**,
**bounded live ASIC/Husky evidence**, **offline verification**, and **pending physical
oscilloscope validation**.

The live evidence predates the source-only public integration and used a separately
packaged host runtime. The public composition described here has been regression-tested
offline; it must not be described as a new live-hardware validation.

## What was built

A unified, resumable acquisition framework under `acq/`, driven by one fancy CLI
(`./run.sh`). Shared core + thin per-core adapters + pluggable measurement backend.

```
acquire.py                 fancy CLI (rich + questionary + click): wizard OR flags
acq/config.py              AcqConfig — the single campaign description
acq/inputs.py              key/input policy matrix, seeded + reproducible-by-index
acq/store.py               v2 row memmaps + SHA-256-bound atomic checkpoint
acq/native.py              exact-done v2/legacy loader + immutable capture identity
acq/exporters.py           completed-capture HDF5/CSV exports; native remains authoritative
acq/analysis.py            exact-dataset Welch TVLA + core-specific CPA adapters
acq/alignment.py           read-only scope trigger-phase view for paired analysis
acq/serial_ports.py        metadata-only MCP2200 UART discovery + stable aliases
acq/checkpoint.py          resume + integrity (regenerate-and-compare)
acq/validate.py            pre-run KAT + trigger/clip/consistency preflight
acq/progress.py            PlainUI (tqdm) / RichUI (rich bar+panel), resume at %
acq/space.py               disk-space estimate + free-space check
acq/run.py                 A-to-Z driver: clock→UART→verify→preflight→loop→watchdog→summary
acq/backend/husky.py       ChipWhisperer-Husky backend (connected)
acq/backend/clock.py       Husky HS2 clock holder for oscilloscope captures
acq/backend/scope.py       allowlisted Keysight/Tek pyvisa backend (simulated only)
acq/targets/{aes1,aes2,sw_rv,sw_rv_masked,aead}.py   workload adapters
```

## Per-target status

| core | implemented | bounded ASIC/Husky run, 2026-09-11 | historical live evidence | automatic analysis meaning |
|---|---|---|---|---|
| AES1 | yes | 200/200 valid | 6000-row resume and watchdog runs | last-round AES CPA |
| AES2 | yes | 200/200 valid | 120-row live run | last-round AES CPA |
| Sw-RV | yes | 200/200 valid | 120-row live program-upload run | first-round AES CPA |
| Masked Sw-RV | yes | 200/200 valid | first bounded live run | first-order diagnostic only |
| Xoodyak | yes | 200/200 valid | 120-row live run | positional hypotheses only |
| ASCON | yes | 200/200 valid | 120-row live run | positional hypotheses only |

Each bounded run used 100 fixed-input and 100 random-input rows. Across all six, every
requested row completed with zero capture failures, recoveries, quality rejects and
reference-output mismatches. These 200-row smoke runs verify the path and protocol;
their CPA/TVLA outputs do not establish minimum trace counts or absence of leakage.

## Feature verification

| feature | status | evidence |
|---|---|---|
| A-to-Z run (reset→program→verify→capture→store→summary) | **bounded hw-verified** | all 6 targets in the 2026-09-11 200-row runs; historical controller recovery evidence |
| Pre-run validation (KAT, trigger, clip, consistency) | **hw-verified** | preflight refuses on bad output/clip/dead-line |
| Resume / checkpoint (atomic) | **hw-verified** | interrupt@5500/6000 → resume → 6000/6000, 0 gaps, 6000 unique inputs |
| Watchdog / retry | **hw-verified** | 54 injected faults → 300/300 valid, 0 bad counted |
| Reprogram recovery (persistent fault) | implemented | shares campaign.py logic field-proven over the multi-day autopilot |
| Progress bar (tqdm + rich), resume % | **hw-verified** | live runs; RichUI panel+summary |
| Key/input policy matrix | unit + partial hw | reproducibility unit tests; live fixed-key/random-input |
| Disk-space estimate + check | **verified** | `--estimate` dry-run; refuses a 3.5 TB run vs 318 GB free |
| Legacy embedded native storage | **historically hw-verified** | prior 6000-row capture/resume; remains read-compatible |
| Native v2 sidecar storage + bounded checkpoint | **offline-verified** | `done` plus trace/all-present-sidecar rows are SHA-256 bound; corruption/resume/orphan/mismatch cases fail closed; pre-digest rows remain an explicit unverified legacy prefix |
| Backend abstraction | **hw-verified (Husky)** | Husky path used for all runs |
| Fancy CLI: wizard + flags + summary | **verified** | flag-mode live run; rich panel rendered |
| Frequency/UART live setup | **offline-verified** | fixed RTL divisor 27, 115200-at-50-MHz auto scaling, explicit baud tolerance, stable port selection, and clock-before-UART order tested with fakes; not run on hardware in this update |
| Trigger-source selection | **offline-verified** | auto/core/firmware map distinctly to CFGSEL auto/core/software; the supported controller firmware does not pulse its software trigger |
| Scope board-clock selection | **offline-verified** | default Husky-HS2 holder and explicit external declaration are distinct resume identities; fake tests prove clock-before-UART ordering, lifetime and cleanup; no scope hardware used |
| Strict scope protocol integration | **offline-verified** | allowlisted models; vendor/dialect/readback gates; NORMAL one-shot trigger; trigger electrical state; stale/early/missed trigger faults; per-shot preamble/phase; size-aware transfer timeout; checkpoint state verification; cleanup/resume; still no physical-scope evidence |
| Per-row waveform integrity | **offline-verified** | nonfinite, constant, clipped and third-consecutive-identical records are rejected; 20 consecutive quality failures abort without UART reopen/controller reprogram; storage failures fail fast and cleanup preserves the original exception |
| HDF5/CSV export | **offline-verified** | valid rows only; HDF5 carries native integrity state; CSV marker binds exact basename, byte size, SHA-256 and generation |
| Discarded operation warm-up | **offline-verified** | exact successful/reference-checked operations per start/resume/recovery session; no hardware this turn |
| Balanced/interleaved TVLA + Welch analysis | **offline-verified** | exact class counts, stored group/block/time, parity warning, O(chunk) diagnostics, and completed-dataset count/block/fixed-input integrity gates |
| Automatic AES CPA | **offline-verified** | exact `done`, TVLA random-group selection, model/round mapping, blind candidate ciphertext verification, peak sample/cycle/time diagnostics; scope runs generate paired raw and `_scope_aligned` reports |
| Masked Sw-RV integration | **bounded hw + offline verified** | distinct pinned VMEM pair, RNG enable-before-boot, fresh nonzero per-operation reseed and AES reference in the 200-row live smoke; TVLA/warm-up/export tests and first-order-only CPA caveat |
| Automatic AEAD scoring | **offline-verified** | ASCON/Xoodyak model self-tests and reference ciphertext checks; positional hypotheses only, no full-key decoder; scope runs pair raw and aligned views |
| Warning-clean pytest | **257 pass** | full public hardware-free suite, including clean-clone portability, included analysis models, strict scope simulation, bounded large-record preflight and storage corruption cases |
| CLI acceptance matrix | **235/235 pass** | six targets, output formats, analysis modes, explicit scope timeout and narrow-record estimate cases |

## Not testable now (no oscilloscope connected)

- **Oscilloscope backend** (`acq/backend/scope.py`): implemented for an explicit
  Keysight InfiniiVision 3000 X-Series and Tektronix 4/5/6 MSO model allowlist using
  pyvisa-py `@py`. It applies and reads back NORMAL one-shot edge trigger type, slope,
  source, level and safe controllable trigger electrical state. Measurement-channel
  analog settings are read, stored and pinned but remain a deliberate operator choice.
  **It cannot be labelled hardware-verified until run on a real scope.**
  Scope capture now defaults to a separately held-open Husky HS2 clock connection;
  `--scope-clock-source external` records an explicit unverified declaration.
  After each shot, the backend re-reads the preamble, validates record length/sample
  interval and stores the trigger index. Native scope rows remain raw and unaligned;
  automatic CPA/TVLA also creates `_scope_aligned` derived reports and a bound
  raw-versus-aligned comparison. Scope state/error verification runs before storage,
  at chunk checkpoints and at finalization. Binary timeout is record-size-aware or set
  with `--scope-transfer-timeout-ms`. Wiring + SCPI + usage are in `SCOPE_GUIDE.md`.
  Strict simulated Keysight/Tek tests cannot validate one instrument's firmware,
  probe bandwidth or analog SNR.
- Nonzero scope offset is refused because the backend does not yet apply/query a
  horizontal delay. Keysight and Tektronix point count, sample interval and trigger
  position are required preamble readbacks; a missing or invalid value fails closed.
  The exact bench firmware response still needs physical validation.

## Remaining work

- L3.x: extend each core's matrix (random-key, larger reliability/throughput runs).
- L4: hardware-profile the new every-row output reference check and v2 sidecars;
  run one ≥100k campaign per core type.
- Scope: hardware-verify when an instrument is available.
- Extend `sw_rv_masked` beyond its bounded 200-row live smoke: verify mask variance,
  interruption/resume and sustained capture.
- A mask-aware higher-order model remains future work. Automatic masked-AES CPA is
  intentionally limited to a first-order diagnostic.

## How to use

```bash
./run.sh                                   # interactive wizard
./run.sh --target aes1 --traces 100000 --key fixed --input random
./run.sh --target aes1 --traces 100000 --clock-mhz 50 --baud auto
./run.sh --target aes1 --traces 100000 --port /dev/serial/by-id/usb-Microchip_MCP2200_...
./run.sh --target xoodyak --traces 1000000 --trigger 0x11   # narrow AEAD trigger
./run.sh --target aes2 --traces 500000 --estimate           # disk estimate only
./run.sh --target sw_rv --traces 5000000                    # resumes if interrupted
./run.sh --target sw_rv_masked --traces 100000 --warmup 1000 --format h5
./run.sh --target sw_rv_masked --traces 10000 --tvla        # 10k/class
./run.sh --target sw_rv_masked --traces 100000 --auto-cpa   # first-order diagnostic
./run.sh --target aes1 --traces 100000 --backend scope --scope-clock-source husky
./run.sh --target aes1 --traces 2 --backend scope --samples 2000 \
  --scope-resource TCPIP0::192.168.1.50::hislip0::INSTR \
  --scope-trigger-source CHANnel2 --scope-transfer-timeout-ms 12000
python -m acq.tests.test_core                  # unit tests
```
