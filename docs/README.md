# PROACT documentation

Use the guides below for the current host-software release. The retained PDF manual and historical wiki pages document the wider platform and earlier software revisions; they have not all been rewritten or revalidated against the new UI.

**Public release scope:** this repository provides host software, platform documentation and PCB material. Complete RTL, firmware sources, generated controller/Sw-RV VMEM images, FPGA bitstreams and reference capture datasets are separate artifacts. Historical build commands and links to those design files require the corresponding separately obtained design package. Current host-software instructions below take precedence for installation, GUI behavior and storage.

| Need | Current guide |
|---|---|
| Install in a dedicated environment | [Installation](../INSTALL.md) |
| Start with the GUI or CLI | [Start here](START_HERE.md) |
| Understand connections, clocks, samples and recovery through pictures | [Illustrated practical guide](ILLUSTRATED_GUIDE.md) |
| Find a command or option | [Generated CLI reference](CLI_REFERENCE.md) |
| Understand the GUI and long-job behavior | [GUI guide](wiki/GUI-Guide.md) |
| Use the shared Python library | [Python API](../Software/Python/README.md) |
| Choose storage format and recover committed data | [Storage guide](STORAGE.md) |
| Understand compatibility changes | [Migration](MIGRATION.md) |
| Inspect integrated validation and limitations | [Release report](../reports/HOST_SOFTWARE_RELEASE.md), [test guide](../tests/README.md) |
| Continue development | [Architecture](ARCHITECTURE.md), [maintenance workflow](../skills/proact-host-maintenance/SKILL.md) |

Hardware reference material is retained in [address_map.md](address_map.md), [hardware_hazards.md](hardware_hazards.md), the [hardware overview](wiki/Hardware-Overview.md) and [PCB documentation](../PCB/README.md). It does not certify current board wiring or define a new PCB revision. The [bring-up guide](bringup_guide.md) explains the externally supplied image prerequisites.

Regenerate the current command guide without device access:

```bash
.venv/bin/python tools/generate_cli_reference.py
```

The GUI screenshot and layout tools also run offline; see [GUI guide](wiki/GUI-Guide.md). Publishing the repository and synchronizing the separately hosted wiki are different operations; current release documentation is the Markdown in this repository.

For implementation ownership and data flow, see the [architecture map](ARCHITECTURE.md).

The [Acquisition framework](../Acquisition/README.md) provides the frequency-first
wizard, Husky capture, a simulated allowlisted oscilloscope backend, resumable native
storage, HDF5/CSV export, warm-up, TVLA and target-specific CPA. Live use requires
separately supplied firmware; physical oscilloscope validation remains pending.
