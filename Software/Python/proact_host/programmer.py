"""
MCP2210 SPI code loader for the PROACT controller.

Streams a firmware .vmem into the controller memory as 64-bit
{addr[31:0], data[31:0]} frames (address in the upper word), MSB-first, and
drives the GPIO reset choreography. Adapted from the proven GUI spi.py +
codeController.py.

mcp2210/hid are imported lazily so this module can be imported without them.
"""
import threading
import time
from typing import Callable, Optional

from . import config
from .vmem import parse_vmem


class _LockedMcp:
    """Thread-safety wrapper around the Mcp2210 object.

    The mcp2210 library talks to one HID endpoint with strict command/response
    pairing; if two threads interleave commands (e.g. the GUI's 1 s indicator
    poll while a reset button runs on a worker thread) the response stream
    desynchronizes and every later call raises
    Mcp2210CommandResponseDesyncException. This proxy serializes ALL calls
    through one re-entrant lock, and if a desync still slips through (a stale
    response left over from a crashed call) it retries once, which re-aligns
    the stream. Non-callable attributes pass straight through.
    """

    def __init__(self, mcp):
        self._mcp = mcp
        self.lock = threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._mcp, name)
        if not callable(attr):
            return attr

        def call(*a, **k):
            with self.lock:
                try:
                    return attr(*a, **k)
                except Exception as e:  # noqa: BLE001
                    if "Desync" not in type(e).__name__:
                        raise
                    time.sleep(0.05)
                    return attr(*a, **k)  # one retry re-syncs cmd/response
        return call


