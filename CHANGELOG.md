# Changelog

This file records all notable changes to the PROACT software platform. The
hardware is frozen and is never modified.

## [1.13.0] — 2026-08-21

### Changed — the fabricated ASIC is screened and passing
- **The A-Z self-check now runs on a fabricated PROACT die and passes in full:
  16 pass / 0 fail / 0 skip**, driven from the GUI on a chip in the CW308 target
  board with a ChipWhisperer Husky attached — the same sweep, over the same UART,
  that screens the CW305 FPGA build. Documentation that described the silicon as
  "not tested" or "not screened" has been updated across `README.md`, the wiki pages
  (Home, Getting-Started, Testing, Troubleshooting, GUI-Guide, Python-API,
  Controller-Firmware, Target-Software, Address-and-Register-Map),
  `FPGA/README.md` and `Software/Python/README.md`.
- **What the 16 passing steps cover on silicon:** UART link and baud integrity;
  AES1 and AES2 encrypt known-answer plus decrypt round-trip; ASCON and Xoodyak
  on-chip encrypt KAT plus host-side software decrypt; timer; control register;
  PRNG; Sw-RV software AES; ChipWhisperer clock lock; and a real trace capture on
  the chip. Beyond the self-check, **sustained unattended trace-capture campaigns
  run for hours with no failed captures**, from the GUI and from the CLI alike.
- The CW305 FPGA build reports the same 16 pass / 0 fail / 0 skip (2026-08-07),
  so silicon and FPGA now agree step for step. The A-Z check remains the
  screening procedure for further chips.
- Windows and macOS remain untested.

## [1.12.0] — 2026-08-07

### Changed — GUI layout: one page per workflow step, no scrolling
- **The GUI no longer scrolls on a normal desktop window.** Measured before:
  the Registers tab overflowed its viewport by 539 px and the ChipWhisperer tab
  by 286 px at a 1240x900 window. Measured after: **0 px of overflow at
  1280x720, 1366x768, 1600x900 and 1920x1040**.
- **Seven pages instead of five**, each holding one workflow step: Crypto
  experiment · ChipWhisperer · **CPA analysis** (new) · Registers ·
  **Memory / Sw-RV** (new) · Self-Check (A–Z) · UART monitor. The CPA panel
  moved off the ChipWhisperer tab (its recovered-key console now fills the
  page); raw bus access and the Sw-RV loader moved off the Registers tab.
- **Sidebar**: *Reset control* is now a collapsible panel that starts collapsed
  — it is a bring-up tool, not a per-run control — so Connection and
  Programming are always visible without scrolling. Collapsing is purely
  visual: line state and the once-per-second read-back poll keep running.
- **Status register** is now an 8x4 bit grid (was 16x2), and the ChipWhisperer
  tab's self-check group box is a single compact row.
- **The window opens larger and centred**: `min(1500, screen-60) x
  min(1000, screen-60)`.
- The layout change is placement-only — no control, field or behaviour moved in
  or out. Verified offscreen: every documented widget and every `on_*` handler
  still constructs and runs, and all seven pages render without scrolling. (The
  broader release *does* change some host behaviour — see **Fixed** below.)

### Added
- **`tools/gen_gui_screenshots.py`** regenerates the `docs/images/gui_*.png`
  screenshots offscreen (no board or scope needed) — now seven pages at
  1400x900, matched by tab title rather than index, and it fails if the GUI
  grows an undocumented tab. Run it whenever the GUI's appearance changes, then
  rebuild the manual and the wiki (`tools/publish_wiki.py --build`).
- **`tools/check_gui_layout.py`** reports per-page vertical overflow at four
  screen sizes and exits non-zero if any page would need scrolling — a
  regression guard for the layout above.
- **`tests/` — an offline regression suite** (~1240 tests: 1224 pass, 14
  documented-bug xfails, 1 skip; ~2 s, no board, no network) covering
  `aead_soft`, `validation`, `inputs`, `monitor`, `vmem`, `storage`, `regs` +
  `config/hardware.json` consistency, `transport` framing and the CLI parser.
  Run it with **`tools/run_tests.sh`**; configuration in `pytest.ini`, scope
  and limits documented in `tests/README.md`. Writing the suite surfaced 12
  real bugs (see **Fixed**); the 14 remaining xfails pin the 2 still open
  (ASCON block-aligned decrypt, and the `config/hardware.json` command-byte
  gap), and `xfail_strict` turns each into a hard failure the moment it is
  fixed.

