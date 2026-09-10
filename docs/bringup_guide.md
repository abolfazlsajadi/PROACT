# Board preparation for the host-software release

This guide identifies the artifacts and checks needed before using PROACT host
software with an ASIC evaluation board or CW305 FPGA. Python installation,
opening a communication resource and verifying a board operation are separate
steps. The host update's offline tests do not establish a live-board result.

## 1. Obtain matching design artifacts

The public host release includes GUI/CLI code and platform/PCB documentation.
It does not include the complete RTL, firmware sources, generated VMEM images,
an FPGA bitstream or reference capture datasets. Obtain these artifacts through
the project before attempting the corresponding operation.

| Workflow | Required artifacts |
|---|---|
| ASIC host communication | Controller VMEM matching the fabricated design and host protocol |
| FPGA host communication | Matching PROACT CW305 bitstream and controller VMEM |
| Sw-RV workload | Matching instruction and data VMEM images, plus their documented data-base address |
| Compare a recorded dataset | The dataset and its build, acquisition and validation metadata |

Record each image's version or hash. A GUI default such as
`Software/Controller/main.vmem` identifies a conventional path; it does not
mean the public checkout contains the file. Select the actual externally
supplied image in the GUI or CLI. Build instructions in historical documentation
require the separate design package and its toolchain.

## 2. Check the software offline

Follow [INSTALL.md](../INSTALL.md), using a dedicated environment. Add the
`capture` extra when preparing a ChipWhisperer workflow. From the repository
root:

```bash
./run_cli.sh doctor --json
./run_cli.sh program --help
./run_cli.sh load-swrv --help
./tools/run_tests.sh -W error
./run_gui.sh
```

These commands inspect software, show help, run offline tests or open the GUI
disconnected. `doctor` does not enumerate USB or test driver connectivity.
Missing generated images are expected until you supply them.

## 3. Confirm the physical setup separately

Identify the board, its verified power/clock configuration, UART bridge and
baud, SPI bridge and reset wiring. Compare the physical board with the
[PCB documentation](../PCB/README.md) and read the
[bus access rules](hardware_hazards.md) before using registers or reset controls.

The host's ASIC and FPGA clock paths differ; see the
[illustrated clock diagram](ILLUSTRATED_GUIDE.md#2-choose-asic-or-fpga-before-connecting).
A software connection indicator does not prove that a clock reaches the chip
or that an image matches the hardware.

Configure operating-system device permissions for the selected bridges using
your platform's procedures. Python setup does not install udev rules or change
services. Run the GUI/CLI as the ordinary user in its configured environment.

## 4. Select images and connect

Use the GUI sidebar or the documented CLI options to select the intended
platform, port and matching firmware file. The ordinary GUI uses the MCP2210
SPI programmer and MCP2200 UART bridge. The alternative Husky transport entry
is disabled because that GUI backend is not implemented.

Programming a controller image transfers its words through SPI and changes
reset state. Verify the subsequent controller response independently. FPGA
programming changes the FPGA configuration; supply the intended bitstream
explicitly. Sw-RV has its own images and loader.

The host parser validates VMEM syntax and unsigned 32-bit words before its
reset sequence. That validation does not certify image provenance or hardware
compatibility. See [migration notes](MIGRATION.md) for parser and cleanup
behavior and the [GUI guide](wiki/GUI-Guide.md) for each control.

## 5. Keep functional and measurement evidence distinct

A known expected response, a complete saved record and a usable analog waveform
establish different properties. The GUI's **Self-Check (A–Z)** is a live
workflow that can program/use resources and acquire a waveform; it is not the
offline regression suite. Read individual PASS, FAIL and SKIP results and their
dependencies rather than treating completion as a universal board certificate.

For a new measurement workflow, follow the
[illustrated guide](ILLUSTRATED_GUIDE.md), review the current form and preserve
actual settings, row counts, failures and image identities with the dataset.
The current host release does not supply a new gain optimum, an exact operation
window, a recovery threshold or an FPGA/ASIC comparison result.

Current implementation guidance is in the [CLI reference](CLI_REFERENCE.md),
[architecture map](ARCHITECTURE.md) and [storage guide](STORAGE.md). Historical
manual/wiki procedures describe earlier design releases and may require files
or tools outside this public host tree.
