"""Offline environment discovery. Never imports or enumerates hardware drivers."""
from importlib import metadata, util
from pathlib import Path
import platform
import re
import sys


DEPENDENCIES = (
    ("numpy", "numpy", "core", True),
    ("serial", "pyserial", "UART", True),
    ("hid", "hidapi", "USB bridge", True),
    ("mcp2210", "mcp2210-python", "SPI programmer", True),
    ("PyQt6", "PyQt6", "GUI", False),
    ("h5py", "h5py", "HDF5 storage", False),
    ("chipwhisperer", "chipwhisperer", "capture", False),
    ("pytest", "pytest", "offline tests", False),
)

VERSION_REQUIREMENTS = {
    "numpy": ">=1.23",
    "pyserial": ">=3.5",
    "hidapi": ">=0.14",
    "mcp2210-python": "==1.0.4",
}


def _version_satisfies(installed, requirement):
    """Check the simple exact/minimum forms used by the core manifests.

    Prerelease or otherwise nonnumeric versions fail closed. This intentionally
    avoids importing an extra packaging dependency in the offline doctor.
    """
    if not installed:
        return False
    if requirement.startswith("=="):
        return installed == requirement[2:]
    if not requirement.startswith(">="):
        return False
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:\.post\d+)?(?:\+[A-Za-z0-9.-]+)?", installed)
    target = re.fullmatch(r"\d+(?:\.\d+)*", requirement[2:])
    if match is None or target is None:
        return False
    left = tuple(int(part) for part in match.group(1).split("."))
    right = tuple(int(part) for part in target.group().split("."))
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def environment_report():
    from . import __version__
    root = Path(__file__).resolve().parents[3]
    dependencies = []
    for module, distribution, purpose, required in DEPENDENCIES:
        try:
            available = util.find_spec(module) is not None
        except (ValueError, ImportError, AttributeError):
            available = False
        try:
            version = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            version = None
        version_requirement = VERSION_REQUIREMENTS.get(distribution)
        compatible = available and version is not None and (
            version_requirement is None
            or _version_satisfies(version, version_requirement))
        required_version = (
            version_requirement[2:]
            if version_requirement and version_requirement.startswith("==")
            else None)
        dependencies.append(dict(module=module, distribution=distribution,
                                 purpose=purpose, required=required,
                                 discoverable=available, version=version,
                                 version_requirement=version_requirement,
                                 required_version=required_version,
                                 compatible=compatible))
    return {
        "schema_version": 1, "proact_version": __version__,
        "python": sys.executable, "python_version": platform.python_version(),
        "workspace": str(root), "package": str(Path(__file__).resolve().parent),
        "hardware_accessed": False,
        "check_scope": "Package discovery and declared core version checks only; driver imports and device connectivity are not tested.",
        "core_dependencies_available": all(d["compatible"] for d in dependencies if d["required"]),
        "dependencies": dependencies,
        "files": {p: (root / p).is_file() for p in (
            "config/hardware.json", "Software/GUI/proact_gui.py",
            "Software/Controller/main.vmem", "Software/SW_RV/sw_rv_imem.vmem",
            "Software/SW_RV/sw_rv_dmem.vmem")},
    }
