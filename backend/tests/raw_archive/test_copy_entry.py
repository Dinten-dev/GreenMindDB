"""Copy-only production entry cannot acquire deletion through configuration."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_copy_entry_overrides_every_destructive_flag(monkeypatch):
    from app.raw_archive import runner

    flags = (
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED",
        "RAW_ARCHIVE_READS_ENABLED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
    )
    for name in flags:
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("RAW_ARCHIVE_ENABLED", "true")

    def main():
        assert all(os.environ[name] == "false" for name in flags)
        assert os.environ["RAW_ARCHIVE_ENABLED"] == "true"
        return 0

    monkeypatch.setattr(runner, "main", main)
    script = Path(__file__).resolve().parents[3] / "deploy/raw-archive/copy-only.py"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")
    assert result.value.code == 0


def test_adaptive_launcher_ignores_stale_environment_and_working_directory(tmp_path):
    stale = tmp_path / "backend"
    (stale / "app/raw_archive").mkdir(parents=True)
    (stale / "app/__init__.py").write_text("")
    (stale / "app/raw_archive/__init__.py").write_text("")
    script = Path(__file__).resolve().parents[3] / "deploy/raw-archive/adaptive-copy.py"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy,sys;runpy.run_path(sys.argv[1]);import app;print(app.__file__)",
            str(script),
        ],
        cwd=stale,
        env={**os.environ, "PYTHONPATH": str(stale)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == script.parents[2] / "backend/app/__init__.py"
