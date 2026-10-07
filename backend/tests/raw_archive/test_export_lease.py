"""A paused catalog exporter must withdraw readiness before releasing its lease."""

import json
import runpy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.raw_archive.health import SafetyPause


def test_resource_pause_withdraws_ready_marker_before_unlock(tmp_path, monkeypatch):
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
