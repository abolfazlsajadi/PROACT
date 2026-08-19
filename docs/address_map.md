# PROACT — Address & Register Map (canonical)

> **Single source of truth.** All values in this document are taken directly from the **frozen, fabricated** ASIC RTL
> (`ASIC/rtl/PROACTPKG/config_defs.svh`, `ASIC/rtl/SCreg/s_c_REG_pkg.sv` + `s_c_REG.sv`) and verified against
> the signoff netlist `PROACT_signoff.v` (tapeout 2025-11-14). The chip is fabricated; this map is fixed.
> The corresponding firmware header is [`Software/common/proact_regs.h`](../Software/common/).
> The ASIC and FPGA share this map **byte-for-byte** — there is no address divergence.

## 1. Bus devices

| idx | Device | Base | Mask | Region | Notes |
|----:|--------|------|------|--------|-------|
| 0 | RAM | `0x02000000` | `0xFE000000` | 32 MB | Controller data RAM (128 KB phys). SPI port-B write when addr[23]=1 |
| 1 | RII_IMEM | `0x04000000` | `0xFE000000` | 32 MB | Sw-RV **instruction** memory write port (controller loads code here) |
| 2 | RII_DMEM | `0x08000000` | `0xF8000000` | 128 MB | Sw-RV **data** memory; the controller↔target **mailbox** lives here |
| 3 | S_C_REG | `0x20000000` | `0xFFFFFC00` | 1 KB | **WRITE = control, READ = status** (same address) |
| 4 | UART | `0x10000000` | `0xFFFFFC00` | 1 KB | AHBUART |
| 5 | TIMER | `0x40000000` | `0xFFFFFC00` | 1 KB | 32-bit counter |
| 6 | RNG | `0x80000000` | `0xFFFFFC00` | 1 KB | Write = seed. **Read routed only to Sw-RV**, not the controller |
| 7 | AES1 | `0x10001000` | `0xFFFFFC00` | 1 KB | AES-128 hardware core |
| 8 | AES2 | `0x10002000` | `0xFFFFFC00` | 1 KB | AES-128 hardware core (2nd) |
| 9 | Xoodyak | `0x10003000` | `0xFFFFFC00` | 1 KB | Xoodyak AEAD core |
| 10 | ASCON | `0x10005000` | `0xFFFFFC00` | 1 KB | ASCON AEAD core |
| 11 | **Co_re** | `0x10007000` | `0xFFFFFC00` | 1 KB | **No hardware instance — any access hangs the CPU. Do not access this region.** |

`0x10004000` and `0x10006000` are **unmapped** → clean bus decode error (**not** a hang — different from Co_re).

## 2. Control register — `0x20000000` (WRITE)

> [!WARNING]
> **The control register is 31 bits wide, not 32.** `s_c_REG.sv:103` casts the 32-bit CPU write to the
> 31-bit `control_reg_bits_t` type (`reserved[13:0]` = 14 bits), so **data bit 31 is truncated (dropped)
> by the hardware** and a `0x80000000` write has no effect on silicon. The capture **trigger is bit 30
> (`0x40000000`)**: `s_c_REG.sv` drives `trigger_o = control_reg.trigger | …`, and `control_reg.trigger`
> is the MSB of the 31-bit structure. This was confirmed by the hardware design team (2026-07-21). The
> design team's diagram labels the trigger "bit 31" as the intended layout, but the fabricated hardware
> uses **bit 30**; the firmware macro `CTRL_TRIGGER` already writes bit 30.

| Bit | Field | Bit | Field |
|----:|-------|----:|-------|
| 0 | `ENABLE_TARGET` (Sw-RV out of reset) | 8 | `START_XOODYAK` |
| 1 | `ENABLE_AES1` | 9 | `DEC_XOODYAK` |
| 2 | `START_AES1` | 10 | `ENABLE_ASCON` |
| 3 | `DEC_AES1` (1=decrypt) | 11 | `START_ASCON` |
| 4 | `ENABLE_AES2` | 12 | `DEC_ASCON` |
| 5 | `START_AES2` | 13 | `ENABLE_RNG` |
| 6 | `DEC_AES2` | 14 | `ENABLE_TIMER` |
| 7 | `ENABLE_XOODYAK` | 15 | `RESET_UART` (active-high) |

- **[22:20] `CFGSEL`** — trigger-source mux: `000`=software, `001`=ASCON, `010`=AES1, `011`=AES2, `100`=Xoodyak, `101`=Sw-RV. Firmware normally leaves this `000`.
- **bit 29** = `TRIGGERPC` (`0x20000000`)
- **bit 30** = `TRIGGER` (`0x40000000`) — the capture trigger.

## 3. Status register — `0x20000000` (READ)

> The status register **is** a full 32-bit register (unlike the control side). Bits [31:17] are written by the
> Sw-RV target via port B; **status bit 31 is the live "target done" handshake** the controller polls. This is a
> *different bit* from control-side bit 31, which the hardware truncates (Section 2) — the two must not be conflated.

