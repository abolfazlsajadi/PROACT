# PROACT — Hardware Behavior and Software Access Rules

PROACT is an area-optimized side-channel research SoC: the bus and
peripherals are deliberately kept minimal so the silicon stays small and the
power signatures stay clean. As a consequence of this efficient design, a few
peripherals expect the software to follow simple, well-defined access rules — a
discipline standard for bare-metal embedded systems. **The drivers and the
`proact_host` library in this repository already implement every rule below, so
these details require no direct handling in normal use.** This page documents
them so the behavior is fully understood, and so that any extension of the
firmware keeps to the same conventions.

> The hardware is fabricated and frozen. These are permanent characteristics of
> the design (identical on the ASIC and the FPGA). They are documented and
> accommodated in software — the software follows the hardware.

## R1. The bus implements no access watchdog

To save area, the shared bus does not implement an access watchdog/timeout. A
peripheral answers each access when its data is ready. If firmware addresses
something that is intentionally inactive (a disabled core, an unimplemented
slot, or the UART receive path when no byte has arrived), the access simply
waits for a reply. Rules R2–R4 list the conditions that must be satisfied
before each such access, and the drivers enforce them automatically.

An access to a *completely unmapped* address (e.g. `0x10004000`) returns a clean
bus decode response — a normal error, handled gracefully.

## R2. Enable a crypto core before accessing it (reset gating)

Each crypto core is held in reset while its `ENABLE` bit is 0
(`rst_n = rst_Gsys_n & enable_<core>`) — a clean, deterministic power-gating
scheme that keeps an unused core quiet (beneficial for trace quality and power
consumption).

**Software rule:** set the core's `ENABLE` bit, then access its registers; run
one core at a time; clear the enable when done. `aes_run()` / `aead_run()` do
this in the proven order (the same order that resolves the known
"second experiment" case: de-assert `START` first, then drop `ENABLE`).

## R3. Address `0x10007000` (`Co_re`) is a reserved expansion slot

`Co_re` is an address slot reserved for a future co-processor; no core is
attached in this revision. **Software rule:** do not access it. The
register header marks it `PROACT_CORE_BASE_DO_NOT_USE` for clarity.

## R4. UART: use the status-register handshake

The UART is a compact FIFO design. Three conventions:

1. **Writes are accepted at offsets `0x00` (TX data) and `0x04` (baud).** Use the
   provided `putchar()` / `uart_set_baud_divisor()`.
2. **Receiving uses the status handshake.** The hardware hardwires the UART
   "**RX FIFO not-empty**" signal (`~rx_empty`) to **status-register bit 0**
   (`STAT_UART_RVALID` at `0x20000000`). Poll that bit (the S_C_REG always
   answers immediately) to know a byte is waiting, then read the UART at
   `0x10000000`:
   ```c
   while ((proact_read32(0x20000000) & STAT_UART_RVALID) == 0) { }  /* wait: 1 = RX not empty */
   byte = proact_read32(0x10000000);                                 /* read the byte */
   ```
   `uart_getchar()` does exactly this. (The status-bit handshake is the intended
   receive path; it lets firmware determine unambiguously whether a byte is
   present.)
3. **TX FIFO depth is 512 bytes.** Sustained output faster than the serial line
   drains it fills the FIFO; the paced `putchar()` keeps output within the line
   rate so nothing is lost. If the baud rate is lowered, scale the pacing
   accordingly.

## R5. Control register width (31 bits) and the trigger bit

The control register carries 31 defined bits. The 32-bit CPU write is taken as
the low 31 bits, so the **global trigger is control-register bit 30
(`0x40000000`)** (`s_c_REG.sv`: `trigger_o = control_reg.trigger | …`, and
`control_reg.trigger` is the MSB of the 31-bit `control_reg_bits_t`) — confirmed
by the hardware design team. `triggerpc` = bit 29; the trigger-source select
`cfgsel` = bits [22:20]. The firmware constant `CTRL_TRIGGER` already uses bit 30.

The **status** register is a full 32-bit register; **status bit 31** is the live
Sw-RV "target done" / Sw-RV trigger source (a separate signal from the control
side). `STAT_UART_RVALID` is status bit 0 (R4).

## R6. Trigger and timer behavior

- `trigger_Out` (chip pin) is the OR of the software trigger and each core's own
  trigger, routed through the `cfgsel` [22:20] mux so that the core driving the
  scope can be selected. The firmware sets `cfgsel` to match the running core.
- The **timer counts while the trigger is high**, so it measures the exact
  operation window in clock cycles — suitable for comparing per-core throughput.
- **AES cores** assert their trigger when `START` is received and hold it until
  the operation finishes (~11 clock cycles per block).
- **ASCON / Xoodyak** offer a fine-grained in-core `triggercfg` field (LEN[23:16])
  that brackets a chosen phase (key / nonce / AD / plaintext) — see
  [address_map.md](address_map.md) §5 and the manual's crypto chapter for the
  full activation/deactivation table.

## R7. RNG delivery path

The LFSR RNG is seeded and enabled by the controller (write the seed to
`0x80000000` with `ENABLE_RNG` set). Its random output is delivered to the
**Sw-RV target** core (the consumer of randomness), not read back by the
controller — a clean producer/consumer split. The controller retrieves any
result it needs through the Sw-RV data-memory mailbox.

## R8. Sw-RV data-port addressing

The Sw-RV core's data side decodes address bits directly (a compact, fast
scheme): `addr[30]` read → an RNG word; `addr[29]` write → the status feedback
field; `addr[27]` selects the data-memory write; otherwise its 128 KB data RAM.
Keep target-firmware pointers in the intended windows.

## R9. Control register is write-oriented (shadow copy)

Control-register writes set the whole word (there is no hardware read path that
merges bits), so firmware keeps a small **shadow copy**, updates it with the
`CTRL_*` bit macros, and flushes it — the `proact_ctrl` driver handles this.
One shadow, one writer per binary.

## R10. ASCON / Xoodyak are encrypt-only in hardware (decrypt in software)

The frozen AEAD wrapper implements the encrypt direction only. Three
independent reasons in the RTL:

1. the wrapper's output router writes the CT FIFO only for ciphertext output
   words (`bdo_type = HDR_CT`), so on decrypt the core's recovered plaintext
   (`HDR_PT`) is dropped;
2. there is no bus path to feed the received tag into the core, so the
   `VERIFY_TAG` phase stalls and `done` never asserts;
3. `tag_ok` is hardwired to 1, so even a completed decrypt could not report a
   tag failure.

A hardware decrypt attempt therefore runs into the firmware's bounded timeout
and returns zeros — deterministic, no hang. (AES1/AES2 are not AEAD cores;
they encrypt **and** decrypt fully in hardware.)

**Software rule:** hardware encrypt → software decrypt.
`proact_host/aead_soft.py` is a pure-Python, dependency-free, bit-exact
ASCON-128 v1.2 / Xoodyak v2 (NIST LWC final round, nonce absorbed with the key
— matching the GMU core in silicon), validated against the exact reference
vectors the silicon reproduces. `ascon128_decrypt()` / `xoodyak_decrypt()`
verify the tag and return `None` on a mismatch (the AEAD contract). It is not
constant-time — use it for validation and experiments, not production keys.

---

**Summary:** PROACT's peripherals are compact and efficient, and the software
layer in this repository already encodes the small set of access conventions
they expect. Using the library APIs yields correct, hang-free behavior
automatically; the rules above serve as reference material for understanding the
design and for extending the firmware.
