# Host software release — 10 September 2026

The 1.1.0.dev1 GUI, CLI, Python library and documentation update has been integrated into the full local PROACT checkout and prepared independently on the public repository history. No board, serial port, USB instrument or capture process was accessed during this release work.

## What changed and why

- **GUI:** a single foreground job state, batched bounded logs, responsive disconnect/close and explicit failure status. Seven pages fit common desktop windows. The capture form has an offline setup-review dialog and clearer descriptions of settings and saved data.
- **CLI:** invalid arguments and firmware files fail before opening hardware; interruptions and incomplete work have meaningful exit status; UART/programmer handles are closed on error. Empty SwRV data images remain valid while empty instruction images and malformed files are rejected.
- **Storage library:** inputs are owned instead of borrowed; variable payload lengths and format metadata are preserved; legacy snapshots are published atomically; optional tracepack chunks bound waveform buffering. See [format limitations](../docs/STORAGE.md).
- **Libraries and setup:** clearer parsing errors, exact AEAD tag-length checks, lazy imports and dedicated environments. Setup preserves existing environments and does not implicitly modify the shared bench installation.
- **Documentation:** installation, architecture, migration, illustrated workflows, generated CLI help and maintenance instructions match the host release. Required firmware, bitstreams and captured datasets are explicitly separate artifacts.

## Verification of this integration

| Check | Result |
|---|---|
| Full local design checkout, offline host suite | 1,537 passed; 1 skipped |
| Public host-only checkout, offline host suite | 1,495 passed; 43 skipped |
| Public-only skip difference | 41 firmware/header cross-checks and 1 generated-VMEM fixture are absent; all host checks remain enabled |
| Common skip | HDF5 is installed, so the test specific to its absence is skipped; explicit fallback regressions also use mocks |
| Qt layout | Seven pages at five sizes: 35 page/size checks passed, plus sidebar/tab-bar checks |
| GUI illustrations | Re-rendered with fake/disconnected handles and a generic example path; no connected-device claim |
| CLI help and diagnostics | 25-command reference regenerated without dispatch; doctor located the checked-out public source without enumerating devices |
| Offline acquisition UI and installer | 73 passed; responsive 80-column display, read-only checks and no-driver guards |
| Targeted compatibility review | Empty-DMEM and parser/cleanup regression set: 181 passed |
| Public portability review | 157 register checks in the full tree; 116 passed and 41 explicit skips in a host-only fixture; 9 benchmark admission checks passed |

Both complete host suites used warnings as errors, Python 3.10.12, NumPy 1.26.4, HDF5 bindings 3.16.0 and PyQt6 6.11.0. The existing development interpreter was used read-only while each checkout supplied its own source through PYTHONPATH. No packages were installed into the bench environment. The initial full-suite attempt had a missing parent directory for the test scratch area; it did not constitute an implementation failure. The corrected run above passed.

The offline UI suite used an existing interpreter read-only with Click 8.1.7, Rich 15.0.0 and Questionary 2.1.1. Its two real-symlink checks ran on a symlink-capable temporary filesystem; those checks explicitly skip when the chosen scratch volume cannot create symlinks. No UI dependencies were installed into the bench environment.

The GitHub workflow additionally targets Python 3.9 and 3.12. Local execution here does not establish a completed remote CI run on those versions.

## Historical development timings

The following figure and its input JSON preserve earlier synthetic/fake-resource measurements. They were **not rerun as release performance measurements**. The development baseline is separately retained and is not part of the public repository history. Later correctness fixes mean these figures must not be described as exact current-release speedups.

![Historical synthetic host timings, not live acquisition throughput](software_performance.png)

The saved [startup](startup_benchmark.json), [storage](storage_benchmark.json) and [GUI](gui_responsiveness.json) measurements retain conditions and individual trials; machine paths have been removed. Basic import, GUI callback latency and checkpoint writing measure different operations. Atomic legacy snapshots can be slower. The GUI experiment uses a fake lock held for 150 ms; it does not measure device disconnect latency. Benchmark runners now require an explicit locally available `--baseline COMMIT` instead of silently selecting an unrelated root commit.

## Acquisition folder scope

The new [Acquisition folder](../Acquisition/README.md) is an offline configuration/review interface and a labelled synthetic progress demonstration. It asks for frequency first (50 MHz default), calculates requested host UART baud from the declared firmware divisor, records trigger/scope requests and can export a new JSON file. It does not apply those settings or capture waveforms.

Static review of the older separate acquisition package found potential false-completion handling, physical-voltage clipping, incomplete resume checks and cleanup gaps. These findings are not demonstrated causes of the ASIC trace-count difference. Live-driver integration was stopped after automatic cybersecurity screening rejected that task. The incomplete acquisition changes remain outside the published package; the original acquisition directory is unchanged. Exact oscilloscope model information and instrument validation remain outstanding.

## Evidence limits

This software release establishes offline host behavior only. It does not prove lower measurement noise, repaired acquisition drivers, reduced ASIC recovery trace counts, a PCB improvement, or new CPA/TVLA/ML results. Existing captures and research evidence were not modified or reprocessed. Full private design history, firmware images and datasets were not transferred into the public release.
