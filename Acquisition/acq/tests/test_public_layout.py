"""Portability checks for the source-only public repository layout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from acq.analysis import ensure_dependencies
from acq.config import AcqConfig


REPO_ROOT = Path(__file__).resolve().parents[3]
ACQUISITION_ROOT = REPO_ROOT / "Acquisition"


def _probe_repo(env):
    return subprocess.run(
        [sys.executable, "-c", "from acq.paths import REPO; print(REPO)"],
        cwd=ACQUISITION_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_clean_clone_resolves_enclosing_public_checkout():
    env = os.environ.copy()
    env.pop("PROACT_REPO", None)
    result = _probe_repo(env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == REPO_ROOT


def test_invalid_explicit_repo_fails_instead_of_silently_falling_back(tmp_path):
    env = os.environ.copy()
    env["PROACT_REPO"] = str(tmp_path / "not-a-proact-checkout")
    result = _probe_repo(env)
    assert result.returncode != 0
    assert "PROACT_REPO does not contain" in result.stderr


@pytest.mark.parametrize(
    "target", ("aes1", "aes2", "sw_rv", "sw_rv_masked", "ascon", "xoodyak"))
def test_all_automatic_analysis_models_ship_in_public_tree(target):
    cfg = AcqConfig(target=target, traces=2, auto_cpa=True)
    ensure_dependencies(cfg)
