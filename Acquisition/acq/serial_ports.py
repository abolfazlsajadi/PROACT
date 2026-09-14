"""Read Linux USB-UART metadata without opening a serial device."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional, Sequence


@dataclass(frozen=True)
class SerialPort:
    device: str
    path: str
    description: str
    serial_number: str = ""
    location: str = ""


_USB_TTY = re.compile(r"tty(ACM|USB)(\d+)\Z")


def _entries(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        return []


def _metadata(directory: Path, name: str) -> str:
    try:
        value = (directory / name).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())


def _details(entry: Path, sys_root: Path) -> tuple[str, str, str]:
    try:
        device = (entry / "device").resolve(strict=True)
    except (OSError, RuntimeError):
        return "USB serial port", "", ""
    for parent in (device, *device.parents):
        if parent != sys_root and sys_root not in parent.parents:
            break
        product = _metadata(parent, "product")
        manufacturer = _metadata(parent, "manufacturer")
        serial = _metadata(parent, "serial")
        try:
            is_usb = (parent / "idVendor").exists() or (parent / "idProduct").exists()
        except OSError:
            is_usb = False
        if product or manufacturer or serial or is_usb:
            if product and manufacturer and manufacturer.casefold() not in product.casefold():
                description = f"{product} ({manufacturer})"
            else:
                description = product or manufacturer or "USB serial port"
            return description, serial, _metadata(parent, "location") or parent.name
    return "USB serial port", "", ""


def discover_serial_ports(*, sys_tty: Path = Path("/sys/class/tty"),
                          dev_root: Path = Path("/dev")) -> list[SerialPort]:
    """List present ttyACM/ttyUSB nodes, preferring stable by-id aliases."""
    sys_tty, dev_root = Path(sys_tty), Path(dev_root)
    try:
        sys_root = sys_tty.resolve(strict=True).parents[1]
    except (OSError, RuntimeError, IndexError):
        return []
    aliases: dict[Path, str] = {}
    for alias in _entries(dev_root / "serial" / "by-id"):
        try:
            if alias.is_symlink():
                aliases.setdefault(alias.resolve(strict=True), str(alias))
        except (OSError, RuntimeError):
            continue

    ports: list[SerialPort] = []
    seen: set[Path] = set()
    for entry in _entries(sys_tty):
        match = _USB_TTY.fullmatch(entry.name)
        if not match:
            continue
        node = dev_root / entry.name
        try:
            if not entry.exists():
                continue
            resolved = node.resolve(strict=True)
            if resolved in seen:
                continue
        except (OSError, RuntimeError):
            continue
        description, serial, location = _details(entry, sys_root)
        try:
            if not entry.exists() or not node.exists():
                continue
        except OSError:
            continue
        seen.add(resolved)
        saved_path = aliases.get(resolved, str(node))
        if saved_path != str(node):
            try:
                if Path(saved_path).resolve(strict=True) != resolved:
                    saved_path = str(node)
            except (OSError, RuntimeError):
                saved_path = str(node)
        ports.append(SerialPort(str(node), saved_path, description, serial, location))
    ports.sort(key=lambda p: (Path(p.device).name[:6],
                              int(_USB_TTY.fullmatch(Path(p.device).name).group(2)),
                              p.path))
    return ports


def recommended_port(ports: Sequence[SerialPort]) -> Optional[SerialPort]:
    """Return a unique labelled MCP2200, or the sole USB-UART candidate."""
    mcp2200 = [p for p in ports if "mcp2200" in p.description.casefold()]
    if len(mcp2200) == 1:
        return mcp2200[0]
    return ports[0] if len(ports) == 1 else None
