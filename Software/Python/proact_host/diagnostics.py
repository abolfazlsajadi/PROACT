"""Offline environment discovery. Never imports or enumerates hardware drivers."""
from importlib import metadata, util
from pathlib import Path
import platform
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
        dependencies.append(dict(module=module, distribution=distribution,
                                 purpose=purpose, required=required,
                                 discoverable=available, version=version))
    return {
        "schema_version": 1, "proact_version": __version__,
        "python": sys.executable, "python_version": platform.python_version(),
        "workspace": str(root), "package": str(Path(__file__).resolve().parent),
        "hardware_accessed": False,
        "check_scope": "Package discovery only; driver imports and device connectivity are not tested.",
        "core_dependencies_available": all(d["discoverable"] for d in dependencies if d["required"]),
        "dependencies": dependencies,
        "files": {p: (root / p).is_file() for p in (
            "config/hardware.json", "Software/GUI/proact_gui.py",
            "Software/Controller/main.vmem", "Software/SW_RV/sw_rv_imem.vmem",
            "Software/SW_RV/sw_rv_dmem.vmem")},
    }
