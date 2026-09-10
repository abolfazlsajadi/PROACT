"""
Shared pytest setup for the PROACT host-library regression tests.

These tests are OFFLINE ONLY: no serial port, no MCP2210/MCP2200, no
ChipWhisperer, no network. Anything that would touch the bench belongs in
the bench self-check (`proact selfcheck`), not here.

Putting Software/Python on sys.path lets `import proact_host` work when the
suite is run from the repo root without installing the package.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_PKG_DIR = os.path.join(REPO_ROOT, "Software", "Python")
HARDWARE_JSON = os.path.join(REPO_ROOT, "config", "hardware.json")

if HOST_PKG_DIR not in sys.path:
    sys.path.insert(0, HOST_PKG_DIR)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def repo_root():
    """Absolute path of the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def hardware_json_path():
    """Absolute path of config/hardware.json, the register-map source of truth."""
    return HARDWARE_JSON
