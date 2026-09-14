"""Resolve public host sources and externally supplied firmware artifacts.

The public repository contains ``proact_host`` but intentionally omits built
controller and Sw-RV firmware. A full design checkout can be selected with
``PROACT_REPO``. Individual firmware locations can also be overridden for a
source-only public checkout.
"""
from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
ACQUISITION_ROOT = os.path.dirname(_HERE)
PUBLIC_REPO_ROOT = os.path.dirname(ACQUISITION_ROOT)


def _host_package(root: str) -> str:
    return os.path.join(root, "Software", "Python", "proact_host")


def _normalized(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _resolve_repo() -> str:
    explicit = os.environ.get("PROACT_REPO")
    if explicit:
        root = _normalized(explicit)
        if not os.path.isdir(_host_package(root)):
            raise SystemExit(
                "PROACT_REPO does not contain Software/Python/proact_host: "
                f"{root}")
        return root

    for candidate in (
            PUBLIC_REPO_ROOT,
            os.path.join(ACQUISITION_ROOT, "proact_runtime")):
        root = _normalized(candidate)
        if os.path.isdir(_host_package(root)):
            return root
    raise SystemExit(
        "Could not find Software/Python/proact_host. Run from a PROACT clone or "
        "set PROACT_REPO to a full PROACT checkout.")


REPO = _resolve_repo()
SW_PY = os.path.join(REPO, "Software", "Python")
CONTROLLER_VMEM = _normalized(os.environ.get(
    "PROACT_CONTROLLER_VMEM",
    os.path.join(REPO, "Software", "Controller", "main.vmem")))
SWRV_FW = _normalized(os.environ.get(
    "PROACT_SWRV_FW_DIR",
    os.path.join(REPO, "Software", "SW_RV")))
SWRV_MASKED_FW = _normalized(os.environ.get(
    "PROACT_MASKED_FW_DIR",
    os.path.join(REPO, "Software", "SW_RV_masked")))

if SW_PY not in sys.path:
    sys.path.insert(0, SW_PY)
