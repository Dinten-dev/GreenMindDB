"""Real file locks prove exclusion, release and cooperative copy yielding."""

import json
import time

import pytest

from app.raw_archive.coordination import lease, request, yield_requested
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


def test_other_job_never_enters_and_copy_yields(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    with lease("copy"):
        with pytest.raises(SafetyPause, match="archive_job_busy"):
            with lease("export"):
                pytest.fail("Concurrent export entered")
        assert yield_requested()
    with pytest.raises(SafetyPause, match="archive_job_yield"):
        with lease("copy"):
            pytest.fail("Copy ignored waiting download")
    with lease("export"):
        assert not yield_requested()
    with lease("copy"):
        pass


def test_expired_request_and_exception_release(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    request(tmp_path, "catalog", time.time() - 1)
    assert not yield_requested()
    with pytest.raises(ValueError):
        with lease("export"):
            raise ValueError()
    with lease("catalog"):
        pass


def test_symlink_lock_cannot_touch_other_file(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    target = tmp_path / "private"
    target.write_text("unchanged")
    (tmp_path / "jobs.lock").symlink_to(target)
    with pytest.raises(OSError):
        with lease("copy"):
            pytest.fail("Symlink followed")
    assert target.read_text() == "unchanged"


def test_invalid_request_fails_closed(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    (tmp_path / "catalog.request").write_text(json.dumps({"expires": "forever"}))
    with pytest.raises(ArchiveBlocked):
        yield_requested()
