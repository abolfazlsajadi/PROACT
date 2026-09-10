"""
UART transport + high-level PROACT target API.

`UartTransport` is the raw serial link (auto-detects the MCP2200). `ProactTarget`
wraps it in the controller command protocol (regs.py) with a clean method per
operation and a parser for the 0xA5 binary result frames.

pyserial/hid are imported lazily so this module can be imported (and the API
inspected/unit-tested) on a machine without them or without hardware attached.

THREADING: there is ONE serial stream, so a command and its reply frame must be
atomic against every other thread that touches the port (the GUI's UART-monitor
poller famously ate reply frames -- every command then died with "no frame
marker"). `UartTransport.lock` is that transaction lock; ProactTarget holds it
for each command+reply, and passive readers must use read_available() and only
after try-acquiring the lock.
"""
import os
import threading
from typing import List, Optional, Tuple

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

from . import config, regs


def _u32_be(word: int) -> bytes:
    return bytes([(word >> 24) & 0xFF, (word >> 16) & 0xFF,
                  (word >> 8) & 0xFF, word & 0xFF])


def block_to_words(block: bytes) -> List[int]:
    """16 bytes -> 4 big-endian words (the firmware's convention)."""
    if len(block) != 16:
        raise ValueError("block must be 16 bytes")
    return [int.from_bytes(block[i:i + 4], "big") for i in range(0, 16, 4)]


def words_to_block(words: List[int]) -> bytes:
    return b"".join(_u32_be(w) for w in words)


class UartTransport:
    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 timeout: float = 2.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser = None
        self.lock = threading.RLock()  # transaction lock (see module docstring)
        self._lock_fh = None

    def _detect_port(self) -> str:
        import serial.tools.list_ports
        # 1) Try identifying by VID/PID via pyserial (fastest and doesn't hang)
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            if p.vid == config.MCP2200_VID and p.pid == config.MCP2200_PID:
                if config.MCP2200_SERIAL and p.serial_number != config.MCP2200_SERIAL:
                    continue
                return p.device

        # 2) Fallback: use hid to find the serial number, then match with pyserial.
        # Enumerate ONLY the Microchip VID/PID -- a full hid.enumerate() walk
        # can crash inside hidapi on unrelated HID hardware (bench-observed).
        try:
            import hid
            for dev in hid.enumerate(config.MCP2200_VID, config.MCP2200_PID):
                if config.MCP2200_SERIAL and dev.get("serial_number") != config.MCP2200_SERIAL:
                    continue
                for p in ports:
                    if p.serial_number == dev.get("serial_number"):
                        return p.device
        except (ImportError, Exception):  # noqa: BLE001
            pass

        raise RuntimeError(
            "No MCP2200 UART device found. Ensure the MCP2200 is connected via USB and "
            "that you have the necessary permissions (udev rules on Linux)."
        )

    def open(self):
        import serial
        with self.lock:
            if self._ser is not None:
                return self
            if fcntl is None:
                raise RuntimeError("UART locking requires POSIX fcntl on this host")
            port = self.port or self._detect_port()
            # Cross-process lock to prevent UART contention. Closing its file
            # descriptor releases the lock, including on a failed serial open.
            lock_path = os.path.join("/tmp", f"proact_uart_{os.path.basename(port)}.lock")
            try:
                self._lock_fh = open(lock_path, "a")
                fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                self.close()
                raise RuntimeError(
                    f"Could not acquire lock for {port}. "
                    "Ensure no other PROACT process (GUI or CLI) is running."
                ) from None
            try:
                self._ser = serial.Serial(port=port, baudrate=self.baud, timeout=self.timeout)
            except BaseException:
                self.close()
                raise
            self.port = port
            return self

    def close(self):
        with self.lock:
            serial_handle, lock_handle = self._ser, self._lock_fh
            self._ser = self._lock_fh = None
            try:
                if serial_handle is not None:
                    serial_handle.close()
            finally:
                if lock_handle is not None:
                    lock_handle.close()

    def write(self, data: bytes):
        self._ser.write(data)

    def read(self, n: int) -> bytes:
        return self._ser.read(n)

    def read_available(self) -> bytes:
        """Read ONLY the bytes already buffered by the OS -- never blocks.
        This is what a passive monitor/logger must use: a blocking read(256)
        from a second thread swallows the reply frames of real commands."""
        if self._ser is None:
            return b""
        n = self._ser.in_waiting
        return self._ser.read(n) if n else b""

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()


