"""
Robust UART-monitor decoding. Serial data from the chip can contain non-ASCII
bytes -- deliberately (the 0xA5 binary result frames) or from line noise / a
marginal clock. These helpers NEVER raise on bad bytes: printable ASCII is shown
as text (with the backslash doubled, so the escape alphabet can never be forged
by payload bytes), everything else as a hex escape, and a parallel hex column is
always available.
"""
from typing import Iterator, List, Tuple

_PRINTABLE = set(range(0x20, 0x7F)) | {0x09}  # space..~ plus tab


def safe_text(data: bytes) -> str:
    """Printable ASCII kept; every other byte shown as \\xNN. Lossless, never
    raises. A payload backslash is doubled (\\\\), so the escape prefix always
    means an escape and the text alone maps back to exactly one byte string."""
    out = []
    for b in data:
        if b == 0x0A:
            out.append("\\n")
        elif b == 0x0D:
            out.append("\\r")
        elif b == 0x5C:
            # Backslash is printable, but it also introduces every escape; left
            # raw it would let real data spell an escape sequence.
            out.append("\\\\")
        elif b in _PRINTABLE:
            out.append(chr(b))
        else:
            out.append("\\x%02X" % b)
    return "".join(out)


def hex_str(data: bytes) -> str:
    """"48 65 6C ..." """
    return " ".join("%02X" % b for b in data)


def is_frame_start(data: bytes) -> bool:
    """True if a byte >= 0x80 is present (a binary result frame / non-ASCII)."""
    return any(b >= 0x80 for b in data)


class MonitorDecoder:
    """Streaming, fault-tolerant line splitter. Feed it whatever bytes arrive;
    it yields (text, hex) tuples per newline-terminated chunk, flushing a
    partial line after enough idle feeds so a dropped newline never hides data.
    Non-ASCII bytes are preserved (shown as hex escapes), so a corrupted or
    binary byte can never crash the monitor or desync it permanently."""

    def __init__(self, idle_flush: int = 20):
        self._buf = bytearray()
        self._idle = 0
        self._idle_flush = idle_flush

    def feed(self, data: bytes) -> List[Tuple[str, str]]:
        lines: List[Tuple[str, str]] = []
        if not data:
            self._idle += 1
            if self._buf and self._idle >= self._idle_flush:
                lines.append(self._emit())
            return lines
        self._idle = 0
        self._buf += data
        while b"\n" in self._buf:
            i = self._buf.index(b"\n")
            chunk = bytes(self._buf[:i])
            del self._buf[:i + 1]
            lines.append((safe_text(chunk), hex_str(chunk)))
        # cap runaway buffers (garbage with no newline)
        if len(self._buf) > 4096:
            lines.append(self._emit())
        return lines

    def _emit(self) -> Tuple[str, str]:
        chunk = bytes(self._buf)
        self._buf.clear()
        self._idle = 0
        return (safe_text(chunk), hex_str(chunk))

    def flush(self) -> Iterator[Tuple[str, str]]:
        if self._buf:
            yield self._emit()