### Fixed — bugs found by the new test suite and by hardware bring-up
- **cli**: board commands printed a raw traceback when the controller did not
  answer; they now print a short diagnostic with hints (`PROACT_DEBUG=1` keeps
  the traceback). `cli.main()` discarded the handler return value, so
  `proact cpa` always exited 0 even on failure. `cpa` was missing from the
  `--help` banner. `_hints()` now covers a missing driver package.
- **validation**: `validate_aes(decrypt=True)` raised `IndexError` on a
  truncated read-back, and a short key still raised; both now return `False`.
  `validate_aead` used `ad or bytes(16)`, silently replacing an explicitly
  empty AD with zeros (a fake FAIL); it now honours empty AD.
- **storage**: zero-length `expected` entries were dropped, shifting `expected`
  rows out of alignment with their traces; `--output run.h5` without h5py wrote
  `run.h5.npz` but returned `run.h5`. Both fixed.
- **monitor**: `safe_text` claimed to be lossless while emitting backslash raw.
- **GUI**: **"Read status register" failed on a freshly programmed chip.** The
  controller's send-back (frame) mode is CPU state cleared by every reboot, and
  the GUI enabled it nowhere; it is now re-asserted at Connect, after Program,
  after Restart ctrl, and after any reset action that re-runs the CPU.
- **capture**: `program_fpga` passed `force=False` to ChipWhisperer, which
  **skips the FPGA upload whenever the fabric already holds any bitstream**
  while still reporting success — so the board silently kept a previous
  bitstream. `force` now defaults to True and is exposed on `connect()`.

### Hardware validation (2026-08-07, CW305 FPGA build, Linux)
- Driven through the GUI's own slots against a real ChipWhisperer CW305 with a
  Husky: bitstream uploaded (PLL 50 MHz, locked), controller firmware
  programmed to 100%, status register `0x00001000`, five `aes1` encryptions all
  `69c4e0d86a7b0430d8cdb78070b4c55a` (the FIPS-197 KAT), and the full
  **Self-Check (A–Z) ALL PASS — 16 pass, 0 fail, 0 skip**, including a real
  Husky trace capture. The send-back and force-upload fixes above were verified
  on the board. **The fabricated ASIC itself was not tested; only the CW305
  FPGA build, on Linux.**

### Changed — GUI visual refresh (presentation only, no functional changes)
- **Modernized the GUI theme** (`Software/GUI/proact_gui.py`): layered dark
  palette with panels as rounded cards, a PROACT header in the sidebar, gradient
  primary buttons, accent-underlined tabs, restyled tables/menus/tooltips/
  scrollbars, console-style output boxes, and a status chip in the sidebar.
  The app now uses the Fusion style plus a matching dark `QPalette`, so
  check/radio indicators, combo popups and spin arrows render consistently on
  every platform.
- **New `Software/GUI/assets/`**: four small SVG icons (check mark, radio dot,
  up/down chevrons) referenced by the stylesheet, so checkbox/radio/combo/spin
  indicators stay clearly visible on the dark theme.
- **Visual fixes**: the 12 px reset-line LEDs no longer render as squares (the
  border radius now follows the dot size); the bare LED dots no longer stretch
  their sidebar rows; the CPA capture-path field now gets the row's width
  instead of the Browse button. Console/register monospace is enforced in the
  stylesheet (`#console`/`#monoValue`/`#monoSmall` rules), since the theme's
  global font-family would otherwise override the programmatic `setFont`
  calls. If the Qt SVG image plugin is missing (system-Python installs), the
  GUI now prints a one-line warning instead of silently dropping the icons.
- **Docs refreshed to match**: the `docs/images/gui_*.png` screenshots
  regenerated (now seven, one per page), GUI wording updated in the wiki GUI Guide and
  `Software/GUI/README.md`, the manual rebuilt with the new screenshots and its
  cover matched to the new window color. Also fixed while reviewing: the GUI
  README's reset-control bullet now lists the five real presets (the old
  None/Controller/Global/SPI names never existed), the wiki names the scope
  panel by its real title and documents the previously missing
  "CPA attack (offline)" panel, and the AEAD-decrypt wording is harmonized
  everywhere to the accurate "ends at the firmware's bounded timeout and
  returns zeros". Behavior, commands, the `proact_host` backend and all
  hardware/crypto code paths are unchanged.

## [1.11.0] — 2026-08-03

### Added
- **Photographs of the fabricated chip** (`docs/images/asic_die.jpg`,
  `asic_package.jpg`, `asic_package_top.jpg`): the PROACT die with its bond wires
  and the open-cavity ceramic package that keeps the die reachable for
  side-channel measurement. Shown in the README, the wiki Home page and
  Hardware-Overview. Re-encoded for the web (~1 MB total) with all EXIF removed.
