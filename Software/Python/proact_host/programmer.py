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
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Callable, Optional

from . import config
from .vmem import parse_vmem


MCP2210_SDK_VERSION = "1.0.4"


def _require_supported_sdk():
    """Reject unverified GPIO-cache behavior before looking for hardware."""
    try:
        installed = distribution_version("mcp2210-python")
    except PackageNotFoundError:
        raise RuntimeError(
            "Cannot verify the installed mcp2210-python version; install "
            f"mcp2210-python=={MCP2210_SDK_VERSION}"
        ) from None
    if installed != MCP2210_SDK_VERSION:
        raise RuntimeError(
            f"Unsupported mcp2210-python {installed}; GPIO setup is validated only "
            f"with {MCP2210_SDK_VERSION}. Install mcp2210-python=={MCP2210_SDK_VERSION}"
        )


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
                    result = attr(*a, **k)
                except Exception as e:  # noqa: BLE001
                    if "Desync" not in type(e).__name__:
                        raise
                    time.sleep(0.05)
                    result = attr(*a, **k)  # one retry re-syncs cmd/response
                # mcp2210-python 1.0.4 sends an immediate output update but
                # leaves its private dirty flag set. Clear it only when an
                # immediate write succeeded; batched setup still needs the flag
                # for its final explicit flush.
                if (name == "set_gpio_output_value"
                        and getattr(self._mcp, "_immediate_gpio_update", None) is True):
                    self._mcp._gpio_output_needs_update = False
                return result
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
        _require_supported_sdk()
        self._Desig = Mcp2210GpioDesignation
        self._Dir = Mcp2210GpioDirection
        # mcp2210-python initializes its cached GPIO output bitmask to zero even
        # after reading the live pin levels.  Batch setup so changing a pin's
        # designation cannot briefly publish that zero mask and assert resets.
        backend = Mcp2210(self._detect(), immediate_gpio_update=False)
        self.mcp = _LockedMcp(backend)
        self.lock = self.mcp.lock
        try:
            self._setup()
            backend._immediate_gpio_update = True
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
        backend = getattr(self.mcp, "_mcp", self.mcp)
        if getattr(backend, "_immediate_gpio_update", None) is not False:
            raise RuntimeError("MCP2210 GPIO setup is not in safe batched mode")
        settings = getattr(backend, "_gpio_settings", None)
        observed_levels = getattr(settings, "gpio_input_level", None)
        if type(observed_levels) is not int or not 0 <= observed_levels <= 0x1FF:
            raise RuntimeError("MCP2210 live GPIO levels are unavailable; refusing unsafe setup")
        if type(getattr(backend, "_gpio_output_needs_update", None)) is not bool:
            raise RuntimeError("MCP2210 GPIO output cache is unavailable; refusing unsafe setup")
        self.mcp.configure_spi_timing(chip_select_to_data_delay=0,
                                      last_data_byte_to_cs=0, delay_between_bytes=0)
        self.mcp.set_spi_mode(1)
        for i in range(9):
            self.mcp.set_gpio_designation(i, self._Desig.GPIO)
        outputs = {p.controller_reset, p.spi_reset, p.global_reset, p.spi_select}
        # Define every direction. GPIO7 is the X1 debug feedback on this board;
        # leaving it in a prior OUTPUT state could drive that external net.
        for pin in range(9):
            direction = self._Dir.OUTPUT if pin in outputs else self._Dir.INPUT
            self.mcp.set_gpio_direction(pin, direction)
        # Seed the SDK's zero-initialized output cache from the levels observed
        # before setup, then publish designations, directions and values once.
        for pin in outputs:
            self.mcp.set_gpio_output_value(pin, bool(observed_levels & (1 << pin)))
        self.mcp.gpio_update()
        # mcp2210-python 1.0.4 does not clear this flag after a successful
        # SET_GPIO_PIN_VALUE. Clear it so later get_gpio_value()/GUI polls issue
        # GET commands only instead of repeatedly rewriting reset outputs.
        backend._gpio_output_needs_update = False

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
