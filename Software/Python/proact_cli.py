#!/usr/bin/env python3
"""
proact_cli.py -- thin shim so `python3 proact_cli.py ...` behaves exactly like
the installed `proact` command. The real implementation is in
proact_host.cli:main (info/devices/build-*/test/run/capture/gui/program/
selftest/load-swrv). Kept for users who run the file directly without installing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proact_host.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
