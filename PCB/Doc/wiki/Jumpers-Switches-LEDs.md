*Part of the PROACT board docs — start at [Home](Home.md).*

# Jumpers, Switches & LEDs

Every 3‑pin routing jumper follows the same rule — **the centre pin is the PROACT chip**; jumper it
toward the module (**M**) to use the USB bridge, or toward **CW** to use the ChipWhisperer.
Silkscreen tags such as `Mosi M/CW` and `SCK M/CW` spell this out — **M** = module,
**CW** = ChipWhisperer.

![All jumpers at a glance](../img/jumpers/overview.png)

---

## JP1 — reset link (1×2)

<p align="center">
  <img src="../img/jumpers/JP1.png" alt="JP1 reset link" width="70%">
</p>

| Jumper | Type | Function |
|--------|------|----------|
| **JP1** | 1×2 | Close to tie PROACT `B_RST_N` (pin 1) to the CW308 `nRST` line. Leave open to reset only from `SW1`. |

---

## JP2 — IBEX PC probe header (1×3)

<p align="center">
  <img src="../img/jumpers/JP2.png" alt="JP2 probe header" width="70%">
</p>

| Jumper | Type | Function |
|--------|------|----------|
| **JP2** | 1×3 | Probe header for IBEX PC bits — `out_pins[2]/[3]/[4]` (chip pins 26/25/24). No jumper fitted. |

> `out_pins[2]/[3]/[4]` on `JP2` expose bits 2–4 of the program counter of the **IBEX** RISC‑V
> soft‑core for probing. Note the index order is **reversed** with respect to the pin order
> (pin 24 = bit 4 … pin 26 = bit 2), matching the board silk `out[2:4]`.

---

## JP3 / JP5 — UART routing

<p align="center">
  <img src="../img/jumpers/JP3_JP5.png" alt="JP3/JP5 UART routing" width="70%">
</p>

| Jumper | Centre = PROACT | Position **M** (module) | Position **CW** (ChipWhisperer) |
|--------|-----------------|-------------------------|---------------------------------|
| **JP3** | `TX` (pin 2)  | 2‑3 → MCP2200 `RX` | 1‑2 → CW308 `J7.8` |
| **JP5** | `RX` (pin 3)  | 2‑3 → MCP2200 `TX` | 1‑2 → CW308 `J7.7` |

See [USB Bridge Modules](USB-Bridge-Modules.md) for the MCP2200 side and
[CW308 Setup](CW308-Setup.md) for the ChipWhisperer side (note the UART direction mapping onto
`TIO1`/`TIO2`).

---

## JP4 / JP6 — SPI routing

<p align="center">
  <img src="../img/jumpers/JP4_JP6.png" alt="JP4/JP6 SPI routing" width="70%">
</p>

| Jumper | Centre = PROACT | Position **M** (module) | Position **CW** (ChipWhisperer) |
|--------|-----------------|-------------------------|---------------------------------|
| **JP4** | `sck` (pin 19) | 2‑3 → MCP2210 `SCK` | 1‑2 → CW308 `J7.12` |
| **JP6** | `SIn` (pin 17) | 2‑3 → MCP2210 `MOSI` | 1‑2 → CW308 `J7.14` |

The SPI select line `SSel_n` is routed separately on **`J6`** (below).

---

## JP7 — Vcore route (1×3)

<p align="center">
  <img src="../img/jumpers/JP7.png" alt="JP7 Vcore route" width="70%">
</p>

`JP7` selects how the 0.8 V core rail from the `U2` LDO reaches the `R7` sense shunt and the chip.

| Link | Route | When to use |
|:----:|-------|-------------|
| **1‑2** | 0.8 V → `J8.8` (**FILTIN**) → **CW308 L‑C low‑pass filter** → back on `J8.5/6` → shunt | Side‑channel capture on the CW308 — the filter cleans the rail so the shunt sees the die, not supply noise |
| **2‑3** | 0.8 V → shunt, **direct** | Bench use off the CW308, or when you want the shortest supply path |

The core rail is trimmable (0.80 – 0.90 V) and must be **set to 0.800 V before the chip is
inserted** — the full power path, trim math and procedure are on
[Power & SCA](Power-and-SCA.md).

---

## J6 — S‑Sel & trigger‑input block (2×3)

<p align="center">
  <img src="../img/jumpers/J6.png" alt="J6 S-Sel and trigger-in" width="70%">
</p>

`J6` selects the source of PROACT `SSel_n` (pin 18) and can route the PROACT trigger input
(pin 23) to the CW308.

| Link on `J6` | Effect |
|--------------|--------|
| **1‑3** | MCP2210 GPIO4 drives `SSel_n` (pin 18) — SPI select from the bridge module |
| **3‑5** | CW308 GPIO3 (`J7.9`) drives `SSel_n` (pin 18) — SPI select from the ChipWhisperer |
| **5‑6** | CW308 GPIO3 drives `trigger_in` (pin 23) |

---

## J10 — trigger select (2×3)

