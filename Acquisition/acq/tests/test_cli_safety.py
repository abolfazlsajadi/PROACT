"""CLI error presentation tests that do not open acquisition hardware."""

import numpy as np
import pytest
import subprocess
import os
import sys
from pathlib import Path
from click.testing import CliRunner

import acquire
import acq.run as run_module
from acq.config import AcqConfig
from acq.inputs import InputGen
from acq.store import TraceStore
from acq.validate import ValidationError


def test_preflight_refusal_is_a_concise_cli_error(monkeypatch, tmp_path):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))

    def refuse(*_args, **_kwargs):
        raise ValidationError("trigger never fired")

    monkeypatch.setattr(run_module, "run", refuse)
    result = CliRunner().invoke(
        acquire.main,
        ["--target", "aes1", "--traces", "1", "--samples", "100",
         "--suffix", "_preflight_refusal", "--yes"],
    )
    assert result.exit_code == 1
    assert "Error: preflight refused acquisition: trigger never fired" in result.output
    assert "Traceback" not in result.output


def test_runtime_acquisition_failure_is_a_concise_cli_error(monkeypatch, tmp_path):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))

    def fail(*_args, **_kwargs):
        raise RuntimeError("scope trigger source readback mismatch")

    monkeypatch.setattr(run_module, "run", fail)
    result = CliRunner().invoke(
        acquire.main,
        ["--target", "aes1", "--traces", "1", "--samples", "100",
         "--suffix", "_runtime_refusal", "--yes"],
    )
    assert result.exit_code == 1
    assert "Error: acquisition failed: scope trigger source readback mismatch" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(("checkpoint_succeeded", "checkpoint_error", "message"), (
    (True, None, "progress checkpointed; re-run the same command to resume"),
    (False, "disk full", "emergency checkpoint failed: disk full"),
))
def test_keyboard_interrupt_exits_nonzero_with_truthful_checkpoint_message(
        monkeypatch, tmp_path, checkpoint_succeeded, checkpoint_error, message):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))

    def interrupt(*_args, **_kwargs):
        exc = KeyboardInterrupt()
        exc.proact_checkpoint_succeeded = checkpoint_succeeded
        exc.proact_checkpoint_error = checkpoint_error
        raise exc

    monkeypatch.setattr(run_module, "run", interrupt)
    result = CliRunner().invoke(
        acquire.main,
        ["--target", "aes1", "--traces", "1", "--samples", "100",
         "--suffix", "_keyboard_interrupt", "--yes"],
    )
    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("source", ("garbage", "CHANnel9", "CH0"))
def test_invalid_scope_trigger_source_is_refused_before_capture(source):
    result = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "1", "--backend", "scope",
        "--scope-trigger-source", source, "--samples", "100", "--estimate",
    ])
    assert result.exit_code == 2
    assert "scope trigger source must be" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("level", ("nan", "inf", "-inf"))
def test_nonfinite_scope_trigger_level_is_refused_before_capture(level):
    result = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "1", "--backend", "scope",
        "--scope-trigger-level", level, "--samples", "100", "--estimate",
    ])
    assert result.exit_code == 2
    assert "scope trigger level must be a finite voltage" in result.output
    assert "Traceback" not in result.output


def _completed_native(monkeypatch, tmp_path, suffix):
    monkeypatch.setattr("acq.config.DATA", str(tmp_path))
    cfg = AcqConfig("aes1", 1, samples=100, suffix=suffix, seed=123).validate()
    gen = InputGen(cfg)
    store = TraceStore(cfg, samples=100, out_len=16, key_varies=False)
    wave = np.linspace(-0.25, 0.25, 100, dtype=np.float32)
    store.append(0, wave, gen.key(0), gen.inp(0), bytes(16))
    store.checkpoint(1)
    store.close()
    return cfg


def test_completed_dataset_identity_is_checked_before_capture(monkeypatch, tmp_path):
    suffix = "_completed_identity_guard"
    _completed_native(monkeypatch, tmp_path, suffix)
    called = []
    monkeypatch.setattr(run_module, "run", lambda *_args, **_kwargs: called.append(True))

    changed_count = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "2", "--samples", "100",
        "--suffix", suffix, "--yes",
    ])
    assert changed_count.exit_code == 1
    assert "stored 1, requested 2" in changed_count.output

    changed_signature = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "1", "--samples", "100",
        "--gain", "30", "--suffix", suffix, "--yes",
    ])
    assert changed_signature.exit_code == 1
    assert "resume settings differ from this dataset: gain_db" in changed_signature.output
    assert called == []


