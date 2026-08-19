*Part of the PROACT board docs — start at [Home](Home.md).*

# Clock System

The chip clock `SYSCLK_P` (pin 9) can come from **three sources, selected on `J12`** (board silk:
`Clk select — SMA / Osc / Cw`), and is **always echoed to the ChipWhisperer on `J7.5`** so the
scope can sample synchronously with the target clock.
J12 pins **2/4/6** are commoned onto the chip clock net (board net `CLK_pin`), together with chip
pin 9, `J7.5` and the `CLK` test point — so whichever source you link on J12 lands directly on the
chip clock.

![Clock tree](../img/clock_tree.png)

## The three sources

| Source | J12 link | Path |
|--------|:--------:|------|
| **On‑board 50 MHz** | **3‑4** | `Y1` oscillator → `R22` 20 Ω → chip clock |
| **External input** | **1‑2** | `J11` coaxial jack → chip clock. *The fitted connector is **SMA** (the board silk reads "BNC").* |
| **ChipWhisperer** | **5‑6** | CW clock arriving on `J7.3` (CW308 `CLKFB` line) → `R8` 100 Ω → chip clock |

### On‑board oscillator — Y1 50 MHz

`Y1` is a 50 MHz clock oscillator (3225 footprint) powered from `VDDIO` and decoupled by
`C13` 1 µF + `C16` 100 nF. Its output passes through the series resistor **`R22` 20 Ω** to J12
pin 3; link **J12 3‑4** to make it the chip clock. Purchase note: fit an **active 3.3 V
oscillator** — verify the part number before ordering (see [BOM](BOM-and-Fab.md)).

### External input — J11

`J11` is the external clock input, feeding J12 pin 1; link **J12 1‑2** to make it the chip clock.
The fitted connector is an **SMA jack** — the board silk reads "BNC", so bring an SMA cable.

### ChipWhisperer as source

Link **J12 5‑6** to make the ChipWhisperer the chip clock: the CW clock arrives on `J7.3` (the
CW308 `CLKFB` line) and passes through **`R8` 100 Ω** to the chip clock net. Leave the **CW308's
`J3` clock jumper unpopulated** in this mode. See [CW308 Setup](CW308-Setup.md).

## Clock echo — J7.5

**Clock echo — synchronous sampling.** The chip clock is **permanently wired to `J7.5`**, so the
ChipWhisperer side can always observe the running clock and lock its sampling to it — whatever the
selected source, with no extra jumper.

<p align="center">
  <img src="../img/jumpers/J12.png" alt="J12 clock select" width="70%">
</p>

> ⚠️ **Rules**
> - Fit **exactly one** source link on `J12` (`1‑2`, `3‑4` *or* `5‑6`).
> - Leave the **CW308's `J3` clock jumper unpopulated** — it drives `J7.5` (the clock‑echo pin)
>   and would fight the selected source.
> - The `CLK` test point sits directly on the chip clock net for probing.

## Synchronous sampling recipe

This is **Typical configuration C** — synchronous capture from the on‑board oscillator, the
flagship SCA setup. It builds on configuration **B** (ChipWhisperer capture), described on the
[CW308 Setup](CW308-Setup.md) page.

| Setting | Value |
|---------|-------|
| As configuration **B**, except: | |
| `J12` | **3‑4** (the on‑board `Y1` clocks the chip) |
| Scope clock | sample from the clock echoed on `J7.5` for phase‑locked traces |

ChipWhisperer software note: the chip clock is always available to the ChipWhisperer side on
`J7.5` — set the scope to sample from it for phase‑locked traces.

---

**See also:** [Jumpers, Switches & LEDs](Jumpers-Switches-LEDs.md) (all J12 positions in context) ·
[CW308 Setup](CW308-Setup.md) (motherboard settings and configurations A/B/C) ·
[Power & SCA](Power-and-SCA.md) (the measurement side of a capture) ·
[Chip Pinout](Chip-Pinout.md) (`SYSCLK_P`, pin 9).
