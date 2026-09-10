"""
proact_host -- one host-side package for the PROACT chip.

  regs        register/address map + command protocol (mirrors proact_regs.h)
  config      bench constants (USB ids, clock, MCP serials, GPIO pins)
  transport   UartTransport + ProactTarget (the command protocol)
  programmer  Mcp2210Programmer (SPI .vmem code loader)
  vmem        .vmem parser
  capture     ChipWhispererCapture (optional; needs chipwhisperer)

Hardware backends (pyserial, hid, mcp2210, chipwhisperer) are imported lazily,
so `import proact_host` works for inspection/tests without them installed.
"""
from . import config, regs, vmem, validation
from .transport import ProactTarget, UartTransport, block_to_words, words_to_block

__all__ = [
    "config", "regs", "vmem", "validation", "storage",
    "UartTransport", "ProactTarget", "block_to_words", "words_to_block",
]
__version__ = "1.1.0.dev1"


def __getattr__(name):
    # Reading help, versions or the register map should not import NumPy/HDF5.
    # Existing `from proact_host import storage` and `proact_host.storage` work.
    if name == "storage":
        from importlib import import_module
        module = import_module(".storage", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
