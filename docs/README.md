# Documentation

> The wiki pages in `wiki/` are also published online:
> **https://github.com/abolfazlsajadi/PROACT_Design/wiki**

All written documentation for PROACT is collected in this directory. This page
is the index: what exists, which one to read, and how each piece is produced.

## Where to start

| If you want to… | Read |
|---|---|
| understand the whole system in one sitting | the **[PDF manual](manual/proact_manual.pdf)** |
| get a board working today | [Getting-Started](wiki/Getting-Started.md), then [bringup_guide.md](bringup_guide.md) |
| learn the libraries by running code | the **[tutorial notebook](../examples/PROACT_Tutorial.ipynb)** |
| look up an address or a register bit | [address_map.md](address_map.md) |
| know why your firmware hangs | [hardware_hazards.md](hardware_hazards.md) |
| work on one directory of the repo | that directory's own README (see [below](#directory-readmes)) |

## Manual

**[`manual/proact_manual.pdf`](manual/proact_manual.pdf)** — a single, complete,
illustrated guide (diagrams, colour-coded rules, worked examples). It covers the
whole system in eighteen chapters, from the address map and hardware behaviour
through the C library, the Python library, the CLI and the GUI to trace capture,
verification status, troubleshooting and the as-packaged chip pinout. It is the
recommended starting point.

The PDF is generated from `manual/proact_manual.tex` with `pdflatex` (TeX Live);
the images it uses are in [`images/`](images/). Rebuild it after changing the
source:

```bash
cd docs/manual && pdflatex proact_manual.tex && pdflatex proact_manual.tex
```

(Run it twice so the table of contents and cross-references settle. The `.aux`,
`.log`, `.toc` and friends are build intermediates and are git-ignored; the PDF
itself is tracked.)

## Tutorial notebook

**[`../examples/PROACT_Tutorial.ipynb`](../examples/PROACT_Tutorial.ipynb)** — a
runnable, section-by-section guide to the Python and C libraries: connect and
program, register access, AES1/AES2 encrypt/decrypt, AEAD hardware encryption
with software decryption, PRNG, Sw-RV loading, ChipWhisperer capture, and the
full A–Z self-check.

## Wiki pages (task-focused, in Markdown)

The [`wiki/`](wiki/) folder contains thirteen short pages for specific tasks.
The Markdown here is the **source**; `tools/publish_wiki.py` rewrites the
cross-links and image references for the GitHub-wiki layout and pushes them to
the project wiki (`--build` builds into `build/wiki` without pushing). Edit the
files here, never the wiki copy.

| Page | Purpose |
|---|---|
| [Home](wiki/Home.md) | overview and links |
| [Getting-Started](wiki/Getting-Started.md) | the first session, step by step |
| [Hardware-Overview](wiki/Hardware-Overview.md) | the on-chip components |
| [Address-and-Register-Map](wiki/Address-and-Register-Map.md) | every address and bit |
| [Hardware-Hazards](wiki/Hardware-Hazards.md) | the software access rules |
| [Controller-Firmware](wiki/Controller-Firmware.md) / [Target-Software](wiki/Target-Software.md) | the on-chip programs |
| [Python-API](wiki/Python-API.md) / [CLI](wiki/CLI.md) / [GUI-Guide](wiki/GUI-Guide.md) | the PC-side tools |
| [ChipWhisperer](wiki/ChipWhisperer.md) | capturing power traces |
| [Testing](wiki/Testing.md) / [Troubleshooting](wiki/Troubleshooting.md) | verification and troubleshooting |

## Quick reference (single-topic Markdown)

- **[address_map.md](address_map.md)** — the canonical address and register tables
- **[hardware_hazards.md](hardware_hazards.md)** — the bus access contract and the
  software access rules (R1–R10, including the host-side AEAD
  decrypt convention)
- **[bringup_guide.md](bringup_guide.md)** — the program → run → capture flow

## Directory READMEs

Reference documentation lives here; the *working* documentation for each part of
the repository sits next to the code it describes.

| Directory | Covers |
|---|---|
| [`../Software/Python/`](../Software/Python/README.md) | the `proact_host` library, the `proact` CLI, the offline test suite |
| [`../Software/GUI/`](../Software/GUI/README.md) | the graphical control application |
| [`../Software/Controller/`](../Software/Controller/README.md) | the on-chip command-server firmware |
| [`../Software/SW_RV/`](../Software/SW_RV/README.md) | the second core's software-AES firmware |
| [`../Software/common/`](../Software/common/README.md) | the shared C layer and the generated register header |
| [`../FPGA/`](../FPGA/README.md) | the CW305 Vivado build flow |
| [`../ASIC/`](../ASIC/README.md) | the frozen RTL and the full-chip testbench |
| [`../PCB/`](../PCB/README.md) | the carrier board (with its own wiki set under `PCB/Doc/`) |
| [`../tests/`](../tests/README.md) | what the offline suite pins, and what it deliberately does not |
| [`../examples/`](../examples/README.md) | the tutorial notebook |
| [`../INSTALL.md`](../INSTALL.md) | toolchain, USB permissions, optional components |

## Provenance of the documented facts

The address/register facts in all of these documents come from one place —
`config/hardware.json` — and are checked against the frozen chip design, so the
docs, the firmware, and the Python tools always agree. To change a register
definition, edit the JSON and regenerate (`python3 scripts/gen_hardware.py`);
generated files must not be edited by hand. `tools/verify_regs_vs_rtl.py`
re-derives the same facts straight from `ASIC/rtl/` as an independent check.

## Images

[`images/`](images/) holds the figures used by the README, the wiki and the
manual: GUI screenshots, the SoC architecture and register diagrams, the die and
package photographs, and the CPA result plots. Two of them are regenerated by
script rather than by hand — rerun these whenever the GUI's appearance or the
attack results change, then rebuild the manual and the wiki:

```bash
python3 tools/gen_gui_screenshots.py    # the gui_*.png set (offscreen, no hardware)
python3 tools/gen_cpa_figures.py        # the cpa_*.png set, from datasets/
```

Two figures are optional placeholders the manual picks up automatically if the
hardware design team's originals become available: `architecture.png` (used in
place of the built-in TikZ system drawing) and `pinout.png` (the package
drawing that would accompany the pinout chapter).
