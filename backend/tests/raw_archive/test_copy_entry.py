"""Copy-only production entry cannot acquire deletion through configuration."""

import os
import runpy
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
