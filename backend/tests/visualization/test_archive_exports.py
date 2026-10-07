"""A complete ZIP is published only after every original matches its digest."""

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app.visualization import archive_exports as jobs
from app.visualization import archive_exports_worker as worker
from app.visualization.resolution import display_step


def test_display_step_binds_long_windows_without_inventing_finer_values():
    end = datetime.now(UTC)
    assert display_step(end - timedelta(minutes=5), end) == 1
    assert display_step(end - timedelta(days=7), end) == 600
    assert display_step(end - timedelta(days=365), end) == 28800
    with pytest.raises(ValueError):
        display_step(end, end)


def make_job(path, values):
    (path / "jobs.sqlite3").touch()
    db = jobs.connect(path)
    items = [
        dict(
            kind="gateway",
            bucket="greenmind-raw",
            key=f"wav/{i}",
            sha256=hashlib.sha256(value).hexdigest(),
            size=len(value),
            name=f"{i}.wav",
            sample_rate=380,
            channels=1,
            started_at="2026-09-29T00:00:00+00:00",
        )
        for i, value in enumerate(values)
    ]
    identity = str(uuid.uuid4())
    with db:
        db.execute(
            "INSERT INTO jobs(id,user_id,kind,sensor_id,created,status,items) VALUES (?,?,?,?,?,?,?)",
            (identity, "user", "gateway", str(uuid.uuid4()), 1, "working", json.dumps(items)),
        )
    return db, db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()


class Body(io.BytesIO):
    pass


def test_verified_export_is_atomic_and_contains_machine_metadata(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"first wav", b"second wav"])
    values = {"wav/0": b"first wav", "wav/1": b"second wav"}
    monkeypatch.setattr(worker, "headroom", lambda _: None)
    monkeypatch.setattr(worker, "_get_s3_client", lambda: object())
    monkeypatch.setattr(worker, "DirectSettings", lambda: object())
    monkeypatch.setattr(worker, "ArtifactStore", lambda _: object())
    monkeypatch.setattr(worker, "source_for", lambda item, *_: Body(values[item["key"]]))
    worker.make_parts(tmp_path, job, db)
    row = db.execute("SELECT status,parts,completed FROM jobs").fetchone()
    assert row["status"] == "ready" and row["completed"] == 2
    with zipfile.ZipFile(tmp_path / json.loads(row["parts"])[0]) as bundle:
        assert bundle.read("0.wav") == b"first wav"
        assert bundle.read("1.wav") == b"second wav"
        metadata = json.loads(bundle.read("manifest.json"))
        assert metadata[0]["sha256"] == hashlib.sha256(b"first wav").hexdigest()
        assert metadata[0]["sample_rate"] == 380
        assert "key" not in metadata[0] and "bucket" not in metadata[0]
    db.close()


def test_corrupt_source_never_publishes_part(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"expected"])
    monkeypatch.setattr(worker, "headroom", lambda _: None)
    monkeypatch.setattr(worker, "_get_s3_client", lambda: object())
    monkeypatch.setattr(worker, "DirectSettings", lambda: object())
    monkeypatch.setattr(worker, "ArtifactStore", lambda _: object())
    monkeypatch.setattr(worker, "source_for", lambda *_: Body(b"wrong!!!"))
    with pytest.raises(RuntimeError, match="Prüfsumme"):
        worker.make_parts(tmp_path, job, db)
    assert not list(tmp_path.glob("*.zip"))
    assert list(tmp_path.glob("*.part"))  # Private failed evidence is retained.
    assert db.execute("SELECT status FROM jobs").fetchone()[0] != "ready"
    db.close()


def test_resource_pause_keeps_export_queued(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"expected"])
    with db:
        db.execute(
            "UPDATE jobs SET status='queued',created=strftime('%s','now') WHERE id=?",
            (job["id"],),
        )
    db.close()
    monkeypatch.setattr(worker, "root", lambda: tmp_path)
    monkeypatch.setattr(worker, "headroom", lambda _: None)

    def pause(*_):
        raise worker.CapacityPause("host priority")

    monkeypatch.setattr(worker, "make_parts", pause)
    assert worker.run_once()["status"] == "paused_for_host_load"
    with jobs.connect(tmp_path) as reopened:
        row = reopened.execute("SELECT status,completed,error FROM jobs").fetchone()
    assert tuple(row) == ("queued", 0, None)


def test_mid_file_pause_preserves_prior_verified_part_and_resumes(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"first", b"second"])
    values = {"wav/0": b"first", "wav/1": b"second"}
    monkeypatch.setattr(worker, "_get_s3_client", lambda: object())
    monkeypatch.setattr(worker, "DirectSettings", lambda: object())
    monkeypatch.setattr(worker, "ArtifactStore", lambda _: object())
    calls = []

    def source(item, *_):
        calls.append(item["key"])
        return Body(values[item["key"]])

    monkeypatch.setattr(worker, "source_for", source)
    checks = 0

    def guard(_):
        nonlocal checks
        checks += 1
        if checks == 4:
            raise worker.CapacityPause("memory")

    monkeypatch.setattr(worker, "headroom", guard)
    with pytest.raises(worker.CapacityPause):
        worker.make_parts(tmp_path, job, db)
    saved = db.execute("SELECT * FROM jobs").fetchone()
    assert saved["completed"] == 1 and len(json.loads(saved["parts"])) == 1
    monkeypatch.setattr(worker, "headroom", lambda _: None)
    worker.make_parts(tmp_path, saved, db)
    result = db.execute("SELECT * FROM jobs").fetchone()
    assert result["status"] == "ready" and result["completed"] == 2
    assert calls.count("wav/0") == 1
    assert (
        worker.saved_progress(
            [tmp_path / p for p in json.loads(result["parts"])], json.loads(job["items"])
        )
        == 2
    )
    db.close()
