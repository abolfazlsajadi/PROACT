"""Hardware-free firmware fixtures for the source-only public test suite."""
from __future__ import annotations

import hashlib

import pytest


@pytest.fixture(autouse=True)
def public_firmware_fixture(tmp_path, monkeypatch):
    """Keep unit tests independent of firmware omitted from the public tree."""
    from acq import paths
    from acq.targets import base as base_module
    from acq.targets import sw_rv as swrv_module
    from acq.targets.sw_rv_masked import MaskedSwRVTarget

    root = tmp_path / "firmware"
    controller = root / "Controller" / "main.vmem"
    plain = root / "SW_RV"
    masked = root / "SW_RV_masked"
    controller.parent.mkdir(parents=True)
    plain.mkdir(parents=True)
    masked.mkdir(parents=True)
    controller.write_text("00000000\n", encoding="ascii")
    for directory in (plain, masked):
        (directory / "sw_rv_imem.vmem").write_text(
            "@00000000\n00000000\n", encoding="ascii")
        (directory / "sw_rv_dmem.vmem").write_text(
            "@00000000\n00000000\n", encoding="ascii")

    identities = {
        name: hashlib.sha256((masked / name).read_bytes()).hexdigest()
        for name in ("sw_rv_imem.vmem", "sw_rv_dmem.vmem")
    }
    monkeypatch.setattr(paths, "CONTROLLER_VMEM", str(controller))
    monkeypatch.setattr(paths, "SWRV_FW", str(plain))
    monkeypatch.setattr(paths, "SWRV_MASKED_FW", str(masked))
    monkeypatch.setattr(base_module, "CONTROLLER_VMEM", str(controller))
    monkeypatch.setattr(swrv_module, "SWRV_FW", str(plain))
    monkeypatch.setattr(MaskedSwRVTarget, "firmware_dir", str(masked))
    monkeypatch.setattr(MaskedSwRVTarget, "firmware_sha256", identities)
