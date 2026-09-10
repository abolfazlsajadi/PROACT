"""Shared terminal policy. Importing this module never enumerates devices."""
from __future__ import annotations

import os
import sys

try:
    from rich.console import Console
    HAVE_RICH = True
except ImportError:
    Console = None
    HAVE_RICH = False

CONSOLE = None
PLAIN = False


def is_tty():
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def configure_console(*, plain=False, no_color=False):
    global CONSOLE, PLAIN
    PLAIN = bool(plain)
    if HAVE_RICH:
        CONSOLE = Console(no_color=bool(no_color or "NO_COLOR" in os.environ),
                          color_system=None if plain else "auto")
    else:
        CONSOLE = None
    return CONSOLE


configure_console()