- `tools/publish_wiki.py` now also collects and rewrites images referenced by HTML
  `<img src="...">`, not only by markdown `![](...)`. Side-by-side photo layouts
  need HTML, and those images were previously not copied to the wiki (external
  http/https/data sources are left untouched).
- The wiki Home page leads with the three-core CPA comparison and the measured
  trace counts instead of the single AES-1 correlation plot.

## [1.10.0] — 2026-07-31

### Added — reference traces, per-core comparison, offline CPA in CLI and GUI
- **`datasets/`** ships real CW305 captures so the attacks reproduce **without a
  board**: `aes1_reference.npz` (4800 traces), `aes2_reference.npz` (5300),
  `swrv_reference.npz` (1400) — 9 MB total, `int16` (lossless for the 12-bit ADC,
  and CPA is scale-invariant). Each recovers **16/16 key bytes**.
- **`./run_cli.sh cpa`** runs the attack on a capture, defaulting to the shipped
  dataset for the chosen core: `--core aes1|aes2|swrv`, `--capture`, `--filter`,
  `--window`, `--plot`. Works with no hardware attached.
- **GUI: a "CPA attack (offline)" panel** on the ChipWhisperer tab — pick a
  capture (or use the reference dataset), the model and the filter width, and the
  recovered key is printed in place.
- **`--filter auto` is now the default for `cpa_lastround.py`**: the width is
  chosen from the data by maximising how far the best key guess stands out from
  the other 255 (a scale-free z-score), so no key is needed. It picks MA4 for
  AES1 and MA2 for AES2, both 16/16. A fixed default could not serve both.
- **Four comparison figures** regenerated by `tools/gen_cpa_figures.py`:
  `cpa_core_comparison.png` (leak point and rho per core),
  `cpa_traces_needed.png` (bytes recovered vs traces, filtered vs not),
  `cpa_filter_effect.png` (why the width must match the leak),
  `cpa_key_recovery_full.png` (16/16 on all three cores). The README and wiki now
  lead with these instead of the old 12/16 figure.
- **Per-core comparison table** (model, POI, rho, filter width, traces) on the
  ChipWhisperer page, plus per-width trace counts: AES1 is best at MA8 (~3600
  traces), AES2 at MA2 (~4700), Sw-RV at MA16 (~1300). These thresholds are
  approximate -- recovery is not strictly monotonic in the trace count near the
  boundary, so a byte can flip back for a few hundred traces.

### Fixed
- `cmd_cpa` called a non-existent `err()` helper on the missing-file path
  (would have raised `NameError`); it uses `bad()` like the rest of the CLI.

## [1.9.0] — 2026-07-31

### Added — low-pass filtering on ALL cores; full key verified on AES1, AES2 and Sw-RV
- **`cpa_lastround.py` also filters now** (`--filter K`, default 4, `1` disables).
  The leakage of one operation is smeared over about as many samples as the
  operation takes, so averaging over the leak width raises rho; the width must
  MATCH the leak, since too wide smears neighbouring intermediates together.
- **Bench-measured traces for the full 16/16 key** (CW305, fixed key / random
  plaintext), all re-verified end-to-end with the shipped scripts:

  | Core | Leak | Best filter | Unfiltered | Filtered |
  |---|---|---|---|---|
  | AES1 (hardware) | last round, ~1 clock edge | MA 8 | ~11 500 | **~3 600** |
  | AES2 (hardware) | last round, narrower | MA 2 | ~6 500 | **~4 700** |
  | Sw-RV (software) | 1st S-box, ~4 cycles | MA 16 | not reached at 6 000 (15/16) | **~1 300** |

  The hardware cores finish a round in about one clock edge and want a narrow
  filter (MA 16 *hurts* AES2: 10/16 at 5 000); the software AES spends several
  cycles per state byte and wants a wide one.
- **The repository's own 5000-trace AES1 capture now yields 16/16** with the
  default filter, where it previously recovered 12/16 — the README, Home and
  ChipWhisperer pages are corrected accordingly (the 12/16 figure is retained as
  the unfiltered baseline).
- The trace-count table and the noise-floor reasoning
  (`floor ~ sqrt(2*ln(S)/n)`, traces ~ 1/rho^2) are documented on the
  ChipWhisperer wiki page, and surfaced in the CLI `--traces` help and the GUI
  capture tooltip.

