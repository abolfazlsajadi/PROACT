"""Offline Acquisition setup/launcher tests; no pip downloads or devices."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ACQUISITION = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("proact_setup_offline", ACQUISITION / "setup_offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("PROACT_ACQ_PYTHON", raising=False)
    return module


def report(prefix, *, missing=False, base="/base-python"):
    return {"prefix": str(prefix), "base_prefix": str(base), "python_version": [3, 10, 12],
            "dependencies": [{"name": "click", "available": not missing, "version": "8.1.0"}]}


def contents(path):
    return {str(item.relative_to(path)): item.read_bytes() if item.is_file() else None
            for item in path.rglob("*")}


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected environment or dependency mutation")


def test_check_missing_environment_is_read_only(setup, tmp_path, monkeypatch):
    (tmp_path / ".venv_path").write_text("/shared/bench/bin/python\n")
    before = contents(tmp_path)
    monkeypatch.setattr(setup.venv, "EnvBuilder", forbidden)
    checked = []
    def missing(python):
        checked.append(python)
        raise FileNotFoundError(python)
    monkeypatch.setattr(setup, "_inspect", missing)
    assert setup.main(["--check"]) == 1
    assert checked == [str(tmp_path / ".venv/bin/python")]
    assert contents(tmp_path) == before


@pytest.mark.parametrize("missing", [False, True])
def test_check_explicit_python_does_not_install_or_record_path(setup, tmp_path, monkeypatch, missing):
    monkeypatch.setenv("PROACT_ACQ_PYTHON", "/chosen environment/bin/python")
    monkeypatch.setattr(setup.venv, "EnvBuilder", forbidden)
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    checked = []
    def inspect(python):
        checked.append(python)
        return report("/chosen environment", missing=missing)
    monkeypatch.setattr(setup, "_inspect", inspect)
    assert setup.main(["--check"]) == int(missing)
    assert checked == ["/chosen environment/bin/python"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args, expected", [(["--help"], 0), (["--use-venv"], 2),
                                           (["--unknown"], 2)])
def test_help_and_argument_errors_have_no_side_effects(setup, tmp_path, monkeypatch, args, expected):
    monkeypatch.setattr(setup.venv, "EnvBuilder", forbidden)
    monkeypatch.setattr(setup, "_inspect", forbidden)
    with pytest.raises(SystemExit) as exc:
        setup.main(args)
    assert exc.value.code == expected
    assert not list(tmp_path.iterdir())


def test_conflicting_explicit_selectors_fail_before_inspection(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("PROACT_ACQ_PYTHON", "/chosen/python")
    monkeypatch.setattr(setup, "_inspect", forbidden)
    with pytest.raises(SystemExit) as exc:
        setup.main(["--use-venv", str(tmp_path / "other")])
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("no_install", [False, True])
def test_default_setup_creates_only_project_environment(setup, tmp_path, monkeypatch, no_install):
    created, pip_calls = [], []
    class Builder:
        def __init__(self, **kwargs):
            assert kwargs == {"with_pip": True, "symlinks": False, "system_site_packages": False}
        def create(self, destination):
            created.append(destination)
    monkeypatch.setattr(setup.venv, "EnvBuilder", Builder)
    monkeypatch.setattr(setup, "_inspect", lambda python: report(tmp_path / ".venv"))
    monkeypatch.setattr(setup.subprocess, "run", lambda command, **kwargs: pip_calls.append(command))
    assert setup.main(["--no-install"] if no_install else []) == 0
    assert created == [tmp_path / ".venv"]
    assert len(pip_calls) == (0 if no_install else 1)
    if pip_calls:
        assert pip_calls[0] == [str(tmp_path / ".venv/bin/python"), "-B", "-m", "pip", "install",
                                "--disable-pip-version-check", "--no-user", "-r",
                                str(tmp_path / "requirements.txt")]
    assert not (tmp_path / ".venv_path").exists()


def test_explicit_base_python_is_never_modified(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("PROACT_ACQ_PYTHON", "/base-python/bin/python")
    monkeypatch.setattr(setup, "_inspect", lambda python: report("/base-python"))
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        setup.main([])
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


def test_existing_environment_must_match_interpreter_prefix(setup, tmp_path, monkeypatch):
    destination = tmp_path / ".venv"
    destination.mkdir()
    (destination / "pyvenv.cfg").write_text("home = /base-python\n")
    monkeypatch.setattr(setup, "_inspect", lambda python: report("/other-environment"))
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    before = contents(tmp_path)
    with pytest.raises(SystemExit) as exc:
        setup.main([])
    assert exc.value.code == 2
    assert contents(tmp_path) == before


def test_check_refuses_implicit_symlink_to_another_environment(setup, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    try:
        (tmp_path / ".venv").symlink_to(shared, target_is_directory=True)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EOPNOTSUPP}:
            pytest.skip("test scratch volume does not support creating symlinks")
        raise
    monkeypatch.setattr(setup, "_inspect", forbidden)
    with pytest.raises(SystemExit) as exc:
        setup.main(["--check"])
    assert exc.value.code == 2
    assert not list(shared.iterdir())


def test_nonempty_non_environment_is_preserved(setup, tmp_path, monkeypatch):
    destination = tmp_path / ".venv"
    destination.mkdir()
    (destination / "keep.txt").write_text("user content")
    monkeypatch.setattr(setup.venv, "EnvBuilder", forbidden)
    before = contents(tmp_path)
    with pytest.raises(SystemExit) as exc:
        setup.main([])
    assert exc.value.code == 2
    assert contents(tmp_path) == before


def shell_environment(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    env.pop("PROACT_ACQ_PYTHON", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def fake_python(path, *, fail_dependencies=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "with open(os.environ['OFFLINE_LAUNCH_LOG'], 'a') as stream:\n"
                    "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    + ("raise SystemExit(1 if '-c' in sys.argv else 0)\n" if fail_dependencies else ""))
    path.chmod(0o755)


def test_launcher_ignores_shared_fallback_and_old_marker(tmp_path):
    shutil.copy2(ACQUISITION / "run.sh", tmp_path / "run.sh")
    env = shell_environment(tmp_path)
    shared = Path(env["HOME"]) / ".proact-venv/bin/python"
    fake_python(shared)
    (tmp_path / ".venv_path").write_text(str(shared))
    log = tmp_path / "calls.jsonl"
    env["OFFLINE_LAUNCH_LOG"] = str(log)
    result = subprocess.run(["bash", str(tmp_path / "run.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and "interpreter not found" in result.stderr
    assert not log.exists()
    result = subprocess.run(["bash", str(tmp_path / "run.sh"), "--help"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0 and "offline configuration" in result.stdout
    assert not log.exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_launcher_uses_selected_python_and_preserves_arguments(tmp_path, explicit):
    shutil.copy2(ACQUISITION / "run.sh", tmp_path / "run.sh")
    env = shell_environment(tmp_path)
    python = tmp_path / ("chosen environment/bin/python" if explicit else ".venv/bin/python")
    fake_python(python)
    if explicit:
        env["PROACT_ACQ_PYTHON"] = str(python)
    log = tmp_path / "calls.jsonl"
    env["OFFLINE_LAUNCH_LOG"] = str(log)
    result = subprocess.run(["bash", str(tmp_path / "run.sh"), "--output", "path with spaces"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0][:2] == ["-B", "-c"]
    assert calls[1] == ["-B", str(tmp_path / "acquire.py"), "--output", "path with spaces"]


def test_launcher_dependency_failure_prevents_cli_start(tmp_path):
    shutil.copy2(ACQUISITION / "run.sh", tmp_path / "run.sh")
    fake_python(tmp_path / ".venv/bin/python", fail_dependencies=True)
    env = shell_environment(tmp_path)
    log = tmp_path / "calls.jsonl"
    env["OFFLINE_LAUNCH_LOG"] = str(log)
    result = subprocess.run(["bash", str(tmp_path / "run.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and "dependencies are missing" in result.stderr
    assert len(log.read_text().splitlines()) == 1


def test_launcher_requires_explicit_selection_for_symlinked_environment(tmp_path):
    shutil.copy2(ACQUISITION / "run.sh", tmp_path / "run.sh")
    shared = tmp_path / "shared"
    fake_python(shared / "bin/python")
    try:
        (tmp_path / ".venv").symlink_to(shared, target_is_directory=True)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EOPNOTSUPP}:
            pytest.skip("test scratch volume does not support creating symlinks")
        raise
    env = shell_environment(tmp_path)
    log = tmp_path / "calls.jsonl"
    env["OFFLINE_LAUNCH_LOG"] = str(log)
    result = subprocess.run(["bash", str(tmp_path / "run.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and "choose it explicitly" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("args, code", [(["--help"], 0), (["--check"], 1), (["--use-venv"], 2)])
def test_install_shell_read_only_paths(tmp_path, args, code):
    for name in ("install.sh", "setup_offline.py"):
        shutil.copy2(ACQUISITION / name, tmp_path / name)
    before = contents(tmp_path)
    result = subprocess.run(["bash", str(tmp_path / "install.sh"), *args],
                            env=shell_environment(tmp_path), capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    assert contents(tmp_path) == before


def test_requirements_include_only_offline_ui_dependencies():
    requirements = {line.split(">=")[0] for line in (ACQUISITION / "requirements.txt").read_text().splitlines()
                    if line and not line.startswith("#")}
    assert requirements == {"click", "rich", "questionary"}
