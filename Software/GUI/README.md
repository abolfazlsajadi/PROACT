# PROACT GUI

The PyQt6 desktop interface connects the shared `proact_host` library to seven
workflows: experiments, scope setup, recorded-trace analysis, registers, memory
and Sw-RV programs, self-checks, and the UART/activity log.

This is the **1.1.0.dev1 host-software update**. Its behavior was verified with
fake devices and offscreen Qt; it does not establish new live-board results.
Matching controller/Sw-RV images and the FPGA bitstream must be supplied
separately; the public host release does not include the full design sources.

## Launch the GUI

From the repository root after [installation](../../INSTALL.md):

```bash
./run_gui.sh
```

The launcher prefers the repository's `.venv` unless `PROACT_VENV` explicitly selects
another environment. See [installation](../../INSTALL.md) for setup and the
[GUI guide](../../docs/wiki/GUI-Guide.md) for each page. Simply opening the GUI
leaves the board disconnected; device access begins only when you request it.

## What changed

- A task name and elapsed timer stay visible at the bottom of every page;
  experiments also report completed attempts against their planned count.
  A single foreground task owns the session; other actions pause while it runs.
  Tabs, help and output selection remain usable.
- Disconnect waits for the UART lock in a worker, keeping the window responsive.
  Closing an idle connected window closes its resources asynchronously; closing
  during an active task leaves the task and window running.
- Unhandled worker exceptions reach the window and activity log. Input-file
  selection and fixed-block input validation fail before a hardware job starts.
- The UART table retains the newest **5,000 messages**, indicates discarded
  history, and batches updates. Reading older rows no longer jumps to the end
  when a new message arrives. CSV exports the retained history, including queued
  messages, using UTF-8.
- The experiment display retains **5,000 lines**. **Also save log file** streams
  the complete experiment log, including communication failures, into a unique
  file under the repository's `experiments/`. Without saving, no duplicate full log is
  accumulated in memory.
- All seven pages have a descriptive heading and context badge. Plaintext and
  Associated data have readable labels; help appears beside panel headings;
  input and help controls have accessible names. Default pages fit from
  **1280×720** upward at the tested desktop sizes.
- The alternate Husky transport and capture cycle-logging controls are disabled
  because the GUI did not implement them. Decrypt is unavailable for the
  encryption-only ASCON/Xoodyak hardware. Capture timing/order and CPA algorithms
  remain unchanged; unqualified trace-count and filtering promises were removed.

Capture preflight now requires a connected scope and valid fixed inputs, sample
count, trigger and clock settings. Memory and Sw-RV operations check aligned
u32 addresses/spans without silently truncating words. A failed new-scope setup
closes the partial resource. The Output tooltip explains new `.tracepack`
directories alongside NPZ/HDF5 snapshots; it does not add CPA format support.

## Offline verification and screenshots

```bash
./tools/run_tests.sh tests/test_gui_workflows.py -W error
PYTHONPATH=Software/Python .venv/bin/python tools/check_gui_layout.py
PYTHONPATH=Software/Python .venv/bin/python tools/gen_gui_screenshots.py reports/gui_local --size 1400x900 --size 1280x800
```

These commands use Qt offscreen and fake resources. They do not enumerate or
open devices, capture traces or run recorded-trace analysis. Use a new local
output directory when generating screenshots so recorded release images remain
available for comparison.

See the [release report](../../reports/HOST_SOFTWARE_RELEASE.md),
[overview](../../reports/gui_followup_20260909/overview_1280x800.png),
[capture page](../../reports/gui_followup_20260909/default_capture_1280x800.png)
and [Review setup dialog](../../reports/gui_followup_20260909/help_capture_780x650.png).

`proact_gui.py` contains the UI and its worker coordination; `assets/` contains
SVG control icons. Keep those assets beside the module when distributing it.