### Added — Sw-RV CPA needs ~10x fewer traces (low-pass filtering)
- **`cpa_swrv.py` now low-pass filters the traces** (16-sample moving average,
  `--filter K`, `--filter 1` disables). The Sw-RV S-box leak spans several clock
  cycles, so at `adc_mul = 4` most samples in it are noise; averaging over the
  leak width recovers the energy and **doubles the correlation, rho 0.10 -> 0.22**.
  Measured effect on full-key recovery: unfiltered reached only **15/16 even at
  6000 traces**, filtered reaches **16/16 at ~1300** (250 -> 7/16, 500 -> 13/16,
  1000 -> 15/16, **1312 -> 16/16**). Widths 1/8/12/16/20/24 were compared; 16
  (~4 target cycles) was best.

### Measured (Sw-RV first-S-box capture, CW305 bench)
Bench characterisation of the Sw-RV first-round capture, now documented in
`examples/cpa_swrv.py`, the ChipWhisperer wiki page and the GUI tooltips:
- **Trigger window = 378 target cycles = 1512 ADC samples** at `adc_mul = 4`;
  the 16 byte leaks are spaced ~94 samples apart across it. Captures are now
  sized to exactly this window, so a trace holds only the first S-box.
- **Gain: low/20 dB is correct for Sw-RV** — 86% of ADC full scale with 0%
  clipping, versus 26% at low/10 dB (range wasted), 5.7% clipping at high/25 dB
  and 50% at high/33 dB. Confirms the `RECOMMENDED_GAIN` value added in 1.7.0.
  Gain fixes range usage but barely moves rho: the noise scales with the signal.
- **Keep `adc_mul = 4`** — synchronous 1 sample/cycle measured *worse*
  (rho ~= 0.05 versus ~= 0.10).
- **Use the S-box model.** `HW(pt^k)` has a higher raw rho (~0.17) but is linear
  in the key, so wrong guesses score nearly as high and the attack stalls (10/16
  at 2000 traces); the nonlinear S-box model separates the key cleanly.
- **Trace count follows from rho against the correlation noise floor**
  ~ `sqrt(2*ln(S)/n)`: for S = 1512 the floor is 0.24 at n = 250 and 0.10 at
  n = 1500. At the bench's rho ~= 0.10 the full key needs a few thousand traces;
  rho ~= 0.25 would recover it in ~250. Documented as a table so a slow attack is
  diagnosed by comparing rho, not by capturing more traces.

### Fixed
- `PROACTExperiment` health-checked the link with `read_status()`, which cannot
  work: this firmware answers `CMD_RDSTAT` with ASCII text while `read_frame()`
  waits for a binary `0xA5` frame, so it always raised `no frame marker`. Bring-up
  scripts now probe with `run_and_read()`. Also note `program()` alone does not
  boot the controller — `restart_controller()` is required after it.

## [1.7.0] — 2026-07-31

### Fixed
- **`PROACTExperiment` dropped `platform` when connecting the scope** — the scope
  was constructed as `ChipWhispererCapture(samples=...)` with no `platform`, so it
  fell back to `"asic"` and drove its own HS2 clock. Every CLI capture run with
  `--platform fpga` therefore sampled *unlocked* from the CW305's 50 MHz target
  clock instead of `extclk`. `platform`, `clock_hz`, `bitstream` and the gain are
  now forwarded. (The GUI already passed `platform` and was unaffected.)

### Added (capture quality)
- **Per-core ADC gain** (`capture.RECOMMENDED_GAIN`): the hardware AES cores keep
  the modest low/10 dB (their last round leaks in the clipping-prone leading
  samples), while **Sw-RV now uses 20 dB** — at 10 dB a bench Sw-RV capture used
  only 28% of ADC full scale with 0% clipping, giving rho ~= 0.07 and needing
  >10k traces; traces required scale as ~1/rho^2. Applied by the CLI and GUI.
  `gain_mode` is now a parameter (it was hardcoded to `"low"`).
- **`samples` auto-sized to the trigger window.** The on-chip timer counts the
  trigger-high cycles, and the scope digitizes `adc_mul` samples per cycle, so a
  window of N cycles needs N*adc_mul samples. `PROACTExperiment.capture()` now
  measures the window with one throw-away run and grows `samples` to fit
  (`--no-auto-samples` opts out). Too-small settings silently truncate the trace:
  a 773-cycle window at adc_mul=4 needs 3092 samples, so a 1200-sample capture
  keeps only the first 300 cycles (39%) and the leakage past the cut — for
  byte-serial software AES, most key bytes — is unrecoverable at any trace count.
- `ChipWhispererCapture.trace_quality()` reports clipping and ADC-range usage, and
  new CLI options `--gain`, `--gain-mode`, `--clock`, `--bitstream`,
  `--no-auto-samples`.