class ProactTarget:
    """High-level controller protocol. One method per command."""

    def __init__(self, transport: UartTransport):
        self.t = transport
        # Transaction lock shared with the transport (fallback for bare links).
        self.lock = getattr(transport, "lock", None) or threading.RLock()

    # --- low level ---
    def _cmd(self, byte: int, payload: bytes = b""):
        with self.lock:
            self.t.write(bytes([byte]) + payload)

    # --- configuration ---
    def enable_debug(self):
        self._cmd(regs.CMD_DBG)

    def enable_sendback(self):
        self._cmd(regs.CMD_SB)

    def toggle_arm(self):
        self._cmd(regs.CMD_ARM)

    def set_decrypt(self, on):
        self._cmd(regs.CMD_DEC, bytes([1 if on else 0]))

    def set_trigger_cfg(self, cfg):
        self._cmd(regs.CMD_TRIG, bytes([cfg & 0x7F]))

    def seed_rng(self, seed):
        self._cmd(regs.CMD_SEED, _u32_be(seed))

    # trigger-source mux (which core drives the scope trigger); None = auto
    _CFGSEL = {None: 0xFF, "software": 0, "ascon": 1, "aes1": 2, "aes2": 3,
               "xoodyak": 4, "swrv": 5}

    def set_cfgsel(self, source):
        """source: None (auto), 'software', 'aes1', 'aes2', 'ascon', 'xoodyak', 'swrv'."""
        self._cmd(regs.CMD_CFGSEL, bytes([self._CFGSEL[source]]))

    def set_key(self, key: bytes):
        self._cmd(regs.CMD_KEY, words_to_block(block_to_words(key)))

    def set_plaintext(self, pt):
        self._cmd(regs.CMD_PT, words_to_block(block_to_words(pt)))

    def set_nonce(self, nonce):
        self._cmd(regs.CMD_NONCE, words_to_block(block_to_words(nonce)))

    def set_ad(self, ad):
        self._cmd(regs.CMD_AD, words_to_block(block_to_words(ad)))

    # --- core select ---
    _SELECT = {"aes1": regs.CMD_AES1, "aes2": regs.CMD_AES2,
               "xoodyak": regs.CMD_XOO, "ascon": regs.CMD_ASC,
               "swrv": regs.CMD_SWRV}

    def select(self, core: str):
        self._cmd(self._SELECT[core.lower()])

    # --- run / results ---
    def run(self):
        self._cmd(regs.CMD_RDY)

    def read_frame(self) -> Tuple[int, bytes]:
        """Read one 0xA5 <mode> <len> <payload> frame. Skips any ASCII debug
        bytes (all < 0x80) until the 0xA5 marker.
        """
        with self.lock:
            # Skip noise/ASCII until we see the frame marker. Limit skip to
            # avoid infinite loops on a floating RX line or wrong baud rate.
            skipped = 0
            while True:
                b = self.t.read(1)
                if not b:
                    raise TimeoutError("no frame marker")
                if b[0] == regs.FRAME_MARKER:
                    break
                skipped += 1
                if skipped > 8192:
                    raise RuntimeError("too much junk before frame marker (wrong baud rate?)")

            hdr = self.t.read(2)  # <mode><len>; read(2) may short on timeout
            if len(hdr) != 2:
                raise TimeoutError("short frame header")
            mode, length = hdr[0], hdr[1]
            payload = self.t.read(length)
            if len(payload) != length:
                raise TimeoutError("short frame payload")
            return mode, payload

    def run_and_read(self) -> Tuple[int, bytes]:
        with self.lock:  # command + reply = one transaction
            self.run()
            return self.read_frame()

    @staticmethod
    def _u32(payload: bytes, what: str) -> int:
        """Decode a 32-bit big-endian reply, refusing a short frame.

        `int.from_bytes(payload[:4])` silently returns a plausible small number
        when the controller sends fewer than 4 bytes -- an empty payload reads
        back as a perfectly valid-looking 0. A short frame means the link is
        broken, so raise instead of inventing a register value."""
        if len(payload) < 4:
            raise TimeoutError(
                f"short {what} reply: expected 4 payload bytes, got {len(payload)}")
        return int.from_bytes(payload[:4], "big")

    def get_timer(self) -> int:
        with self.lock:
            self._cmd(regs.CMD_TIME)
            mode, payload = self.read_frame()
        return self._u32(payload, "timer")

    def read_status(self) -> int:
        """Read the real status register (0x20000000) via the controller."""
        with self.lock:
            self._cmd(regs.CMD_RDSTAT)
            mode, payload = self.read_frame()
        return self._u32(payload, "status")

    def write_control(self, value: int):
        """Write a raw 32-bit control value (hardware truncates bit31)."""
        self._cmd(regs.CMD_WRCTRL, _u32_be(value & 0xFFFFFFFF))

    def aead_kat(self) -> Tuple[bool, bool]:
        """Run the on-chip ASCON + Xoodyak known-answer ENCRYPT test (the exact
        reference vectors / expected CT+TAG). Returns (xoodyak_ok, ascon_ok)."""
        with self.lock:
            self._cmd(getattr(regs, "CMD_AEADKAT", 0x1A))
            mode, payload = self.read_frame()
        res = int.from_bytes(payload[:4], "big")
        return bool(res & 0x1), bool(res & 0x2)

    # --- raw bus access (bring-up / debug / Sw-RV memory poke) ---
    def poke(self, addr: int, value: int):
        """Raw bus write: write `value` to `addr` (CMD_POKE 0x18)."""
        with self.lock:
            self._cmd(getattr(regs, "CMD_POKE", 0x18), _u32_be(addr) + _u32_be(value))

    def peek(self, addr: int) -> int:
        """Raw bus read: return the 32-bit word at `addr` (CMD_PEEK 0x19)."""
        with self.lock:
            self._cmd(getattr(regs, "CMD_PEEK", 0x19), _u32_be(addr))
            mode, payload = self.read_frame()
        return int.from_bytes(payload[:4], "big")

    def poke_words(self, addr: int, words: List[int], stride: int = 4):
        """Write consecutive words starting at `addr` (addr += stride each)."""
        with self.lock:
            for i, w in enumerate(words):
                self.poke(addr + i * stride, w)

    def peek_words(self, addr: int, count: int, stride: int = 4) -> List[int]:
        """Read `count` consecutive words starting at `addr`."""
        with self.lock:
            return [self.peek(addr + i * stride) for i in range(count)]

    def self_test(self):
        self._cmd(regs.CMD_TEST)

    # --- Sw-RV target loading ---
    def load_target_imem(self, words: List[int]):
        self._cmd(regs.CMD_LDI, _u32_be(len(words)) + b"".join(_u32_be(w) for w in words))

    def load_target_dmem(self, words: List[int], base: int):
        self._cmd(regs.CMD_LDD,
                  _u32_be(len(words)) + _u32_be(base) + b"".join(_u32_be(w) for w in words))

    def load_swrv_program(self, imem: List[int], dmem: List[int],
                          dmem_base: int = 0x08100000, boot_delay: float = 0.2):
        """Load a program into the Sw-RV target and boot it, in the ONE correct
        order. CMD_LDI holds the target in reset while its memories are written;
        select('swrv') then releases it, so the 0->1 enable edge boots the
        freshly-loaded code (a reload of a *different* program takes effect)."""
        import time
        with self.lock:
            self.load_target_imem(list(imem))  # holds target in reset + loads
            self.load_target_dmem(list(dmem), dmem_base)
            self.select("swrv")  # release -> fresh boot
        time.sleep(boot_delay)  # let it reach its mailbox loop
