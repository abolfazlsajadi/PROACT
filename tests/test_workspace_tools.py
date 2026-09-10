"""Offline setup, launcher isolation and fast-import regressions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workspace_setup", ROOT / "tools/setup_env.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_setup_dry_run_does_not_create_files_or_install(tmp_path, capsys):
    destination = tmp_path / "env"
    assert setup.main(["--venv", str(destination), "--with", "gui", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["action"] == "create"
    assert report["removes_existing_files"] is False
    assert not destination.exists()


def test_setup_refuses_non_environment_directory_without_deleting_it(tmp_path):
    sentinel = tmp_path / "keep.txt"; sentinel.write_text("keep")
    with pytest.raises(SystemExit) as exc:
        setup.main(["--venv", str(tmp_path), "--no-install"])
    assert exc.value.code == 2
    assert sentinel.read_text() == "keep"


def test_setup_reuses_existing_environment_without_recreating(monkeypatch, tmp_path):
    (tmp_path / "pyvenv.cfg").write_text("home = /fake\n")
    (tmp_path / "bin").mkdir(); (tmp_path / "bin/python").touch()
    sentinel = tmp_path / "user-installed.txt"; sentinel.write_text("keep")
    monkeypatch.setattr(setup.venv, "EnvBuilder", lambda **kwargs: pytest.fail("existing venv recreated"))
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *args, **kwargs: str(tmp_path) + "\n")
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected pip"))
    assert setup.main(["--venv", str(tmp_path), "--no-install"]) == 0
    assert sentinel.read_text() == "keep"


def test_setup_uses_copies_and_supports_a_filesystem_without_symlinks(monkeypatch, tmp_path):
    destination = tmp_path / "env"
    settings = {}
    class FakeBuilder:
        def __init__(self, **kwargs): settings.update(kwargs)
        def create(self, path):
            if os.name == "posix" and sys.maxsize > 2**32:
                assert (path / "lib64").is_dir()
                assert not (path / "lib64").is_symlink()
            (path / "bin").mkdir(); (path / "bin/python").touch()
            (path / "pyvenv.cfg").write_text("fake")
    monkeypatch.setattr(setup.venv, "EnvBuilder", FakeBuilder)
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *args, **kwargs: str(destination))
    assert setup.main(["--venv", str(destination), "--no-install"]) == 0
    assert settings["symlinks"] is False
    assert settings["system_site_packages"] is False


def test_setup_cannot_install_copy_over_shared_bench_environment():
    with pytest.raises(SystemExit) as exc:
        setup.main(["--venv", str(Path.home() / ".proact-venv"), "--dry-run"])
    assert exc.value.code == 2


def test_cli_launcher_uses_this_copy_from_another_working_directory(tmp_path):
    env = os.environ.copy(); env["PROACT_PYTHON"] = sys.executable
    run = subprocess.run(["bash", str(ROOT / "run_cli.sh"), "doctor", "--json"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert json.loads(run.stdout)["workspace"] == str(ROOT)


def test_explicit_invalid_python_does_not_silently_fall_back(tmp_path):
    env = os.environ.copy(); env["PROACT_PYTHON"] = str(tmp_path / "missing-python")
    run = subprocess.run(["bash", str(ROOT / "run_cli.sh"), "version"], env=env,
                         capture_output=True, text=True)
    assert run.returncode == 1
    assert "PROACT_PYTHON" in run.stderr


def test_basic_import_does_not_load_storage_or_device_backends():
    env = os.environ.copy(); env["PYTHONPATH"] = str(ROOT / "Software/Python")
    code = '''import sys, json, proact_host
print(json.dumps(sorted(set(sys.modules) & {'numpy','h5py','serial','hid','mcp2210','chipwhisperer','proact_host.storage'})))'''
    run = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == []


def test_failed_target_initialization_closes_uart(monkeypatch):
    from proact_host import cli, transport
    closed = []
    class FakeTransport:
        def __init__(self, **kwargs): pass
        def open(self): return self
        def close(self): closed.append(True)
    def bad_target(_transport): raise RuntimeError("initialization failed")
    monkeypatch.setattr(transport, "UartTransport", FakeTransport)
    monkeypatch.setattr(transport, "ProactTarget", bad_target)
    with pytest.raises(RuntimeError, match="initialization failed"):
        cli._target(SimpleNamespace(port=None))
    assert closed == [True]
