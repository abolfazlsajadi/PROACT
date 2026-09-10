"""
Bench configuration for the PROACT host tools.

Some values here could not be settled from the source tree and depend on the
PHYSICAL bench (see DISCOVERY_REPORT.md open questions). They are collected in
ONE place, with clearly-marked defaults, so a new bench is a one-file change.
Override any of them at runtime, e.g.:

    from proact_host import config
    config.INPUT_CLOCK_HZ = 50_000_000
"""
import json
import os
from dataclasses import dataclass

# --- USB device identifiers (stable; from the proven GUI) ---
MCP2200_VID = 0x04D8   # UART bridge
MCP2200_PID = 0x00DF
MCP2210_VID = 0x04D8   # SPI bridge / code loader
MCP2210_PID = 0x00DE


def _clock_from_hardware_json(default=50_000_000):
    """Single source of truth: read the core clock from config/hardware.json."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "..", "config", "hardware.json")
    try:
        with open(path) as f:
            return int(json.load(f).get("clock_hz_default", default))
    except Exception:  # noqa: BLE001
        return default


# --- core clock. Read from hardware.json so C, Python and docs never disagree.
#     Evidence (divisor 27 -> ~115200 baud, ~4340-cycle pacing) => ~50 MHz.
#     BENCH-CONFIRM this value. ---
INPUT_CLOCK_HZ = _clock_from_hardware_json()

# --- OPEN QUESTION: MCP serial numbers differ across scripts. Leave None to
#     auto-detect the first matching device; set explicitly to pin a board. ---
MCP2210_SERIAL = None
MCP2200_SERIAL = None


@dataclass
class Mcp2210Pins:
    """MCP2210 GPIO map (from the proven GUI spi.py).

    OPEN QUESTION: the readback pins for controller/global reset are 3/6 in
    spi.py but swapped (6/3) in a standalone SPI_DRIVER.py. These match spi.py,
    which is the one the working GUI uses -- confirm for the current board.
    """
    controller_reset: int = 5
    read_controller_reset: int = 3
    spi_reset: int = 1
    read_spi_reset: int = 0
    global_reset: int = 2
    read_global_reset: int = 6
    spi_select: int = 4
    read_spi_select: int = 7


PINS = Mcp2210Pins()


def divisor_for_baud(baud: int, clock_hz: int = None) -> int:
    """UART divisor = clock / (16 * baud). Reset default is 27."""
    clk = clock_hz if clock_hz is not None else INPUT_CLOCK_HZ
    return max(1, round(clk / (16 * baud)))


def baud_for_divisor(divisor: int, clock_hz: int = None) -> float:
    clk = clock_hz if clock_hz is not None else INPUT_CLOCK_HZ
    return clk / (16 * divisor)