def test_matching_completed_dataset_finalizes_without_capture(monkeypatch, tmp_path):
    suffix = "_completed_offline_finalize"
    _completed_native(monkeypatch, tmp_path, suffix)
    called = []
    monkeypatch.setattr(run_module, "run", lambda *_args, **_kwargs: called.append(True))
    result = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "1", "--samples", "100",
        "--suffix", suffix, "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert "finalizing offline without opening the board or instrument" in result.output
    assert called == []


@pytest.mark.parametrize("suffix", ("../../escape", "/absolute", "nested/path", "bad\\path"))
def test_suffix_cannot_escape_the_data_directory(suffix):
    result = CliRunner().invoke(acquire.main, [
        "--target", "aes1", "--traces", "1", "--samples", "100",
        "--suffix", suffix, "--estimate",
    ])
    assert result.exit_code == 2
    assert "suffix may contain only" in result.output
    assert "Traceback" not in result.output


def test_configuration_validation_remains_active_under_python_optimized_mode():
    root = Path(__file__).resolve().parents[2]
    script = (
        "from acq.config import AcqConfig\n"
        "bad = AcqConfig('bogus', -1, samples=-5, offset=-3, chunk=0, "
        "suffix='/../../outside')\n"
        "try:\n"
        " bad.validate()\n"
        "except ValueError as exc:\n"
        " print(exc)\n"
        " raise SystemExit(0)\n"
        "raise SystemExit(9)\n")
    result = subprocess.run(
        (str(Path(sys.executable)), "-O", "-c", script), cwd=root,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=10)
    assert result.returncode == 0, result.stdout
    assert "target must be" in result.stdout


@pytest.mark.parametrize(("args", "message"), (
    (("--check", "--not-a-real-option"), "unknown option"),
    (("--check", "--use-venv"), "requires a directory"),
))
def test_installer_rejects_invalid_options_cleanly(args, message):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        (str(root / "install.sh"), *args), cwd=root,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=10,
    )
    assert result.returncode == 2
    assert message in result.stdout
    assert "unbound variable" not in result.stdout


def test_launchers_are_valid_posix_shell_scripts():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ("sh", "-n", str(root / "install.sh"), str(root / "run.sh")),
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("launcher", ("install.sh", "run.sh"))
def test_launcher_directory_resolution_ignores_cdpath(launcher):
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["CDPATH"] = str(root.parent)
    environment["PROACT_ACQ_PYTHON"] = sys.executable
    result = subprocess.run(
        ("sh", f"{root.name}/{launcher}", "--help"),
        cwd=root.parent, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout
    assert "Usage:" in result.stdout or "usage:" in result.stdout
    assert str(root) not in result.stdout


def test_installer_check_fails_when_dependencies_are_missing_without_writes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    package = tmp_path / "package"
    package.mkdir()
    installer = package / "install.sh"
    installer.write_bytes((root / "install.sh").read_bytes())
    installer.chmod(0o755)
    fake_venv = tmp_path / "empty-venv"
    fake_python = fake_venv / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    environment = os.environ.copy()
    environment["HOME"] = str(fake_home)
    result = subprocess.run(
        (str(installer), "--check", "--use-venv", str(fake_venv)),
        cwd=package, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=10,
    )
    assert result.returncode == 1
    assert "required dependencies missing" in result.stdout
    assert not (package / ".venv_path").exists()
    assert not (package / ".venv").exists()
    assert not (fake_home / ".proact_acq_venv").exists()


def test_installer_rejects_installed_version_outside_declared_spec(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import importlib.metadata as m\n"
        "original = m.version\n"
        "def version(name):\n"
        "    if name.lower().replace('_', '-') == 'mcp2210-python':\n"
        "        return '0.0.0'\n"
        "    return original(name)\n"
        "m.version = version\n",
        encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(shim)
    venv_dir = Path(sys.executable).parent.parent
    result = subprocess.run(
        (str(root / "install.sh"), "--check", "--use-venv", str(venv_dir)),
        cwd=root, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=15,
    )
    assert result.returncode == 1
    assert "[MISSING] mcp2210 -> mcp2210-python==1.0.4" in result.stdout
    assert "required dependencies missing" in result.stdout


def test_installer_fails_if_interpreter_selection_cannot_be_recorded(tmp_path):
    root = Path(__file__).resolve().parents[2]
    package = tmp_path / "package"
    package.mkdir()
    installer = package / "install.sh"
    installer.write_bytes((root / "install.sh").read_bytes())
    installer.chmod(0o755)
    (package / ".venv_path").mkdir()
    venv_dir = Path(sys.executable).parent.parent
    result = subprocess.run(
        (str(installer), "--use-venv", str(venv_dir)),
        cwd=package, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=15,
    )
    assert result.returncode == 1
    assert "could not record the selected interpreter" in result.stdout
    assert "Setup complete" not in result.stdout
