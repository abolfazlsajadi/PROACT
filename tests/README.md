# Offline software verification

Run from the repository root:

```bash
./tools/run_tests.sh -W error
./tools/run_tests.sh -k aead
./tools/run_tests.sh tests/test_transport.py::test_select_key_run_is_the_documented_byte_stream
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/check_gui_layout.py
```

The development checkpoint before public integration passed **1,522 tests with 2 skips** under Linux/Python 3.10 with warnings treated as errors. The public host tree excludes full design sources and generated firmware images, so availability-dependent checks can differ. Current integrated totals, skip reasons and verification evidence are recorded in the [release report](../reports/HOST_SOFTWARE_RELEASE.md). Counts and runtimes are measured results, not fixed promises.

## Environment and scope

Use `bash tools/setup_env.sh --with dev` for the offline suite, including Qt and HDF5. Tests import `Software/Python` from this checkout. Launchers honor explicit `PROACT_PYTHON` or `PROACT_VENV`, then prefer the repository's `.venv`; broken explicit overrides fail rather than silently selecting another environment. Paths passed to the test launcher replace pytest's default `tests/` selection.

No test requires a board, serial port, HID device or oscilloscope. Hardware-facing regression tests replace transports, locks, scope objects and driver imports with fakes or fail-fast sentinels. The GUI renders with Qt offscreen. Temporary-file and child-process tests intentionally exercise filesystem failure and process interruption. Some GUI tests process events and use short bounded waits; randomness tests and synthetic benchmarks are explicitly separate from live measurements. This suite does use subprocesses and temporary files.

| Coverage | Files |
|---|---|
| Protocol bytes, reply framing and register/firmware definitions | `test_transport.py`, `test_regs_map.py` |
| Reference outputs, authentication, input generation and file parsing | `test_aead_soft.py`, `test_validation.py`, `test_inputs.py`, `test_host_boundaries.py` |
| CLI parsing, useful errors, exit status and cleanup | `test_cli_parser.py`, `test_cli_updates.py` |
| VMEM comments, width/address checks and programmer lifetime | `test_vmem.py`, `test_programmer.py`, boundary tests |
| Failed preparation and explicit functional-only mode | `test_experiment_errors.py` |
| Buffer ownership, snapshot interruption, chunk consistency and legacy data | `test_storage.py`, `test_storage_integrity.py`, `test_storage_chunked.py` |
| GUI concurrency, logging, preflight and resource ownership | `test_gui_workflows.py` |
| Independent review reproductions for input and UART edge cases | `test_review_regressions.py` |
| Environment setup, launch path selection and offline diagnostics | `test_workspace_tools.py` |

The development checkpoint's two skips covered an older test conditioned on h5py being absent (a monkeypatched regression also covers that fallback) and a check requiring a generated controller VMEM. Public-release checks that need unavailable design artifacts must report that dependency explicitly. An absent artifact is not a successful design check. Earlier ASCON-padding/register-map fixes predate this update.

## Performance evidence

Benchmarks are explicit workflows, not repeated by the normal test suite. Development before/after measurements used a separately preserved baseline with synthetic data or fake resources; that private baseline commit is not part of the public Git history. Inspect each benchmark's supported arguments and provide an available comparison baseline before attempting to reproduce a comparison. Storage timings include checkpoint writes and fsync. The GUI benchmark measures the callback returning to Qt, not communication speed. See the [release report](../reports/HOST_SOFTWARE_RELEASE.md) for which evidence remains applicable to the integrated source.

## What passing tests do not establish

The suite does not certify electrical connections, clock or voltage settings, FPGA programming, physical resource release, acquired waveforms, analog noise, or CPA/TVLA outcomes. The GUI's **Self-Check (A–Z)** and hardware CLI checks are different operations that communicate with a board; they were not run for this software update.

For a new regression, assert an externally observable contract or reproduce a concrete failure. Use `tmp_path` and fake devices, preserve the protocol's exact bytes, and keep tests independent of current bench state. Do not weaken checks to hide a failure.
