"""
Reset-line control + readback over the MCP2210 GPIO, for the GUI's reset
controls and LED indicators. Wraps an Mcp2210Programmer (which owns the MCP and
the pin map). Polarity: output True = pin HIGH = released/active, output
False = pin LOW = held in reset; readback reflects the actual reset net
(bench-verified: each output maps 1:1 to its readback pin, no inversion).

Reset lines (config.Mcp2210Pins):
  controller  -> C_RST_N          CTRL_RV core reset
  global      -> spi_global_RST_N Sw-RV target + all crypto co-processors
  spi         -> spi_c_RST_N      SPI code-loader core (programming only)
  spi_select  -> SPI chip-select (also a controllable line)

THE ONE KNOWN-GOOD RUNNING STATE (hardware-verified, matches programmer.py's
release sequence): controller=True, global=True, spi=False, spi_select=False.
The SPI loader must stay HELD in reset while the chip runs.

SAFETY RULE (hardware-verified): never assert `global` while the controller
CPU is running -- the CPU's next crypto access touches a core that is in
reset, the bus never acks, and the chip wedges beyond software recovery
(only an FPGA reprogram brings it back). Every mode here that asserts
`global` therefore holds the controller FIRST, and `run` releases the
crypto BEFORE rebooting the controller. All modes set ALL FOUR lines, so
the result never depends on what a previous click left behind.
"""
import time
from typing import Dict, Optional

_LINES = ("controller", "global", "spi", "spi_select")


class ResetController:
    def __init__(self, programmer):
        self.prog = programmer  # Mcp2210Programmer (already .open()ed)

    def _out_pin(self, name: str) -> int:
        p = self.prog.pins
        return {"controller": p.controller_reset, "global": p.global_reset,
                "spi": p.spi_reset, "spi_select": p.spi_select}[name]

    def _in_pin(self, name: str) -> int:
        p = self.prog.pins
        return {"controller": p.read_controller_reset, "global": p.read_global_reset,
                "spi": p.read_spi_reset, "spi_select": p.read_spi_select}[name]

    # --- control (True = released/active, False = held in reset) ---
    def set(self, name: str, released: bool):
        self.prog.mcp.set_gpio_output_value(self._out_pin(name), bool(released))

    def assert_reset(self, name: str):
        self.set(name, False)

    def release(self, name: str):
        self.set(name, True)

    # --- readback ---
    def get(self, name: str) -> Optional[bool]:
        try:
            return bool(self.prog.mcp.get_gpio_value(self._in_pin(name)))
        except Exception:  # noqa: BLE001
            return None

    def status(self) -> Dict[str, Optional[bool]]:
        """{line: True(active) / False(in reset) / None(unreadable)}."""
        return {n: self.get(n) for n in _LINES}

    def try_status(self) -> Optional[Dict[str, Optional[bool]]]:
        """status(), but returns None immediately if the MCP is busy (another
        thread holds the lock, e.g. mid-programming). For UI pollers: skip the
        update instead of queueing behind a long SPI load."""
        lock = getattr(self.prog, "lock", None)
        if lock is None:
            return self.status()
        if not lock.acquire(blocking=False):
            return None
        try:
            return self.status()
        finally:
            lock.release()

    # --- named reset modes: each sets a COMPLETE, safe 4-line state ---------
    def _apply(self, controller: bool, glob: bool, spi: bool, spi_select: bool):
        self.set("spi", spi)
        self.set("spi_select", spi_select)
        self.set("global", glob)
        self.set("controller", controller)

    def apply_mode(self, mode: str):
        """mode in: run | controller | global | spi | reset_all.
        (Old names 'none' and 'default_program' are accepted as aliases.)

        Every mode drives all four lines to a deterministic state, in a safe
        order -- see the SAFETY RULE in the module docstring.
        """
        if mode in ("run", "none"):
            # Return to THE running state, recoverably: hold the CPU, put the
            # loader/CS/crypto lines in the run state, then reboot the CPU
            # last so it boots with the crypto already released.
            self.set("controller", False)
            self.set("spi", False)
            self.set("spi_select", False)
            self.set("global", True)
            time.sleep(0.05)
            self.set("controller", True)
        elif mode == "controller":
            # Hold the CPU only; crypto stays released; loader stays held.
            self._apply(controller=False, glob=True, spi=False, spi_select=False)
        elif mode == "global":
            # Hold Sw-RV + crypto. The controller MUST be held first --
            # asserting global under a running CPU wedges the bus for good.
            self.set("controller", False)
            self.set("spi", False)
            self.set("spi_select", False)
            time.sleep(0.05)
            self.set("global", False)
        elif mode == "spi":
            # Release the SPI loader (programming-style). Only safe with the
            # CPU and crypto held: the loader writes controller RAM directly.
            self.set("controller", False)
            self.set("global", False)
            self.set("spi_select", False)
            time.sleep(0.05)
            self.set("spi", True)
        elif mode in ("reset_all", "default_program"):
            # Baseline: everything held low (the pre-programming pulse).
            self._apply(controller=False, glob=False, spi=False, spi_select=False)
        else:
            raise ValueError("unknown reset mode: %s" % mode)
