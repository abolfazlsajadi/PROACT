*Part of the PROACT board docs — start at [Home](Home.md).*

# Getting started

First power-up of the PROACT evaluation board — from an empty socket to a
blinking heartbeat and a first UART exchange. Follow the steps in order; **Step 0 must be done
before the chip ever meets the board.**

## Prerequisites

- **A 3.3 V supply for `VDDIO`** — either a ChipWhisperer **CW308 UFO** baseboard (the board mounts
  on its `J7`/`J8`/`J9` edge connectors) **or** a 3.3 V bench supply feeding the `VDDIO` test point.
- **The PROACT ASIC** (28-pin DIP) — kept **out** of the socket until Step 0 is complete.
- **A multimeter** — needed for the Vcore trim.
- **MCP2200 / MCP2210 USB bridge modules** — only for the USB bench route (first UART contact
  below); see [USB Bridge Modules](USB-Bridge-Modules.md).
- **ChipWhisperer capture hardware** — optional; only needed for side-channel work
  (see [CW308 Setup](CW308-Setup.md)).

## Step 0 — trim Vcore before inserting the chip

The 0.8 V core rail is generated on-board by the **U2 TPS74801** LDO and trimmed with the
multi-turn potentiometer **R20**:

**V<sub>core</sub> = 0.8 V × (1 + R20 / 8.2 kΩ) → adjustable 0.80 – 0.90 V**

![Power path](../img/power_path.png)

> ⚠️ **Do this before the chip ever meets the board:**
> 1. Leave the PROACT chip **out** of socket `U1`.
> 2. Set `JP7` to **2‑3** (direct).
> 3. Apply 3.3 V `VDDIO` (mount on the CW308, or feed the `VDDIO` test point on the bench).
> 4. Meter between the **`Vcore` test point** and `GND` (no load → no shunt drop).
> 5. Turn `R20` (multi‑turn) until the meter reads **0.800 V**.
> 6. Power down, insert the chip (notch up), power up and re‑check under load.

The `Vcore` test point sits on the die side of the `R7` sense shunt, which is deliberately
capacitor-free — probe it **high-impedance only**. Full details of the rail, the `JP7` route and
the measurement chain are on [Power and SCA](Power-and-SCA.md).

## Inserting the chip

The PROACT ASIC sits in the DIP-28 socket **U1**. Insert it with the **package notch up** — pin 1
is then top-left, matching the pinout diagram:

![PROACT DIP-28 pinout](../img/proact_pinout_v2.png)

Insert and remove the chip only with the board powered down. The full pin table is on
[Chip Pinout](Chip-Pinout.md).

## Choosing a clock

The chip clock `SYSCLK_P` (pin 9) can come from three sources, selected on **J12**:

| Source | J12 link | Path |
|--------|:--------:|------|
| **On‑board 50 MHz** | **3‑4** | `Y1` oscillator → `R22` 20 Ω → chip clock |
| **External input** | **1‑2** | `J11` coaxial jack → chip clock. *The fitted connector is **SMA** (the board silk reads "BNC").* |
| **ChipWhisperer** | **5‑6** | CW clock arriving on `J7.3` (CW308 `CLKFB` line) → `R8` 100 Ω → chip clock |

<p align="center">
  <img src="../img/jumpers/J12.png" alt="J12 clock select" width="70%">
</p>

Fit **exactly one** source link on `J12` (`1‑2`, `3‑4` *or* `5‑6`). The chip clock is
**permanently echoed to the ChipWhisperer on `J7.5`** — no jumper needed — so the scope can sample
synchronously. Leave the **CW308's `J3` clock jumper unpopulated** — it drives `J7.5` (the
clock‑echo pin) and would fight the selected source. For the first bench bring-up, `J12` **3‑4**
(on-board 50 MHz) is the simplest choice. Full detail: [Clock System](Clock-System.md).

## First sign of life

Power up and watch the LEDs (full table on
[Jumpers, Switches and LEDs](Jumpers-Switches-LEDs.md)):

- **D1** (yellow-green) is the **alive heartbeat** on `out_pins[7]` (pin 16) — **blinking = chip
  alive**. This is the signal you are waiting for.
- **D7–D10** (red) show the four reset lines `B_RST_N` (pin 1), `C_RST_N` (pin 5),
  `spi_global_RST_N` (pin 4) and `spi_c_RST_N` (pin 20) — a red LED **lights while its reset is
  asserted** (line low).

> 💡 **All four red LEDs on at once is normal during reset — it is not a fault.** They are wired
> from `VDDIO` through the LED into the reset line, so a low (active) reset lights them.

## First UART contact (USB bench, configuration A)

Talk to PROACT from a PC through the MCP2200 USB-UART module, no ChipWhisperer needed.
Set the board up as **Typical configuration A**:

| Setting | Value |
|---------|-------|
| **Precondition** | `Vcore` trimmed to 0.800 V ([Step 0](#step-0--trim-vcore-before-inserting-the-chip)) |
| `S1‑6`, `S1‑7` | **on** (power both bridge modules) |
| `S1‑1…5` | on as needed for GPIO read‑back |
| `JP3`, `JP5`, `JP4`, `JP6` | **M** (2‑3) |
| `J6` | **1‑3** (MCP2210 GPIO4 → `SSel_n`) |
| `J12` | **3‑4** (on‑board 50 MHz) |
| `JP7` | **2‑3** (direct) |
| `JP1` | open (reset via `SW1`) |
| `VDDIO` | feed 3.3 V into the `VDDIO` test point |

With `JP3`/`JP5` in **M**, PROACT `TX` (pin 2) reaches the MCP2200 `RX` and PROACT `RX` (pin 3) is
driven from the MCP2200 `TX` — open the module's virtual COM port on the PC and talk to the chip.
Module pin maps and the `S1` read-back switches are on
[USB Bridge Modules](USB-Bridge-Modules.md); for capture setups on the ChipWhisperer, continue
with [CW308 Setup](CW308-Setup.md).

## Quick troubleshooting

| Symptom | What to check |
|---------|---------------|
| **`D1` does not blink** | `Vcore` trim (0.800 V at the `Vcore` test point, [Step 0](#step-0--trim-vcore-before-inserting-the-chip)) and the clock source — exactly one `J12` source link (`5‑6` for a ChipWhisperer clock, CW308 `J3` unpopulated); observe the `CLK` test point |
| **All four red LEDs stay on** | A reset line is held low — release `SW1`, check the MCP2210 GPIO reset outputs (pins 4 / 5 / 20) and, if `JP1` is closed, the CW308 `nRST` line |
| **`D3` never lights** | Expected — `spare_io` (pin 10) is a **user-driven input**; the chip never lights it. Drive it high via `J1.7` or `J9.19` to see D3 |
