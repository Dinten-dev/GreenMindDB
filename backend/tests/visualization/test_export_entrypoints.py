"""Empty queues still reap copies without starting source/archive operations."""

import argparse
import runpy
from pathlib import Path

from tests.visualization.test_export_cleanup import recent_job

from app.visualization.archive_jobs import EXPIRES


def test_archive_host_empty_queue_reaps_without_source_lease(tmp_path, monkeypatch):
    from app.raw_archive import archive_export_once as export

    db, job, name = recent_job(tmp_path)
    with db:
        db.execute("UPDATE jobs SET created=created-?", (EXPIRES,))
    db.close()
    monkeypatch.setattr(export, "guard", lambda: None)
    monkeypatch.setattr(export.worker, "root", lambda: tmp_path)
    monkeypatch.setattr(
        export, "catalog_lease", lambda: (_ for _ in ()).throw(AssertionError("No source lease"))
    )
    assert export.run()["status"] == "idle"
    assert not (tmp_path / name).exists()


def test_dispatcher_empty_queue_reaps_without_docker(tmp_path, monkeypatch):
    import subprocess

    db, job, name = recent_job(tmp_path)
    with db:
        db.execute("UPDATE jobs SET created=created-?", (EXPIRES,))
    db.close()
    script = (
        Path(__file__).resolve().parents[3] / "deploy/raw-archive/acceptance/dispatch-export.py"
    )
    module = runpy.run_path(str(script))
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda _: argparse.Namespace(state=tmp_path, compose=tmp_path / "unused.yml"),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: (_ for _ in ()).throw(AssertionError("No Docker"))
    )
    assert module["main"]() == 0
    assert not (tmp_path / name).exists()


def test_deployment_scripts_compile_without_executing_privileged_operations():
    root = Path(__file__).resolve().parents[3]
    for name in (
        "deploy/raw-archive/acceptance/dispatch-export.py",
        "deploy/archive-server/snapshot-source-export-jobs.py",
    ):
        path = root / name
        compile(path.read_text(), str(path), "exec")


def test_root_dispatcher_delegates_cleanup_to_state_owner(tmp_path, monkeypatch):
    import os
    import subprocess
    from types import SimpleNamespace

    script = (
        Path(__file__).resolve().parents[3] / "deploy/raw-archive/acceptance/dispatch-export.py"
    )
    module = runpy.run_path(str(script))
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    class State:
        def stat(self, **kwargs):
            return SimpleNamespace(st_uid=996, st_gid=986)

        def __str__(self):
            return str(tmp_path)

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    module["reap_as_owner"](State())
    assert calls[0][1]["user"] == 996 and calls[0][1]["group"] == 986
    assert calls[0][1]["extra_groups"] == []
    assert set(calls[0][1]["env"]) == {"PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
