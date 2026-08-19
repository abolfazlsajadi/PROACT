*Part of the PROACT board docs — start at [Home](Home.md).*

# PROACT chip pinout (DIP‑28)

The PROACT ASIC sits in the DIP‑28 socket **U1**. Pin 1 is top‑left with the package notch up.
Colours in the diagram group pins by function. `IN·PU` = input with internal pull‑up.

![PROACT DIP-28 pinout](../img/proact_pinout_v2.png)

## Full pinout table

| Pin | Signal | Dir | Function | Routing / destination |
|:---:|--------|:---:|----------|-----------------------|
| 1  | `B_RST_N`     | IN·PU | Button / external reset (active low) | `SW1`; `R9` 10 k pull‑up; `JP1` → CW308 `nRST`; red LED **D7** |
| 2  | `TX`          | OUT | UART transmit | `JP3` → MCP2200 `RX` **or** CW308 (`J7.8`) |
| 3  | `RX`          | IN  | UART receive  | `JP5` → MCP2200 `TX` **or** CW308 (`J7.7`) |
| 4  | `spi_global_RST_N` | IN·PU | Global reset (board net `GRST`) | MCP2210 GPIO2; CW308 LED3; red LED **D9** |
| 5  | `C_RST_N`     | IN·PU | Controller reset (board net `CRST`) | MCP2210 GPIO5; CW308 LED2; red LED **D8** |
| 6  | `out_pins[0]` | OUT | Debug output | yellow LED **D2** · `J1.9` · `J9.20` |
| 7  | `VDDIO`       | PWR | I/O supply | 3.3 V |
| 8  | `VSS`         | GND | Ground | — |
| 9  | `SYSCLK_P`    | IN  | Chip clock | from **J12** clock select · `CLK` test point · `J7.5` |
| 10 | `spare_io`    | IN  | Spare input (user‑driven) | orange LED **D3** · `J1.7` · `J9.19` |
| 11 | `out_pins[5]` | OUT | Debug output | yellow LED **D4** · `J1.5` · `J9.18` |
| 12 | `out_pins[6]` | OUT | Debug output | yellow LED **D5** · `J1.3` · `J9.17` |
| 13 | `out_pins[1]` | OUT | Trigger (config) | `J10` (3‑4) → CW308 `TRIG` |
| 14 | `trigger_Out` | OUT | Trigger (normal) | `J10` (2‑4) → CW308 `TRIG` |
| 15 | **`VDD` 0.8 V core** | PWR | Core supply | via **R7** shunt (with pin 28) |
| 16 | `out_pins[7]` | OUT | **Alive** heartbeat | yellow‑green LED **D1** (blinks = alive) |
| 17 | `SIn`         | IN  | SPI data in (MOSI) | `JP6` → MCP2210 **or** CW308 |
| 18 | `SSel_n`      | IN  | SPI select (active low) | `J6` → MCP2210 GPIO4 **or** CW308 GPIO3 |
| 19 | `sck`         | IN  | SPI clock | `JP4` → MCP2210 **or** CW308 |
| 20 | **`spi_c_RST_N`** | IN·PU | SPI‑controller reset (board net `SPI_RST`) | MCP2210 GPIO1; CW308 LED1; red LED **D10** |
| 21 | `out_pins[11]`| OUT | Debug output | yellow LED **D6** · `J1.1` · `J9.11` |
| 22 | `VDDIO`       | PWR | I/O supply | 3.3 V |
| 23 | `trigger_in`  | IN  | Trigger input | `J6.6` ← CW308 GPIO3 (link `J6` 5‑6) |
| 24 | `out_pins[4]` | OUT | IBEX PC probe (bit 4) | `JP2.3` |
| 25 | `out_pins[3]` | OUT | IBEX PC probe (bit 3) | `JP2.2` |
| 26 | `out_pins[2]` | OUT | IBEX PC probe (bit 2) | `JP2.1` |
| 27 | `out_pins[8]` | OUT | Trigger (reserve / software) | `J10` (4‑6) → CW308 `TRIG` |
| 28 | **`VDD` 0.8 V core** | PWR | Core supply | via **R7** shunt (with pin 15) |

The core supply pins **15 / 28** reach the chip through the `R7` 0.01 Ω sense shunt — see
[Power &amp; SCA](Power-and-SCA.md). The routing jumpers named in the last column are described on
[Jumpers, switches &amp; LEDs](Jumpers-Switches-LEDs.md), and the `J12` clock select on
[Clock System](Clock-System.md).

## `IN·PU` — inputs with internal pull‑ups

Pins **1, 4, 5 and 20** — the four reset lines `B_RST_N`, `spi_global_RST_N`, `C_RST_N` and
`spi_c_RST_N` — are inputs with an **internal pull‑up inside the chip**: left undriven, they sit
high, i.e. the resets are **deasserted** by default. Driving a line low asserts the reset and
lights its red LED (D7–D10).

## `JP2` — IBEX PC probe bits

`out_pins[2]/[3]/[4]` on `JP2` expose bits 2–4 of the program counter of the **IBEX** RISC‑V
soft‑core for probing. Note the index order is **reversed** with respect to the pin order
(pin 24 = bit 4 … pin 26 = bit 2), matching the board silk `out[2:4]`.

## Silkscreen names

The board silkscreen prints shorter legacy names for several signals. Translate between the
print and the datasheet signal names with this table:

| Printed on the board (pin) | Signal name |
|---------------|---------|
| `GRST` (4) | `spi_global_RST_N` |
| `CRST` (5) | `C_RST_N` |
| `Mem[23]` (6) | `out_pins[0]` |
| `CLK` (9) | `SYSCLK_P` |
| `Spare_In` (10) | `spare_io` |
| `UART_Rvalid` (11) | `out_pins[5]` |
| `Mem_Req` (12) | `out_pins[6]` |
| `Trig_CFG` (13) | `out_pins[1]` |
| `Trigger` (14) | `trigger_Out` |
| `SPI_RST` (15) | **`VDD` core** |
| `ALIVE` (16) | `out_pins[7]` |
| `SPI_MOSI` (17) | `SIn` |
| `S_Sel` (18) | `SSel_n` |
| `SPI_SCK` (19) | `sck` |
| `VCORE` (20) | **`spi_c_RST_N`** |
| `CoProc_Req` (21) | `out_pins[11]` |
| `Trig_In` (23) | `trigger_in` |
| `PC2/PC3/PC4` (24/25/26) | `out_pins[4]/[3]/[2]` |
| `Rsv_Trig` (27) | `out_pins[8]` |
