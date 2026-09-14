"""Shared terminal capabilities and the single Rich console used by the CLI.

Keeping one console prevents status spinners, progress bars and messages from
competing for the cursor. ``NO_COLOR`` is honoured by Rich and ``TERM=dumb`` or
redirected output selects the plain, log-friendly interface.
"""
from __future__ import annotations

import os
import sys
from typing import Optional, TextIO


try:
    from rich.console import Console
    HAVE_RICH = True
except Exception:  # pragma: no cover - exercised on dependency-light hosts
    Console = None
    HAVE_RICH = False


def is_tty(stream: Optional[TextIO] = None) -> bool:
    """Return whether *stream* is an interactive terminal, without assuming it."""
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def terminal_is_dumb() -> bool:
    return os.environ.get("TERM", "").strip().lower() == "dumb"


def interactive_rich(stream: Optional[TextIO] = None) -> bool:
    """Whether cursor-based Rich rendering is appropriate for this output."""
    return HAVE_RICH and is_tty(stream) and not terminal_is_dumb()


def make_console(*, file: Optional[TextIO] = None, **kwargs):
    """Create a consistently configured console (also useful for UI tests)."""
    if not HAVE_RICH:
        return None
    options = {
        "file": file or sys.stdout,
        "highlight": False,
        "markup": True,
        "soft_wrap": False,
    }
    options.update(kwargs)
    return Console(**options)


CONSOLE = make_console()