- **The controller now goes quiet while the Sw-RV target is measured.**
  `swrv_aes_block()` used to poll `MBOX_DONE_ADDR` in a tight loop, but that word
  lives in `RII_data_mem` — the target's *own* data memory, in use for the AES
  state. Every poll therefore added controller switching activity on the same
  supply *and* contended for the target's memory, delaying its loads/stores and
  jittering execution between traces (jitter no amount of averaging removes).
  The controller now spins in registers only (`swrv_quiet_wait()`, no bus
  transactions — confirmed in the disassembly) across the measured region and
  polls afterwards, when the trace is already captured. Tune with
  `SWRV_QUIET_ITERS` if the measured region grows.

## [1.6.0] — 2026-07-31

### Added (Sw-RV software-AES CPA)
- **`examples/cpa_swrv.py`** — first-round CPA for the Sw-RV target's *software*
  AES. Software AES leaks the first-round S-box output `HW(SBox(pt ^ key))`
  (plaintext-based, recovers the key directly), not the hardware last-round
  ciphertext model, so `cpa_lastround.py` does not apply. Byte-serial: each key
  byte leaks at its own sample, so the auto-window spans all 16 peaks. Verified
  live on the CW305: **16/16 key bytes at 15 000 traces**.
- **Sw-RV trigger fenced around the first S-box** (`SWRV_FENCE_FIRST_SBOX` in
  `Software/SW_RV/`): the target now brackets the capture trigger tightly around
  only the first-round `SubBytes` instead of the whole 10-round encryption, so the
  trace contains just the leaky operation. With the old whole-encryption trigger
  CPA stalled at ~2/16; with the fence it reaches 16/16. `sw_rv_imem.vmem` rebuilt.
- **GUI capture tab loads the Sw-RV program automatically** when the core is
  `swrv` (previously the target had to be loaded by hand from the Registers tab
  before a Sw-RV capture would produce valid traces).
- Wiki (ChipWhisperer) documents the Sw-RV first-round attack, the model
  difference, and the trigger fence.

## [1.5.0] — 2026-07-31

### Fixed (CPA on AES1/AES2 captures)
- **`examples/cpa_lastround.py` now auto-detects the last-round leakage window**
  (`--window auto`, the new default). The last round leaks in a narrow slice of
  the trace (AES1 peaks near sample 57, AES2 near 64); correlating over the whole
  capture buries it and silently drops key bytes. Verified live on the CW305 with
  captures taken through this repository's own host path: full window recovered
  **6/16**, auto window recovered **16/16** (AES1, 20k traces) and **16/16**
  (AES2, 15k traces). Root cause of "GUI captures do not break": a mis-set
  correlation window and too few traces — not a capture-quality problem. The old
  `experiments/capture.npz` that failed was a stale early-bring-up file.
- **udev rules tag the MCP2200 and NewAE serial devices with
  `ID_MM_DEVICE_IGNORE`.** On Linux the MCP2200 re-enumerates on every
  (re)program; ModemManager then probes the fresh `/dev/ttyACM*` for ~15–20 s and
  eats the controller's replies (`no frame marker`). This keeps ModemManager off
  the bench UART.

### Added
- GUI capture-tab tooltips (trace-count guidance for a full-key CPA; sample-count
  note). Wiki (ChipWhisperer, Troubleshooting) and the bring-up guide document the
  window/trace-count levers, the per-core note (AES1/AES2 share the model; ASCON/
  Xoodyak need an AEAD model), and the ModemManager gotcha.

## [1.4.0] — 2026-07-23

### Changed (documentation register + repository scope)
- All documentation — the root README, every directory README, the `docs/`
  guides, the 13 wiki pages, the LaTeX manual and the tutorial notebook — revised
  to a formal academic-technical register, removing first-person and
  conversational phrasing while preserving all technical content.
- Root README rebuilt as a graphical landing page: badge row, an introduction to
  the PROACT research programme (NWO; project-proact.nl), and the embedded system
  architecture, last-round CPA, GUI and AEAD figures.

### Removed (distribution hygiene)
- The foundry SRAM memory models under `ASIC/rtl/mems/` are licensed under NDA
  and are no longer distributed; only the project-designed wrapper modules remain.
  `FPGA/create_project.tcl` skips non-distributed sources, so the FPGA
  configuration still builds from the BRAM-based wrapper.
- Retired student and development material (`legacy/`), the internal discovery
  report, the `hardware-reference/` placeholder and stale RTL variants absent from
  the sign-off compile list are no longer tracked.

## [1.3.0] — 2026-07-22

### Added (graphical wiki + real CPA)
- **Wiki redesigned to be graphical.** 13 new figures generated from a cohesive
  design system: a hero banner, a colored SoC architecture block diagram,
  control/status register bit-field diagrams, the trigger-mux routing, a reset-
  preset state matrix, the AEAD hardware-encrypt→software-decrypt flow, and six
  real-data figures. Every page now uses GitHub alert callouts and styled
  Mermaid diagrams (flowcharts, decision trees, the command-server loop).
