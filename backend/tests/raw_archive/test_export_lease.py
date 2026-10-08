"""A paused catalog exporter must withdraw readiness before releasing its lease."""

import json
import runpy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive.health import SafetyPause


def test_resource_pause_withdraws_ready_marker_before_unlock(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["hold-export"])
    script = Path(__file__).resolve().parents[3] / "deploy/raw-archive/acceptance/hold-export.py"
    scope = runpy.run_path(str(script))
    main = scope["main"]
    settings = main.__globals__
    settings["ROOT"] = tmp_path
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    monkeypatch.setattr(settings["os"], "geteuid", lambda: 0)
    monkeypatch.setattr(settings["os"], "chown", lambda *args: None)
    monkeypatch.setattr(settings["pwd"], "getpwnam", lambda _: SimpleNamespace(pw_gid=0))
    settings["backup_ledger"] = lambda source, dest, checkpoint: dest.write_bytes(b"backup")
    calls = 0

    def healthy():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise SafetyPause("host_load", {"load": 2.5, "maximum": 2.4})

    settings["healthy"] = healthy

    @contextmanager
    def lease(_):
        try:
            yield
        finally:
            files = list(tmp_path.glob("session-*/lease.json"))
            assert len(files) == 1
            assert json.loads(files[0].read_text())["ready"] is False

    settings["lease"] = lease
    assert main() == 75
    assert len(list(tmp_path.glob("session-*/ledger.sqlite3"))) == 1


def operation_scope(tmp_path, monkeypatch, operation):
    monkeypatch.setattr("sys.argv", ["hold-export", "--operation", operation])
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    script = Path(__file__).resolve().parents[3] / "deploy/raw-archive/acceptance/hold-export.py"
    main = runpy.run_path(str(script))["main"]
    settings = main.__globals__
    settings["ROOT"] = tmp_path
    monkeypatch.setattr(settings["os"], "geteuid", lambda: 0)
    monkeypatch.setattr(settings["os"], "chown", lambda *args: None)
    monkeypatch.setattr(settings["pwd"], "getpwnam", lambda _: SimpleNamespace(pw_gid=0))

    @contextmanager
    def lease(_):
        yield

    settings["lease"] = lease
    return main, settings


def test_sql_lease_never_repeats_journal_backup(tmp_path, monkeypatch):
    import pytest

    main, settings = operation_scope(tmp_path, monkeypatch, "sql")
    settings["backup_ledger"] = lambda *a, **kw: pytest.fail("Journal backup must be separate")
    count = 0

    def healthy():
        nonlocal count
        count += 1
        if count > 1:
            raise SafetyPause("memory")

    settings["healthy"] = healthy
    assert main() == 75
    state = json.loads(next(tmp_path.glob("session-*/lease.json")).read_text())
    assert state["ready"] is False and state["ledger"] is None
    assert not list(tmp_path.glob("session-*/ledger.sqlite3"))


def test_journal_backup_releases_lease_before_local_processing(tmp_path, monkeypatch):
    main, settings = operation_scope(tmp_path, monkeypatch, "backup")
    settings["healthy"] = lambda: None
    settings["backup_ledger"] = lambda source, dest, checkpoint: dest.write_bytes(b"backup")
    assert main() == 0
    assert len(list(tmp_path.glob("session-*/ledger.sqlite3"))) == 1
    assert not list(tmp_path.glob("session-*/lease.json"))
