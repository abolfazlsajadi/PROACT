# PROACT — Bring-up & Usage Guide

End-to-end flow for running cryptographic experiments and side-channel captures
on the PROACT chip (ASIC on CW308, or the CW305/PYNQ FPGA — the software is
identical). A runnable, section-by-section notebook version of this complete
flow is provided in `examples/PROACT_Tutorial.ipynb`.

## 0. Prerequisites

- **Firmware toolchain:** `riscv32-unknown-elf-gcc` + `srec_cat`. Build:
  ```
  make -C Software/Controller     # -> main.vmem (one combined text+data image)
  make -C Software/SW_RV           # -> sw_rv_imem.vmem, sw_rv_dmem.vmem
  ```
- **Host environment:** `bash tools/setup_env.sh` (once) builds the dedicated
  `~/.proact-venv` with all required packages (pyserial, hid, mcp2210,
  PyQt6; chipwhisperer for capture). Always launch through `./run_cli.sh` /
  `./run_gui.sh` — these scripts select that virtual environment (or a system
  Python that provides the required packages); a bare `python3` may resolve to
  a different pyenv version that lacks them.
- **Device permissions:** `sudo bash tools/install_udev.sh` (once), then unplug
  and replug the USB devices. After that, **never run the GUI/CLI with sudo** —
  the udev rules make elevated privileges unnecessary, and the root user's
  Python environment does not include the user-installed chipwhisperer package,
  so sudo prevents capture from working.
- **Bench constants:** confirm that the values in
  `Software/Python/proact_host/config.py` (MCP serials, input clock, GPIO reset
  pins) match the board in use.

## 1. Program the controller (MCP2210 SPI)

The SPI loader streams 64-bit `{addr, data}` frames into the controller memory,
driving the reset lines. Hold reset → load → release to run.

```
./run_cli.sh program --vmem Software/Controller/main.vmem
```
(`main.vmem` already contains both `.text` and `.data`/`.rodata` at their
absolute addresses; the SPI loader routes each word to Imem or data RAM by its
address, so this single file programs the entire controller.)

## 2. Communicate with the controller (MCP2200 UART)

```
./run_cli.sh selftest        # runs all 4 cores' self-test
./run_cli.sh run --core aes1 \
    --key 000102030405060708090a0b0c0d0e0f --pt 00112233445566778899aabbccddeeff
```
The protocol is defined in `proact_host/regs.py` / `Software/Controller/main.c`.
Binary results are returned as `0xA5 <mode> <len> <payload>` frames.

> **Linux gotcha — no reply right after programming.** The MCP2200 re-enumerates
> to a new `/dev/ttyACM*` every time the controller is programmed. If
> ModemManager is running it probes that fresh port for ~15–20 s and the
> controller's replies are lost (`no frame marker`). The udev rules now set
> `ID_MM_DEVICE_IGNORE` on the bench devices — reinstall with
> `sudo bash tools/install_udev.sh` (then replug), or `sudo systemctl stop
> ModemManager` as a one-off. Re-detect the port after programming; it changes.

### AEAD (ASCON / Xoodyak): hardware encrypt → software decrypt

The AEAD co-processors implement **hardware encryption with software decryption
on the host** — a deliberate design choice (see `docs/hardware_hazards.md` R10);
a hardware decrypt attempt hits the firmware's bounded timeout and returns
zeros. AES1/AES2 decrypt in hardware as normal.
The supported AEAD round-trip is: encrypt on the chip, then decrypt and verify
the tag in software with `proact_host/aead_soft.py` (bit-exact ASCON-128 v1.2 /
Xoodyak v2, validated against the same reference vectors the silicon
reproduces; not constant-time — intended for validation, not production keys):
```python
from proact_host.aead_soft import ascon128_decrypt   # or xoodyak_decrypt
pt = ascon128_decrypt(key, nonce, ad, ct, tag)       # None => tag mismatch
```
The firmware can also run the reference vectors on-chip:
`ProactTarget.aead_kat()` (`CMD_AEADKAT`, `proact_aead_kat.c`) returns
`(xoodyak_ok, ascon_ok)` — both PASS on hardware.

## 3. Software AES on the Sw-RV target

Load the target images, then run blocks through the mailbox (one command word,
no per-word handshake):
```
./run_cli.sh load-swrv \
    --imem Software/SW_RV/sw_rv_imem.vmem --dmem Software/SW_RV/sw_rv_dmem.vmem \
    --key 000102...  --pt 001122...
```
Selecting `swrv` also routes the Sw-RV trigger (status bit31) to the scope
(control `cfg_sel = 101`); the target raises it around its AES.

## 4. Side-channel capture (ChipWhisperer)

```python
from proact_host.capture import ChipWhispererCapture
from proact_host.transport import ProactTarget, UartTransport
cap = ChipWhispererCapture().connect()
with UartTransport().open() as t:
    tgt = ProactTarget(t); tgt.select("aes1"); tgt.set_key(k); tgt.set_plaintext(p)
    cap.scope.arm(); tgt.run(); trace = cap.capture()
```
The chosen core's own trigger drives `trigger_Out`; the timer counts only during
that window (read it with the `TIME` command / `tgt.get_timer()`).

## 5. Measurements (thesis)

```
# headline: streamed load vs old per-word-ACK
~/.proact-venv/bin/python Software/Python/measure.py load --imem Software/Controller/main.vmem
# hardware cores vs software AES on the Sw-RV
~/.proact-venv/bin/python Software/Python/measure.py aes --n 200
```
(`measure.py` has no wrapper script — use the venv's Python directly, as above.)

## 6. Verify everything against the frozen RTL

```
RTL_ROOT=/path/to/ASIC/rtl bash tools/verify_all.sh
```
Builds both firmwares, cross-checks `proact_regs.h` against `ASIC/rtl`, simulates
the AES driver sequence against the real core (known-answer test), and runs the
host-protocol checks.

## 7. One unified on-chip self-check (A–Z)

`proact_host/fullcheck.py` `run_full_check()` provides the complete A-to-Z
check of a connected chip: UART link + baud, AES1/AES2 encrypt KAT + decrypt
round-trip, ASCON/Xoodyak on-chip encrypt KAT + software decrypt round-trip,
timer, control write, PRNG, Sw-RV software AES, and — with a scope connected —
clock lock and a real trace capture. The GUI's **Self-Check (A–Z)** tab runs
the same sequence (the ChipWhisperer tab's single button leads there; there is
no separate scope self-check). The sequence passes 100% on the real CW305 and
is reusable for ASIC chip screening.

## Golden rules (see docs/hardware_hazards.md)

- Never access `0x10007000` (Co_re), a disabled crypto core, or an idle UART — each hangs the CPU.
- Enable a core before touching its registers; one core at a time.
- The capture trigger is control **bit 30** (`0x40000000`), never bit 31.
- Do not lower the UART baud without raising `UART_TX_PACING_ITERS` in the firmware.
- ASCON/Xoodyak AEAD: encrypt in hardware; decrypt and verify the tag in
  software (`proact_host/aead_soft.py`, rule R10).
- Never launch with sudo — use `./run_cli.sh` / `./run_gui.sh` (udev rules
  provide device access).