- **A real last-round CPA on the CW305.** Captured 5000 fixed-key/random-plaintext
  AES-1 traces and recovered **12/16** round-10 key bytes with the last-round
  Hamming-distance model. Figures (trace overlay, heatmap, leakage point,
  correlation peak, convergence, key-recovery matrix) and a runnable
  walk-through on the **ChipWhisperer** wiki page.
- `examples/cpa_lastround.py` — a dependency-light, runnable last-round CPA that
  consumes a `proact capture` `.npz`/`.h5` and recovers the AES key (`--plot`).

### Fixed (capture)
- **`ChipWhispererCapture` no longer clips the last-round leakage.** The ADC gain
  default was high enough to clip the first ~100 samples — exactly where the last
  AES round leaks — silently defeating CPA. It now defaults to **low mode / 10 dB**
  (bench-verified `clip% = 0.00` over 5000 traces) and is configurable via
  `gain_db=`.

## [1.2.0] — 2026-07-22

### Added (CLI overhaul)
- The `proact` CLI (`./run_cli.sh`) now produces colored output (terminal
  auto-detection; `--no-color` / `NO_COLOR=1` to disable) and expanded from 10
  to 22 subcommands:
  `status` (decoded status bits, `--watch`), `timer`, `version`, `aead-kat`,
  `decrypt-soft` (software AEAD decrypt + tag verify, with `--selftest`),
  `seed`, `reset` (safe presets + line read-back), `restart`, `peek`/`poke`
  (raw bus access), `selfcheck` (the unified A–Z check with `--capture`,
  `--log`, proper exit code), and `monitor` (noise-safe UART dump).
- `run` gained `--runs/--random/--compare/--timer/--trig/--inttrig/--ad/--json`
  — repeatable, validating, scriptable crypto runs. `capture` gained
  `--key/--samples`. Failing checks return nonzero exit codes for scripting.
- Wiki expanded readthedocs-style (full CLI reference, complete Python and C
  API references with examples, real trace image, Ibex docs links) and
  republished.

## [1.1.3] — 2026-07-22

### Fixed (reset control — hardware-verified)
- **"Global reset" left the chip in a state unrecoverable by software.**
  Asserting the crypto/target reset while the controller CPU kept running made
  the CPU's next crypto access hang the bus (no acknowledge) — recovery required
  reprogramming the FPGA. Every preset now sets a complete, deterministic 4-line state in a
  safe order: global-affecting resets hold the CPU first, and **Run** releases
  the crypto before rebooting the CPU last. Verified on the CW305: every preset
  → Run now recovers fully (AES KAT + Sw-RV).
- **"None (all active)" did not match the real running state** (it released the
  SPI loader and chip-select). Renamed to **Run (return to running)** and it now
  drives the exact verified running state: controller=on, crypto=on, SPI loader
  held, CS idle. "Default (programming)" renamed to **Reset all (baseline)** to
  reflect its function.
- **Per-line checkboxes contradicted their own LEDs**: they were hard-coded as
  checked and never synchronized. They now initialize from the actual read-back
  pins on Connect and re-synchronize every second, and toggling a reset line
  routes through
  the safe preset sequences (spi_select still toggles directly).
- `restart_controller()` now restores the full running state including
  `spi_select`.
- **Crash on hosts with certain HID devices**: device detection used an
  unfiltered `hid.enumerate()`, which can crash inside hidapi while walking
  unrelated HID hardware. Both detectors now enumerate only the Microchip
  VID/PID (bench-observed segfault, fixed).

## [1.1.2] — 2026-07-22

### Fixed (Sw-RV load & run through the GUI)
- **Sw-RV load failed with "No such file or directory"** when the GUI/CLI was
  launched from anywhere other than the repo root: the `.vmem` paths were
  relative to the current directory. The Sw-RV loader now resolves paths against
  the repo root (defaults are absolute), checks the files exist with a clear
  "build it with `make -C Software/SW_RV`" message, and `run_gui.sh` now `cd`s to
  the repo root so all relative paths resolve.
- **A reloaded Sw-RV program did not take effect.** The controller enabled the
  target *before* its memory was loaded, and re-selecting a running target does
  not reboot it, so it kept executing the previously-booted code. Now:
  - firmware `CMD_LDI` holds the target in reset while its memory is written;
  - the host `ProactTarget.load_swrv_program(imem, dmem, base)` loads instruction
    then data memory and *then* releases the target — the clean enable edge boots
    the freshly-loaded program.
  Verified on hardware: after loading program B, the target computes B (not the
  stale A). **Rebuild the controller firmware (`make -C Software/Controller`) and
  re-program it to apply this fix.**