class Mcp2210Programmer:
    def __init__(self, serial: Optional[str] = None, pins: config.Mcp2210Pins = None):
        self.serial = serial or config.MCP2210_SERIAL
        self.pins = pins or config.PINS
        self.mcp = None
        self.lock = None  # set on open(); shared by ResetController.try_status

    # --- connection ---
    def _detect(self) -> str:
        try:
            import hid
        except ImportError:
            raise ImportError(
                "The 'hidapi' library is missing. Install it with: "
                "pip install hidapi"
            ) from None

        # Enumerate ONLY the Microchip VID/PID: a full hid.enumerate() walk
        # touches every HID device on the host and hidapi can crash on
        # unrelated hardware (seen with ITE/Logitech nodes on the bench PC).
        try:
            for dev in hid.enumerate(config.MCP2210_VID, config.MCP2210_PID):
                if self.serial and dev.get("serial_number") != self.serial:
                    continue
                return dev.get("serial_number")
        except Exception:  # noqa: BLE001
            pass

        raise RuntimeError(
            "No MCP2210 SPI device found. Ensure the MCP2210 is connected via USB and "
            "that you have the necessary permissions (udev rules on Linux)."
        )

    def open(self):
        if self.mcp is not None:
            return self
        try:
            from mcp2210 import Mcp2210, Mcp2210GpioDesignation, Mcp2210GpioDirection
        except ImportError:
            raise ImportError(
                "The 'mcp2210' library is missing. Install it with: "
                "pip install mcp2210-python"
            ) from None
        self._Desig = Mcp2210GpioDesignation
        self._Dir = Mcp2210GpioDirection
        self.mcp = _LockedMcp(Mcp2210(self._detect()))
        self.lock = self.mcp.lock
        try:
            self._setup()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        """Release the HID handle once, without changing GPIO/reset state.

        mcp2210-python exposes its hidapi handle as `_hid` and has no public
        close method in the installed backend. Prefer a public close if a
        future backend provides it; otherwise close that verified handle.
        """
        mcp = self.mcp
        if mcp is None:
            return
        lock = self.lock or threading.RLock()
        with lock:
            if self.mcp is None:
                return
            self.mcp = None
            self.lock = None
            backend = mcp._mcp if isinstance(mcp, _LockedMcp) else mcp
            close = getattr(backend, "close", None)
            if close is None:
                close = getattr(getattr(backend, "_hid", None), "close", None)
            if close is not None:
                close()

    def _setup(self):
        p = self.pins
        self.mcp.configure_spi_timing(chip_select_to_data_delay=0,
                                      last_data_byte_to_cs=0, delay_between_bytes=0)
        self.mcp.set_spi_mode(1)
        for i in range(9):
            self.mcp.set_gpio_designation(i, self._Desig.GPIO)
        for out in (p.controller_reset, p.spi_reset, p.global_reset, p.spi_select):
            self.mcp.set_gpio_direction(out, self._Dir.OUTPUT)
        for inp in (p.read_controller_reset, p.read_spi_reset,
                    p.read_global_reset, p.read_spi_select):
            self.mcp.set_gpio_direction(inp, self._Dir.INPUT)

    # --- framing ---
    @staticmethod
    def _frame(address: int, data: int) -> bytes:
        """64-bit {address, data} MSB-first (address in the upper 32 bits)."""
        return ((address & 0xFFFFFFFF) << 32 | (data & 0xFFFFFFFF)).to_bytes(8, "big")

    def _reset_low(self):
        p = self.pins
        for pin in (p.controller_reset, p.spi_reset, p.global_reset, p.spi_select):
            self.mcp.set_gpio_output_value(pin, False)

    def program(self, vmem_path: str, progress: Optional[Callable[[int], None]] = None):
        """Reset, stream the vmem, then release the controller to run."""
        words = parse_vmem(vmem_path)
        if not words:
            raise ValueError("firmware contains no data words")
        from mcp2210 import Mcp2210GpioDesignation
        p = self.pins
        self._reset_low()
        time.sleep(0.1)
        # start SPI-load window
        self.mcp.set_gpio_output_value(p.spi_reset, True)
        time.sleep(0.2)
        self.mcp.set_gpio_designation(p.spi_select, Mcp2210GpioDesignation.CHIP_SELECT)

        total = len(words) or 1
        for i, (addr, data) in enumerate(words):
            self.mcp.spi_exchange(self._frame(addr, data), cs_pin_number=p.spi_select)
            if progress and (i % 256 == 0):
                progress(int(i * 100 / total))

        # Release to RUN. HARDWARE-VERIFIED sequence (AES KAT passes on the CW305):
        #   spi_reset    = False -> spi_c_RST_N low       -> SPI loader held in reset
        #   global_reset = True  -> spi_global_RST_N HIGH -> crypto subsystem RELEASED
        #                           (matches the testbench post-load state
        #                            spi_c_RST_N=0 / C_RST_N=1 / spi_global_RST_N=1)
        # then REBOOT the controller (pulse controller_reset) so the CPU starts
        # with the crypto already out of reset. Two things were essential and both
        # were missing before: global_reset must be HIGH (not low), and the CPU
        # must be re-reset AFTER that -- with spi_reset kept LOW (re-asserting it
        # here breaks the boot).
        self.mcp.set_gpio_designation(p.spi_select, self._Desig.GPIO)
        self.mcp.set_gpio_output_value(p.spi_select, False)
        self.mcp.set_gpio_output_value(p.spi_reset, False)
        self.mcp.set_gpio_output_value(p.global_reset, True)
        self.mcp.set_gpio_output_value(p.controller_reset, False)
        time.sleep(0.15)
        self.mcp.set_gpio_output_value(p.controller_reset, True)
        time.sleep(0.3)
        if progress:
            progress(100)

    def restart_controller(self):
        """Reboot the CPU with the crypto released: restore the full proven
        run state (spi_reset LOW, spi_select LOW, global_reset HIGH) and pulse
        controller_reset."""
        p = self.pins
        self.mcp.set_gpio_output_value(p.spi_reset, False)
        self.mcp.set_gpio_output_value(p.spi_select, False)
        self.mcp.set_gpio_output_value(p.global_reset, True)
        self.mcp.set_gpio_output_value(p.controller_reset, False)
        time.sleep(0.1)
        self.mcp.set_gpio_output_value(p.controller_reset, True)
        time.sleep(0.3)

    # The controller firmware prints this once on boot, before its command loop
    # (Software/Controller/main.c). It is the only POSITIVE confirmation that the
    # SPI load actually landed and the CPU is running: the SPI slave is
    # write-only (no MISO), so program() cannot read anything back, and it
    # reports 100% even with nothing attached.
    BOOT_BANNER = b"PROACT controller ready."

    def verify_running(self, uart, timeout=3.0):
        """Reboot the controller with `uart` already listening and wait for the
        boot banner. Returns True if the firmware announced itself, else False
        -- a False here means the SPI stream did not produce a running chip
        (board not connected, no bitstream on FPGA, wrong firmware), which a
        bare program() cannot detect. `uart` is any object with `read_available()`
        and (optionally) `reset_input_buffer()`; the UART transaction lock, if
        present, is taken so this never collides with a monitor poll."""
        lock = getattr(uart, "lock", None)
        if lock is not None:
            lock.acquire()
        try:
            try:
                uart.reset_input_buffer()
            except Exception:  # noqa: BLE001 -- not all transports expose it
                uart.read_available()
            self.restart_controller()          # boot with the port listening
            deadline = time.monotonic() + timeout
            buf = b""
            while time.monotonic() < deadline:
                buf += uart.read_available()
                if self.BOOT_BANNER in buf:
                    return True
                time.sleep(0.05)
            return False
        finally:
            if lock is not None:
                lock.release()
