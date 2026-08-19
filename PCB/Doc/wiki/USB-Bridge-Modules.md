*Part of the PROACT board docs — start at [Home](Home.md).*

# USB bridge modules

Two Microchip USB‑bridge break‑out modules plug into the board's 1×7 header pairs and let a PC
talk to the PROACT chip without a ChipWhisperer:

- **MCP2200 — USB ↔ UART**, in sockets **J2 / J3**: drives the chip's UART (`TX` pin 2 /
  `RX` pin 3).
- **MCP2210 — USB ↔ SPI + GPIO**, in sockets **J4 / J5**: drives the chip's SPI
  (`SIn` / `sck` / `SSel_n`), the three GPIO reset lines, and reads back signals through the
  **S1** DIP switch.

Whether the chip actually hears a module or the ChipWhisperer is decided by the routing jumpers
(`JP3`/`JP5` for UART, `JP4`/`JP6` for SPI, `J6` for `SSel_n`) — position **M** (2‑3) selects the
module. See [Jumpers, Switches & LEDs](Jumpers-Switches-LEDs.md) for every position, and
[CW308 Setup](CW308-Setup.md) for the ChipWhisperer side.

![Board v2 placement — J2/J3 and J4/J5 module sockets](../img/board_v2_placement.png)

---

## MCP2210 — USB ↔ SPI + GPIO (sockets `J4` / `J5`)

| Module pin | Signal | Connected to |
|:----------:|--------|--------------|
| 1  | GPIO0 | reads GPIO1 (`spi_c_RST_N`) when `S1‑4` on |
| 2  | GPIO1 | `SPI_RST` → PROACT pin **20** (`spi_c_RST_N`) |
| 3  | GPIO2 | `GRST` → PROACT pin 4 (`spi_global_RST_N`) |
| 4  | GPIO3 | reads GPIO4 (`SSel_n`) when `S1‑1` on |
| 5  | GPIO4 | `SSel_n` → `J6` (1‑3) → PROACT pin 18 |
| 6  | MOSI  | `JP6` (2‑3) → PROACT pin 17 (`SIn`) |
| 7  | SCK   | `JP4` (2‑3) → PROACT pin 19 (`sck`) |
| 8  | MISO  | not connected |
| 9  | GPIO5 | `CRST` → PROACT pin 5 (`C_RST_N`) |
| 10 | GPIO6 | reads GPIO5 (`C_RST_N`) when `S1‑5` on |
| 11 | GPIO7 | reads `X1` debug bus (`J1`) when `S1‑2` on |
| 12 | GPIO8 | reads GPIO2 (`spi_global_RST_N`) when `S1‑3` on |
| 13 | GND   | ground |
| 14 | VDD   | 3.3 V when `S1‑7` on |

The MCP2210's GPIOs thus fall into three roles: **drivers** (GPIO1/2/5 assert the reset lines,
GPIO4 drives `SSel_n`), **readers** (GPIO0/3/6/7/8 read back other signals when the matching S1
switch is on), and the **SPI engine** (MOSI/SCK to `SIn`/`sck`; MISO is not connected — PROACT's
SPI is write‑only from the host's point of view).

## MCP2200 — USB ↔ UART (sockets `J2` / `J3`)

| Module pin | Signal | Connected to |
|:----------:|--------|--------------|
| 6  | TX  | `JP5` (2‑3) → PROACT `RX` (pin 3) |
| 7  | RX  | `JP3` (2‑3) → PROACT `TX` (pin 2) |
| 14 | VDD | 3.3 V when `S1‑6` on |

---

## S1 read‑back loops (switches 1–5)

`S1` is a 7‑position DIP switch. Switches **1–5** enable GPIO **read‑back** loops (so the USB host
can read the state of a signal it — or the chip — is driving); switches **6–7** power the two USB
bridge modules.

| Switch | Enables | Purpose |
|:------:|---------|---------|
| **1** | MCP2210 GPIO3 reads GPIO4 | Read back the **`SSel_n`** state |
| **2** | MCP2210 GPIO7 reads `X1` | Read back the **debug signal** selected on `J1` |
| **3** | MCP2210 GPIO8 reads GPIO2 | Read back the **global reset** (`spi_global_RST_N`) |
| **4** | MCP2210 GPIO0 reads GPIO1 | Read back the **SPI reset** (`spi_c_RST_N`) |
| **5** | MCP2210 GPIO6 reads GPIO5 | Read back the **controller reset** (`C_RST_N`) |
| **6** | MCP2200 `VDD` → 3.3 V | Power the **UART** bridge module |
| **7** | MCP2210 `VDD` → 3.3 V | Power the **SPI** bridge module |

Each read‑back loop simply closes a switch between a *driving* pin and a *reading* GPIO: with the
switch on, the host can poll the reading GPIO over USB and confirm what is really on the wire
(e.g. that a reset it asserted on GPIO1 actually reached the line). With the switch off, the two
pins are isolated.

### Reading chip outputs — the `J1` / `X1` mechanism (switch 2)

Switch `S1‑2` is the odd one out: it does not read another MCP2210 GPIO, it reads the **`X1`**
line coming from the debug monitor header **`J1`** (silk `SPI_DBG`, 2×5). Each **odd** pin of
`J1` is a live PROACT debug signal; the adjacent **even** pin is the common `X1` line. Fit a
jumper across a row to route that signal onto `X1`, which the MCP2210 reads via **GPIO7**
(enable `S1‑2`). You can equally probe the odd pins directly.

| `J1` pin | Signal | PROACT pin | LED |
|:--------:|--------|:----------:|:---:|
| 9 | `out_pins[0]`  | 6  | D2 |
| 7 | `spare_io`     | 10 | D3 |
| 5 | `out_pins[5]`  | 11 | D4 |
| 3 | `out_pins[6]`  | 12 | D5 |
| 1 | `out_pins[11]` | 21 | D6 |
| 2,4,6,8,10 | `X1` common (→ MCP2210 GPIO7) | — | — |

So the USB host can watch any one of the chip's debug outputs at a time: pick it with a `J1`
jumper, close `S1‑2`, and poll GPIO7.

---

## Powering rules (switches 6–7)

- The modules are powered from the board's 3.3 V `VDDIO` rail through **`S1‑6`** (MCP2200, UART)
  and **`S1‑7`** (MCP2210, SPI). For USB‑only bench use off the ChipWhisperer, feed 3.3 V into
  the `VDDIO` test point instead of mounting on the CW308.
- **During side‑channel capture, keep `S1‑6` and `S1‑7` off** — the USB bridges stay idle and
  unpowered, and the routing jumpers are set to **CW** so the ChipWhisperer talks to the chip.
  The full capture checklist is on [CW308 Setup](CW308-Setup.md).
- For USB bench bring‑up (configuration A on [CW308 Setup](CW308-Setup.md)): `S1‑6`/`S1‑7`
  **on**, `S1‑1…5` on as needed for read‑back, jumpers `JP3`/`JP5`/`JP4`/`JP6` to **M** (2‑3),
  `J6` linked 1‑3 — and, as always, `Vcore` trimmed to 0.800 V *before* the chip is inserted
  (see [Getting Started](Getting-Started.md)).