## [1.1.1] — 2026-07-22

### Fixed (GUI robustness — from a full thread-safety review)
- **Crash on Export CSV** in the Self-Check tab (`_set_status` was called with
  one argument): exporting an empty *or* a populated self-check table raised
  `TypeError`. Fixed both call sites.
- **Crash on scope Disconnect**: the worker thread dereferenced `self.scope`
  after the main thread had already set it to `None`; the object is now captured
  before nulling so that the disconnect completes.
- **Cross-thread Qt widget access**: several handlers (Connect, FPGA program,
  scope connect, Run experiment, Capture, Send byte) read widget values on their
  worker threads. All widget reads now happen on the GUI thread and only plain
  values cross into the worker.
- **Port teardown race**: Disconnect now takes the UART transaction lock before
  closing the port and nulling the target, so it cannot close under a running
  experiment/capture.
- **Capture reconfiguration race**: the whole capture run (setup + every trace)
  now holds the transaction lock, so nothing can change the core/key/decrypt
  midway; the monitor's raw *Send byte* also holds the lock.

### Added
- **`tools/publish_wiki.py`**: builds the GitHub project wiki from `docs/wiki/`
  (rewrites cross-links and embeds the screenshots) and pushes it.
- GUI refinements: the window title shows the connected port; Enter submits in
  the raw address and send-byte fields.

## [1.1.0] — 2026-07-21

### Added
- **Software AEAD decrypt** (`proact_host/aead_soft.py`): pure-Python,
  dependency-free, bit-exact ASCON-128 v1.2 and Xoodyak v2 (NIST LWC final round,
  matching the GMU cores in silicon), validated against the exact reference
  vectors the silicon reproduces. API: `ascon128_encrypt/decrypt`,
  `xoodyak_encrypt/decrypt`, `words_to_bytes`/`bytes_to_words`, `selftest()`;
  decrypt returns `None` on a wrong tag. This realizes the platform's AEAD
  design of hardware encryption with software decryption on the host (the frozen
  wrapper drops decrypt plaintext and has no path to receive a tag, so hardware
  decrypt times out and returns zeros): hardware encrypt → software decrypt +
  tag verify. Not constant-time — intended for validation and experiments, not
  production keys.
- **On-chip AEAD encrypt KAT**: firmware `proact_aead_kat.c` runs the reference
  vectors on ASCON + Xoodyak; host command `CMD_AEADKAT` (0x1A) and Python
  `ProactTarget.aead_kat()` → `(xoodyak_ok, ascon_ok)`. Both PASS on hardware.
- **Tutorial notebook** `examples/PROACT_Tutorial.ipynb`: section-by-section
  runnable guide to the Python and C libraries (connect + program, register
  access, AES1/AES2 encrypt/decrypt, AEAD hardware encrypt + software decrypt,
  PRNG, Sw-RV loading, ChipWhisperer capture, the full A–Z self-check).
- **Hardware sources** now in the repo: `ASIC/rtl/` (the frozen chip RTL, from
  the sign-off source list), `ASIC/tb/PROACT_Top_tb.sv` (full-chip testbench),
  `FPGA/create_project.tcl` + `FPGA/build.tcl` (Vivado scripts that build the
  CW305 bitstream from the same RTL, validated with Vivado 2022.2), and
  `FPGA/constraints/`. Third-party cores (Ibex, AES1/AES2, ASCON, Xoodyak, ARM
  UART) attributed in `THIRD_PARTY_NOTICES.md`.

### Changed
- **One unified self-check**: `proact_host/fullcheck.py` `run_full_check()` is
  the single A–Z check (UART link + baud, AES1/AES2 encrypt KAT + decrypt round-trip,
  ASCON/Xoodyak on-chip encrypt KAT + software decrypt round-trip, timer,
  control write, PRNG, Sw-RV software AES, optional scope clock lock + trace
  capture). The GUI's ChipWhisperer tab no longer has its own separate "Overall
  self-check" — one button jumps to the Self-Check (A–Z) tab and runs the single
  unified check. Reusable for ASIC chip screening.
- **Documentation revised** for the above (README, wiki, PDF manual): the AEAD
  hardware-encrypt/software-decrypt design and its software-decrypt workflow
  documented; the manual's architecture diagram redesigned.

### Fixed
- **MCP2210 desync** (`Mcp2210CommandResponseDesyncException` when the 1 s
  reset-indicator poll overlapped a button action): all MCP2210 HID access is
  serialized through a lock proxy with a one-shot desync retry, and the
  indicator poll is skipped while the MCP is busy
  (`ResetController.try_status()`).