<p align="center">
  <img src="../img/jumpers/J10.png" alt="J10 trigger select" width="70%">
</p>

Pin 4 of `J10` is the CW308 `GPIO4/TRIG` line (`J7.10`); jumper it to one PROACT trigger source.

| Link on `J10` | Trigger source → CW308 `TRIG` |
|---------------|-------------------------------|
| **2‑4** | `trigger_Out` — normal trigger (pin 14) |
| **3‑4** | `out_pins[1]` — config trigger (pin 13) |
| **4‑6** | `out_pins[8]` — reserve / software trigger (pin 27) |

---

## J12 — clock source select (2×3)

<p align="center">
  <img src="../img/jumpers/J12.png" alt="J12 clock select" width="70%">
</p>

`J12` selects the source of the chip clock `SYSCLK_P` (pin 9), which is **always echoed to the
ChipWhisperer on `J7.5`** for synchronous sampling — full detail on
[Clock System](Clock-System.md).

| Source | J12 link | Path |
|--------|:--------:|------|
| **On‑board 50 MHz** | **3‑4** | `Y1` oscillator → `R22` 20 Ω → chip clock |
| **External input** | **1‑2** | `J11` coaxial jack → chip clock. *The fitted connector is **SMA** (the board silk reads "BNC").* |
| **ChipWhisperer** | **5‑6** | CW clock arriving on `J7.3` (CW308 `CLKFB` line) → `R8` 100 Ω → chip clock |

**Clock echo — synchronous sampling.** The chip clock is **permanently wired to `J7.5`**, so the
ChipWhisperer side can always observe the running clock and lock its sampling to it — whatever the
selected source, with no extra jumper.

> ⚠️ **Rules**
> - Fit **exactly one** source link on `J12` (`1‑2`, `3‑4` *or* `5‑6`).
> - Leave the **CW308's `J3` clock jumper unpopulated** — it drives `J7.5` (the clock‑echo pin)
>   and would fight the selected source.

---

## Switches

### S1 DIP switch (read‑back & power enables)

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

The read‑back mechanism is explained on [USB Bridge Modules](USB-Bridge-Modules.md). During
side‑channel capture, `S1‑6`/`S1‑7` stay **off** so the bridges are idle and unpowered
([CW308 Setup](CW308-Setup.md)).

### SW1 — reset button

`SW1` is the tactile push‑button on `B_RST_N` (pin 1), which carries an `R9` 10 k pull‑up to
`VDDIO`. Pressing it asserts the reset (line low) — the red LED **D7** lights while it is held.
With `JP1` closed, the same line is also driven by the CW308 `nRST`.

---

## LEDs

Ten on‑board LEDs in **four color groups**:

| LED(s) | Color | Signal (chip pin) | Lights when |
|:------:|-------|-------------------|-------------|
| **D1** | 🟢 yellow‑green | `out_pins[7]` — alive (16) | the heartbeat output is high — **blinking = chip alive** |
| **D2 / D4 / D5 / D6** | 🟡 yellow | `out_pins[0]/[5]/[6]/[11]` (6 / 11 / 12 / 21) | the debug output is **high** |
| **D3** | 🟠 orange | `spare_io` (10) | **you** drive the spare input high (it is an input — the chip never lights it) |
| **D7 / D8 / D9 / D10** | 🔴 red | `B_RST_N` (1) / `C_RST_N` (5) / `spi_global_RST_N` (4) / `spi_c_RST_N` (20) | the reset is **asserted** (line low) |

**Polarity, spelled out:**

- The **debug LEDs** (D1–D6 group, including the alive LED) are wired from the signal through the
  LED to ground — they light when the output is driven **high**.
- The **reset LEDs** (D7–D10) are wired the other way round — from `VDDIO` through the LED into the
  reset line — so they light **while the reset is asserted** (line **low**).

> 💡 **All four red LEDs on at once is normal during reset — it is not a fault.** They are wired
> from `VDDIO` through the LED into the reset line, so a low (active) reset lights them.

> 🏷️ **Matching the board print:** the silkscreen labels the LEDs as `D1` "Alive?", `D2` "Mem[23]", `D3` "Spare In", `D4` "UART Rvalid", `D5` "Mem req", `D6` "Co req"; the reset LED block reads `B RST` · `SPI RST` · `C RST` · `G RST`.

**CW308 motherboard status LEDs** (driven by the reset lines through `J8.18/19/20`):

| CW308 LED | Signal |
|:---------:|--------|
| **LED1** | `spi_c_RST_N` — SPI reset |
| **LED2** | `C_RST_N` — controller reset |
| **LED3** | `spi_global_RST_N` — global reset |

---

**See also:** [Clock System](Clock-System.md) (J12 in context) ·
[Power & SCA](Power-and-SCA.md) (JP7, Vcore trim and measurement) ·
[CW308 Setup](CW308-Setup.md) (which jumper goes where for capture) ·
[USB Bridge Modules](USB-Bridge-Modules.md) (the M side of every routing jumper) ·
[Chip Pinout](Chip-Pinout.md) (every signal at the chip).
