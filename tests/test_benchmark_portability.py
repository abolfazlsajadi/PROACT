"""Benchmark entry points reject unusable baselines before running any workload."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "scripts/benchmark_startup.py",
    "scripts/benchmark_storage.py",
    "tests/test_gui_benchmark.py",
)


@pytest.mark.parametrize("relative", SCRIPTS)
@pytest.mark.parametrize("arguments", [[], ["--baseline", "not-a-local-commit"]])
def test_benchmark_requires_a_resolvable_explicit_baseline(relative, arguments, tmp_path):
    result = subprocess.run([sys.executable, "-B", str(ROOT / relative), *arguments],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert "--baseline" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("relative", SCRIPTS)
def test_documentation_only_commit_is_not_a_benchmark_baseline(relative, tmp_path):
    """A public docs-only history must fail before imports, timers or writes."""
    repository = tmp_path / "release"
    repository.mkdir()
    script = repository / relative
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / relative, script)
    (repository / "README.md").write_text("Documentation-only fixture\n")
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME="Offline test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Offline test", GIT_COMMITTER_EMAIL="test@example.invalid")
    for arguments in (["init", "-q"], ["add", "README.md"],
                      ["-c", "commit.gpgsign=false", "commit", "-qm", "Documentation fixture"]):
        subprocess.run(["git", *arguments], cwd=repository, env=env, check=True,
                       capture_output=True, text=True)
    before = sorted(path.relative_to(repository) for path in repository.rglob("*") if ".git" not in path.parts)
    result = subprocess.run([sys.executable, "-B", str(script), "--baseline", "HEAD"],
                            cwd=repository, capture_output=True, text=True)
    assert result.returncode == 2
    assert "--baseline must resolve" in result.stderr
    assert "Traceback" not in result.stderr
    after = sorted(path.relative_to(repository) for path in repository.rglob("*") if ".git" not in path.parts)
    assert after == before
