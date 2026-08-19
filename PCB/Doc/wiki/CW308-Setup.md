*Part of the PROACT board docs — start at [Home](Home.md).*

# ChipWhisperer CW308 setup

The PROACT board is a **CW308 UFO** target: it plugs into the ChipWhisperer CW308 baseboard,
which supplies power, carries UART/SPI/GPIO to the capture hardware, and measures the core
current through the on‑board shunt. This page covers mounting, the CW308 motherboard settings,
the ChipWhisperer software notes, the board‑side capture checklist, and the three typical
configurations.

---

## Mounting on the CW308 UFO

The board mates with the CW308's three 20‑pin target sockets through the female edge connectors
on its bottom side — seat it so each connector lands on its matching CW308 header:

| Board connector | Type | CW308 side | Carries |
|-----------------|------|------------|---------|
| **J7** | 1×20 female | **West** | clock · UART · SPI · GPIO · nRST |
| **J8** | 1×20 female | **East** | power rails · shunt sense · filter loop · status LEDs |
| **J9** | 1×20 female | **South** | debug taps · SPI · nRST |

![PROACT board — bottom, CW308 edge connectors](../img/board_final_bottom.png)

The clock pins on `J7` (`CLKIN` on `J7.5`, `CLKFB` on `J7.3`) are detailed on
[Clock System](Clock-System.md); the power and shunt‑sense pins on `J8` are detailed on
[Power and SCA](Power-and-SCA.md).

> ⚠️ **Before the chip ever meets the board:** trim `Vcore` to **0.800 V** with the chip out of
> socket `U1` — the full 6‑step procedure is in [Getting Started](Getting-Started.md).

---

## CW308 motherboard settings for this board

| CW308 control | Set to | Why |
|---------------|--------|-----|
| **3.3 V rail** | On | Supplies `VDDIO` via `J8.14`. |
| **Filter input** | **Victim‑supplied (`FILTIN`)** | With `JP7` = 1‑2 the 0.8 V rail from `U2` goes through the CW308 L‑C filter and back. Do **not** drive it from `VADJ`. With `JP7` = 2‑3 the filter is out of the loop. |
| **`J3` clock jumper** | **Unpopulated** | `J3` drives `J7.5`, which carries the board's **clock echo** — fitting it would fight the selected clock source. The CW‑as‑source option is `J12` 5‑6, not `J3`. |
| **`VREF`** | From victim | Uses this board's `VDDIO` (3.3 V, `J7.20`) as the level reference. |
| **MEAS SMA → Capture** | Connect | Feeds the shunt voltage into the ChipWhisperer ADC (or a scope). |

---

## ChipWhisperer software notes

- **UART direction is mirrored vs. the CW default:** the chip's `TX` arrives on **TIO2** and the
  chip's `RX` is driven from **TIO1** — configure `tio1 = serial_tx`, `tio2 = serial_rx`.
- **Synchronous sampling:** the chip clock is always available to the ChipWhisperer side on
  `J7.5` — sample from it for phase‑locked traces.

---

## Board‑side settings for capture

- `JP3` `JP5` `JP4` `JP6` → **CW** (1‑2) so UART/SPI come from the ChipWhisperer.
- `J6` → 3‑5 (CW308 GPIO3 drives `SSel_n`); add 5‑6 to drive `trigger_in` instead/as needed.
- `J10` → pick the trigger fed to `TRIG` (**2‑4** normal · **3‑4** config · **4‑6** reserve).
- `J12` → pick the clock source (see [Clock System](Clock-System.md)).
- `JP7` → **1‑2** (filtered) for capture.
- `JP1` → closed if you want the CW308 `nRST` to reset the chip.
- `S1‑6` / `S1‑7` → off (the USB bridges stay idle and unpowered during capture).
- **Precondition:** `Vcore` already trimmed to 0.800 V.

---

## Typical configurations

**A. USB bench bring‑up (talk to PROACT from a PC, no ChipWhisperer)**

| Setting | Value |
|---------|-------|
| **Precondition** | `Vcore` trimmed to 0.800 V ([procedure](Getting-Started.md)) |
| `S1‑6`, `S1‑7` | **on** (power both bridge modules) |
| `S1‑1…5` | on as needed for GPIO read‑back |
| `JP3`, `JP5`, `JP4`, `JP6` | **M** (2‑3) |
| `J6` | **1‑3** (MCP2210 GPIO4 → `SSel_n`) |
| `J12` | **3‑4** (on‑board 50 MHz) |
| `JP7` | **2‑3** (direct) |
| `JP1` | open (reset via `SW1`) |
| `VDDIO` | feed 3.3 V into the `VDDIO` test point |

**B. ChipWhisperer capture, CW‑clocked**

| Setting | Value |
|---------|-------|
| Mount board on the **CW308** | — |
| `JP3`, `JP5`, `JP4`, `JP6` | **CW** (1‑2) |
| `J6` | **3‑5** (CW GPIO3 → `SSel_n`) |
| `J10` | choose trigger: **2‑4** normal · **3‑4** config · **4‑6** reserve |
| `J12` | **5‑6** (clock from the ChipWhisperer, via `J7.3`/`R8`) |
| CW308 `J3` | **unpopulated** |
| `JP7` | **1‑2** (through the CW308 filter) |
| `JP1` | closed (reset from CW308 `nRST`) if desired |
| `S1‑6`, `S1‑7` | off |

**C. Synchronous capture from the on‑board oscillator** *(flagship SCA setup)*

| Setting | Value |
|---------|-------|
| As configuration **B**, except: | |
| `J12` | **3‑4** (the on‑board `Y1` clocks the chip) |
| Scope clock | sample from the clock echoed on `J7.5` for phase‑locked traces |

---

## See also

- [Clock System](Clock-System.md) — the `J12` source links, the permanent clock echo on `J7.5`
  and the `CLKIN`/`CLKFB` paths on `J7`.
- [Power and SCA](Power-and-SCA.md) — `JP7` filtered vs. direct routing, the `R7` shunt and the
  measurement chain.
- [Jumpers, Switches & LEDs](Jumpers-Switches-LEDs.md) — every jumper position table in one place.
- [USB Bridge Modules](USB-Bridge-Modules.md) — the module side used in configuration A.
- [Getting Started](Getting-Started.md) — the full Vcore trim procedure and first power‑up.