| Bit | Field | Bit | Field |
|----:|-------|----:|-------|
| 0 | `UART_RVALID` | 9 | `DONE_ASCON` |
| 1 | `TRIGGER_IN` | 10 | `READY_ASCON` |
| 2 | `DONE_AES1` | 11 | `TRIGGER_ASCON` |
| 3 | `TRIGGER_AES1` | 12 | `TEST_I` (= spare_io handshake) |
| 4 | `DONE_AES2` | 13 | `AES1_ACTIVE` |
| 5 | `TRIGGER_AES2` | 14 | `AES2_ACTIVE` |
| 6 | `DONE_XOODYAK` | 15 | `XOODYAK_ACTIVE` |
| 7 | `READY_XOODYAK` | 16 | `ASCON_ACTIVE` |
| 8 | `TRIGGER_XOODYAK` | 31 | `TARGET_DONE` (written by Sw-RV) |

### 3.1 Register legend & key semantics (from the hardware design team's diagram)

Abbreviations used in the bit diagram: **E / S / De** = Enable / Start / Decrypt;
**T / R / D / A** = Trigger / Ready / Done / Active; **cfg / sel / F** =
Configurable / select / feedback.

- **Status bit 0 = "Uart Valid" = UART RX not-empty.** This is the hardware-hardwired
  `~rx_empty`. **Poll this bit** (read `0x20000000`, which always acks) to determine when a
  byte is waiting — then read the UART at `0x10000000`. **Do not probe the UART itself to
  check**: every UART read only acks when RX is non-empty, so it hangs when empty
  (hazard H4). This is the correct receive handshake and is what `uart_getchar()` uses.
- **Status bits [30:17] are Sw-RV → Controller feedback flags** (labelled *"Target →
  Controller status (Not Hardwire)"*): the target core writes them (E/S/De feedback per
  core, RNG feedback, …). Bit 31 = the Sw-RV target trigger/done. The controller only
  reads them; they are not driven by the crypto hardware directly.
- **A diagram of the full control/status bit map** (the hardware design team's colour
  diagram) may be placed at `docs/images/register_map.png`; the manual references this
  location.

> [!NOTE]
> The software capture trigger is control-register **bit 30 (`0x40000000`)**; the RTL derivation and the
> hardware design team's confirmation are documented in Section 2.

## 4. AES1 / AES2 core registers (offset from base)

AES-128. Reads **and** writes ack **only while the core's enable bit is set**. `DONE` is valid only while `START` is held high.

| Offset | Reg | Offset | Reg | Offset | Reg |
|-------:|-----|-------:|-----|-------:|-----|
| `0x00` | START (w bit0) | `0x08`–`0x14` | KEY[127:96 … 31:0] | `0x28`–`0x34` | RESULT[127:96 … 31:0] |
| `0x04` | DONE (r = ~busy) | `0x18`–`0x24` | DATA[127:96 … 31:0] | | |

## 5. ASCON / Xoodyak core registers (offset from base)

128-bit KEY and NPUB are shifted in as 4×32-bit writes. Reads always ack when the core is enabled even if the CT/TAG FIFO is empty (return stale data, **no hang** — unlike the UART).

| Offset | Reg | Notes |
|-------:|-----|-------|
| `0x00` | LEN | `{triggercfg[23:16], pt_len[15:8], ad_len[7:0]}` (MSB→LSB) — i.e. `(triggercfg<<16)|(pt_len<<8)|ad_len`, byte lengths; see [hardware_hazards.md](hardware_hazards.md) for `triggercfg` |
| `0x04` | KEY | 32b ×4 → 128b |
| `0x08` | NPUB | 32b ×4 → 128b |
| `0x0C` | AD | write → AD FIFO |
| `0x10` | PT | write → PT FIFO |
| `0x14` | CT | read ← CT FIFO |
| `0x18` | TAG | read ← TAG FIFO |

> [!WARNING]
> **ASCON/Xoodyak perform encryption only in hardware; decryption runs in software on the host — a
> deliberate design choice.** The `DEC_ASCON`/`DEC_XOODYAK` control bits reach the cores, but a hardware
> decrypt can never complete: the wrapper's output router writes the CT FIFO only for ciphertext output
> (recovered plaintext is dropped), there is no bus path to feed in the received tag (the core's tag-verify
> stage stalls, `DONE` never asserts), and `tag_ok` is hardwired to 1. The firmware's bounded timeout
> catches this and returns zeros. Perform decryption and tag verification in software
> (`proact_host/aead_soft.py`); see [hardware_hazards.md](hardware_hazards.md) rule R10. AES1/AES2
> decryption works fully in hardware.

## 6. UART / Timer / RNG

**UART** `0x10000000`: `+0x00` W=TX byte / R=RX byte; `+0x04` W=baud divisor (`HWDATA[21:0]`, reset default 27). Read of any offset ≠`0x00` → status `{6'b0, tx_full(b1), rx_empty(b0)}`. TX FIFO 512 B, RX FIFO 32 B. See hazards.

**Timer** `0x40000000`: single 32-bit counter (any offset). Enabled by `ENABLE_TIMER`; **counts only while `trigger_Out` is high** — it measures the trigger/crypto window, it is not a free-running timer.

**RNG** `0x80000000`: write = 32-bit seed (enabled by `ENABLE_RNG`). The **controller cannot read** random data here; the LFSR output is routed only to the Sw-RV at its data-side `addr[30]`.

---
*Bench-confirmed on the CW305 board (2026-07-21): the boot address (`0x00100000`) and the 50 MHz core clock / baud arithmetic (`config/hardware.json` `clock_hz_default`) — the controller boots and the full A–Z self-check passes. Still open: the RNG default seed (`0xACE1ACE1` in RTL vs the netlist parameter).*