- **GUI sizing**: long labels word-wrap and the window clamps to the screen
  size, so it fits and can be resized freely; the sidebar and each tab scroll on
  small displays.
- **GUI UART contention** (every command failing with "no frame marker" while
  the UART monitor's *Live read* was on): the monitor's background pump was doing
  blocking reads on the shared serial port and consuming command reply frames. The
  port now has a transaction lock — each command+reply is atomic, and the passive
  monitor uses a non-blocking read only when no operation holds the lock.
  Verified live: A–Z self-check 16/16 with the monitor pump running concurrently.
- **GUI feedback**: Program / Capture / FPGA-bitstream actions show a completion
  (or error) popup, their buttons disable while running, and Program shows 100%.

### GUI additions
- **Capture tab**: separate **Key** and **Plaintext** selectors, each `fixed`
  (editable hex box with a default) or `random` (fresh per trace); the capture
  **progress bar** now functions correctly (it previously updated the sidebar
  bar in error).
- **Reset control**: independent per-line toggle checkboxes (`controller`,
  `global`, `spi`, `spi_select`) so any line — notably `spi_select` — can be
  driven on its own; presets re-sync the toggles.
- **Registers tab**: **Raw bus access (peek/poke)** — read/write any address with
  a word count / hex data list (`ProactTarget.peek`/`poke`/`peek_words`/
  `poke_words`, firmware `CMD_PEEK` 0x19 / `CMD_POKE` 0x18); and a **Sw-RV target
  program** loader (select instruction/data `.vmem` files + data base, load into
  the target core to execute user-supplied software).

### Notes
- The full A–Z self-check passes 100% on a real CW305 board (supersedes the
  1.0.0 note below).

## [1.0.0] — 2026-07-21

Initial software release for the fabricated PROACT ASIC (and the FPGA/PCB it
shares).

### Added
- **Central hardware definition** `config/hardware.json` + `scripts/gen_hardware.py`
  generating the C header and Python register module (one source of truth).
- **Controller C library** (`Software/Controller`): register drivers (ctrl/status),
  hardware-AES driver, unified ASCON/Xoodyak AEAD driver, Sw-RV target driver,
  a four-core self-test, and a UART command server with a binary result frame.
  Trigger-source (`cfg_sel`) control (auto per core + `CMD_CFGSEL` override).
- **Target Sw-RV library** (`Software/SW_RV`): software AES-128 (encrypt + decrypt)
  driven through a low-handshake data-memory mailbox; status-bit-31 trigger.
- **Shared safe-UART layer** (`Software/common`): paced `putchar`, hazard-safe RX,
  no `sim_halt`.
- **Python host package** `proact_host`: transport + command protocol, MCP2210 SPI
  programmer, reset/GPIO control, ChipWhisperer capture (50 MHz HS2, GPIO3 CS),
  noise-tolerant UART monitor, file/random input generation, HDF5/NPZ storage,
  FIPS-197-verified AES validation, a high-level `PROACTExperiment`, and self-check.
- **CLI** `proact` (info/devices/build/test/run/capture/gui).
- **GUI** (`Software/GUI/proact_gui.py`): connection + reset controls with LED
  indicators; crypto experiment (fixed/random/file inputs, enc/dec, trigger +
  internal-trigger, cycle timer, compare-with-reference, log); ChipWhisperer
  (connect/disconnect, clock, transport select, capture, overall self-check);
  graphical status register; noise-tolerant monitor + CSV; `(?)` contextual
  help throughout.
- **Docs**: README, INSTALL, address map, hazards, bring-up guide, a 13-page wiki,
  and a 22-page graphical PDF manual (diagrams, colored callouts, syntax-highlighted
  code, full register/C/Python/GUI/ChipWhisperer reference).
- **Verification**: `tools/verify_regs_vs_rtl.py` (34/34 vs RTL), an iverilog AES
  known-answer sim against the real core, and `tools/verify_all.sh`.

### Fixed (software workarounds for documented hardware behavior)
- Trigger standardized on control **bit 30** (control bit 31 is not used for
  the capture trigger).
- The known "second experiment hangs" AES issue (safe reset-between-runs order).
- The verbose-printing "hang" (UART TX FIFO pacing).
- AEAD LEN word uses byte lengths (no off-by-one).
- Removed hard-coded toolchain paths; two-image vmem generation (avoids an
  87 MB single-image file).

### Notes
- At 1.0.0 release time no test had been run on real silicon/FPGA/ChipWhisperer
  (see TEST_STATUS). Superseded by 1.1.0: the A–Z self-check passes on a real CW305.
