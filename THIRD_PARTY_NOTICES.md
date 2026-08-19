# Third-Party Notices

PROACT integrates several externally-authored hardware cores. Each retains its
original copyright and license; the headers in the source files are authoritative.
This file summarizes them so the licensing of the combined work is clear.

The PROACT-authored parts (the SoC integration, bus, register file, SPI loader,
timer, RNG wrapper, the firmware, the host software, the GUI, and the docs) are
licensed under **Apache-2.0** (see [LICENSE](LICENSE)).

> **Note on licenses:** the bundled cores are under *different* licenses,
> including one copyleft license (ASCON, GPL-3.0) and one public-domain
> dedication (Xoodyak, CC0). Anyone redistributing the combined RTL — e.g. the
> `ASIC/rtl/` tree or a generated bitstream — must honor every component's terms.
> If that is a concern for a particular distribution, the ASCON core can be
> dropped from `ASIC/rtl/PROACT.source_list.tcl` (the other three cores remain).

**License texts included in this repository:** Apache-2.0 → [LICENSE](LICENSE);
GPL-3.0 → [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt); CC0-1.0 →
[LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt); the ARM licence text is embedded
in the file header itself. No upstream project needs to be *forked* — the
obligations are to retain the file headers (retained), distribute the license
texts (included in this repository), and state origin and changes (this file).

| Component | Location | Author / Origin | License |
|-----------|----------|-----------------|---------|
| **Ibex** RISC-V core | `ASIC/rtl/Ibex/` | lowRISC contributors; ETH Zurich & University of Bologna | Apache-2.0 |
| **AES1** (LUT S-box) | `ASIC/rtl/AES1/` | Copyright 2015 Google Inc. | Apache-2.0 |
| **AES2** (composite-field S-box) | `ASIC/rtl/AES2/` | Copyright 2015 Google Inc. | Apache-2.0 |
| **ASCON** AEAD core | `ASIC/rtl/ASCON/` | Robert Primas, IAIK — Graz University of Technology | GPL-3.0 |
| **Xoodyak** AEAD core | `ASIC/rtl/Xoodyak/` | Silvia Mella, STMicroelectronics | CC0-1.0 (public domain) |
| **GMU LWC** Hardware API framework | `ASIC/rtl/ASCON/`, `ASIC/rtl/Xoodyak/` | GMU CERG (`github.com/GMUCERG/LWC`) | see cores above |
| **AHBUART** UART bridge | `ASIC/rtl/UART/AHBUART.v` | Copyright 2012 ARM Ltd. | ARM example EULA (BSD-style, **academic purposes**; full text in the file header) |

**Modifications statement (Apache-2.0 §4(b) and general provenance).** The
copies under `ASIC/rtl/` are the exact sources used for the PROACT tape-out:
a *subset* of each upstream project (only the files the SoC needs), integrated
for the PROACT bus/reset/trigger environment. Original license headers are
retained unmodified. Files in these directories *without* a third-party header
(for example `Ibex/addconv.sv`, the SystemVerilog wrappers
`new*_wrapper.sv`, and the `*_fifo_interface.sv` files) are PROACT-authored
integration glue (Apache-2.0). The Ibex headers reference the upstream
`CREDITS.md`, available at https://github.com/lowRISC/ibex/blob/master/CREDITS.md.

## Details

### Ibex (Apache-2.0)
```
Copyright lowRISC contributors.
Copyright 2018 ETH Zurich and University of Bologna, see also CREDITS.md.
Licensed under the Apache License, Version 2.0.
SPDX-License-Identifier: Apache-2.0
```
Upstream: https://github.com/lowRISC/ibex

### AES1 / AES2 (Apache-2.0)
```
Copyright 2015, Google Inc.
Licensed under the Apache License, Version 2.0.
```
Two AES-128 cores kept deliberately distinct (LUT vs composite-field S-box) so
their side-channel leakage can be compared on the same die.

### ASCON (GPL-3.0)
```
@author     Robert Primas <rprimas@proton.me>
@copyright  Copyright (c) 2020 IAIK, Graz University of Technology, AUSTRIA
@license    GNU Public License (GPL-3.0) -- http://www.gnu.org/licenses/gpl-3.0.txt
```
Built on the GMU LWC Hardware API development package. Full license text:
[LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt). Because GPL-3.0 is copyleft,
any redistribution of a combined work containing this core (the RTL tree or a
bitstream built from it) must satisfy GPL-3.0's source-availability terms for
that combination — this repository satisfies these terms by providing full sources.

### Xoodyak (CC0-1.0, public domain)
```
@author     Silvia Mella <silvia.mella@st.com>
@license    To the extent possible under law, the implementer has waived all
            copyright and related rights -- http://creativecommons.org/publicdomain/zero/1.0/
```
Built on the GMU LWC Hardware API development package. This is Xoodyak **v2**
(NIST LWC final round: the nonce is absorbed together with the key). The host-side
software reference (`Software/Python/proact_host/aead_soft.py`) reproduces exactly
this variant.

### AHBUART (ARM example EULA — academic purposes)
```
Copyright (c) 2012, ARM. All rights reserved.
```
BSD-style ARM end-user licence: redistribution and use, with or without
modification, are permitted **for academic purposes** provided the copyright
notice, conditions and disclaimer are retained (these are retained; the full
licence text appears in the header of `ASIC/rtl/UART/AHBUART.v`). PROACT is an
academic research project, which falls within this licence's intended scope;
any commercial reuse of this file requires prior review of those header terms.
