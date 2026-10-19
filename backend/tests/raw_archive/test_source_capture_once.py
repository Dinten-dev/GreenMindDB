"""Local-source capture pauses before opening SQL or altering schedules."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.raw_archive import source_capture_once as worker
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked


def configured(monkeypatch):
    monkeypatch.setattr(worker.os, "uname", lambda: SimpleNamespace(nodename="Biolingo"))
    monkeypatch.setattr(
        worker.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=worker.os.geteuid())
    )
    for key, value in {
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_COORDINATION_DIR": "/var/lib/greenmind-archive-coordination",
        "RAW_ARCHIVE_GATEWAY_DATABASE_URL": "postgresql://greenmind_wav_metadata_b4ae52d674aa@127.0.0.1/plantdb",
        **{
            key: "false"
            for key in (
                "RAW_ARCHIVE_ENABLED",
                "RAW_ARCHIVE_DELETE_ENABLED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
            )
        },
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAW_ARCHIVE_WORKER_ROLE", raising=False)
    monkeypatch.delenv("RAW_ARCHIVE_SOURCE_PROBE_URL", raising=False)


@pytest.mark.parametrize(
    "key,value",
    [
        ("RAW_ARCHIVE_DELETE_ENABLED", "true"),
        ("RAW_ARCHIVE_ENVIRONMENT", "staging"),
        ("RAW_ARCHIVE_WORKER_ROLE", "archive"),
        ("RAW_ARCHIVE_SOURCE_PROBE_URL", "http://127.0.0.1:18150/source-health"),
    ],
)
def test_unsafe_configuration_opens_no_sql_or_output(tmp_path, monkeypatch, key, value):
    configured(monkeypatch)
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(worker, "create_engine", lambda *a, **k: pytest.fail("No SQL"))
    with pytest.raises(ArchiveBlocked):
        worker.run(tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


def test_memory_pause_preserves_reserves_before_sql(tmp_path, monkeypatch):
    configured(monkeypatch)

    class Probe:
        last = {"reason": "memory"}

        def __call__(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(worker, "health_probe", Probe)
    monkeypatch.setattr(worker, "lease", lambda _: nullcontext())
    monkeypatch.setenv("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")
    monkeypatch.setenv("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")
    monkeypatch.setattr(worker, "create_engine", lambda *a, **k: pytest.fail("No SQL"))
    with pytest.raises(SafetyPause, match="memory"):
        worker.run(tmp_path / "capture")
    assert not (tmp_path / "capture").exists()
    assert worker.os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] == "640"
    assert worker.os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] == "2.4"
