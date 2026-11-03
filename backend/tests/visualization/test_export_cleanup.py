"""Only generated export copies may be retained or cleaned."""

import pytest
from tests.visualization.test_archive_exports import Body, make_job

from app.visualization import archive_exports_worker as worker


def configure(monkeypatch, values):
    monkeypatch.setattr(worker, "headroom", lambda _: None)
    monkeypatch.setattr(worker, "_get_s3_client", lambda: object())
    monkeypatch.setattr(worker, "DirectSettings", lambda: object())
    monkeypatch.setattr(worker, "ArtifactStore", lambda _: object())
    monkeypatch.setattr(worker, "source_for", lambda item, *_: Body(values[item["key"]]))


def test_success_removes_raw_and_duplicate_part_copies(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"original"])
    configure(monkeypatch, {"wav/0": b"original"})
    worker.make_parts(tmp_path, job, db)
    assert list(tmp_path.glob("*.zip"))
    assert not list(tmp_path.glob("*.raw-part"))
    assert not list(tmp_path.glob("*.part"))
    db.close()


def test_failure_removes_incomplete_export_copies(tmp_path, monkeypatch):
    db, job = make_job(tmp_path, [b"expected"])
    configure(monkeypatch, {"wav/0": b"different"})
    with pytest.raises(RuntimeError):
        worker.make_parts(tmp_path, job, db)
    assert not list(tmp_path.glob("*.raw-part"))
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.zip"))
    db.close()


def recent_job(path, *, status="ready", user="user"):
    import json
    import time

    db, job = make_job(path, [b"source"])
    identity = job["id"]
    name = identity + "-000.zip"
    (path / name).write_bytes(b"export copy")
    with db:
        db.execute(
            "UPDATE jobs SET created=?,status=?,user_id=?,parts=? WHERE id=?",
            (time.time(), status, user, json.dumps([name]), identity),
        )
    return db, db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone(), name


@pytest.mark.parametrize("state", ["ready", "failed", "cancelled", "queued", "working"])
def test_expired_job_copies_are_cleaned_without_touching_originals(tmp_path, state):
    from app.visualization.archive_export_storage import ExportStore
    from app.visualization.archive_jobs import EXPIRES

    db, job, name = recent_job(tmp_path, status=state)
    original = tmp_path / "original.wav"
    proof = tmp_path / "restoration.json"
    original.write_bytes(b"preserve original")
    proof.write_bytes(b"preserve evidence")
    with ExportStore(tmp_path) as store, store.locked():
        assert store.reap(db, now=job["created"] + EXPIRES) == 1
    assert not (tmp_path / name).exists()
    assert (
        original.read_bytes() == b"preserve original" and proof.read_bytes() == b"preserve evidence"
    )
    assert db.execute("SELECT status,parts FROM jobs").fetchone()["status"] == "expired"
    db.close()


def test_ready_before_expiry_and_resumable_parts_survive(tmp_path):
    from app.visualization.archive_export_storage import ExportStore

    db, job, name = recent_job(tmp_path, status="working")
    partial = tmp_path / (job["id"] + "-000.part")
    partial.write_bytes(b"incomplete")
    with ExportStore(tmp_path) as store, store.locked():
        store.reap(db)
    assert (tmp_path / name).read_bytes() == b"export copy"
    assert not partial.exists()
    assert db.execute("SELECT status FROM jobs").fetchone()[0] == "queued"
    db.close()


def test_orphan_copies_require_age_and_keep_unknown_files(tmp_path):
    import os
    import time
    import uuid

    from app.visualization.archive_export_storage import ExportStore
    from app.visualization.archive_jobs import connect

    db = connect(tmp_path)
    identity = str(uuid.uuid4())
    old = tmp_path / f"{identity}-000.part"
    fresh = tmp_path / f"{uuid.uuid4()}-000.zip"
    old.write_bytes(b"old generated copy")
    fresh.write_bytes(b"fresh copy")
    unrelated = tmp_path / "unrelated.zip"
    unrelated.write_bytes(b"unattributed")
    os.utime(old, (time.time() - 1000,) * 2)
    with ExportStore(tmp_path) as store, store.locked():
        assert store.reap(db) == 1
    assert not old.exists() and fresh.exists()
    assert unrelated.read_bytes() == b"unattributed"
    db.close()


@pytest.mark.parametrize("shared", [True, False])
def test_live_job_or_download_lock_prevents_even_expired_cleanup(tmp_path, shared):
    import os

    from app.visualization.archive_export_storage import ExportStore
    from app.visualization.archive_jobs import EXPIRES

    db, job, name = recent_job(tmp_path)
    with ExportStore(tmp_path) as holder, holder.locked():
        lock = holder.acquire_job(job["id"], shared=shared)
    try:
        with ExportStore(tmp_path) as store, store.locked():
            assert store.reap(db, now=job["created"] + EXPIRES) == 0
        assert (tmp_path / name).exists()
    finally:
        os.close(lock)
    with ExportStore(tmp_path) as store, store.locked():
        assert store.reap(db, now=job["created"] + EXPIRES) == 1
    db.close()


def test_quotas_include_ready_failed_raw_unknown_and_reserved_copies(tmp_path, monkeypatch):
    import json

    from app.visualization import archive_export_storage as storage

    db, job, name = recent_job(tmp_path, status="failed")
    raw = tmp_path / (job["id"] + "-" + "a" * 32 + ".raw-part")
    raw.write_bytes(b"r" * 10000)
    unknown = tmp_path / "unattributed"
    unknown.write_bytes(b"u" * 10000)
    with storage.ExportStore(tmp_path) as store, store.locked():
        usage = store.inventory()
        assert usage[job["id"]] >= 10011 and usage[None] >= 10000
        monkeypatch.setattr(storage, "USER_BYTES", 10000)
        with pytest.raises(storage.ExportQuota):
            store.budget(db)
        monkeypatch.setattr(storage, "USER_BYTES", 10**9)
        monkeypatch.setattr(storage, "GLOBAL_BYTES", sum(usage.values()) - 1)
        with pytest.raises(storage.ExportQuota):
            store.budget(db)
        monkeypatch.setattr(storage, "GLOBAL_BYTES", 10**9)
        monkeypatch.setattr(
            storage, "USER_BYTES", storage.reservation(json.loads(job["items"])) - 1
        )
        with db:
            db.execute("UPDATE jobs SET status='queued'")
        with pytest.raises(storage.ExportQuota):
            store.budget(db)
    db.close()


def test_quota_failure_cleans_new_output_and_source_body(tmp_path, monkeypatch):
    from app.visualization import archive_export_storage as storage

    db, job = make_job(tmp_path, [b"source"])
    configure(monkeypatch, {"wav/0": b"source"})
    monkeypatch.setattr(storage, "USER_BYTES", 1)
    with pytest.raises(storage.ExportQuota):
        worker.make_parts(tmp_path, job, db)
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.raw-part"))
    assert not list(tmp_path.glob("*.zip"))
    db.close()


def test_cancel_during_copy_discards_all_generated_bytes(tmp_path, monkeypatch):
    from app.visualization.archive_jobs import connect

    db, job = make_job(tmp_path, [b"source"])
    configure(monkeypatch, {"wav/0": b"source"})

    class CancelBody(Body):
        def read(self, size=-1):
            with connect(tmp_path) as other:
                other.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job["id"],))
            return super().read(size)

    body = CancelBody(b"source")
    monkeypatch.setattr(worker, "source_for", lambda *_: body)
    with pytest.raises(worker.ExportCancelled):
        worker.make_parts(tmp_path, job, db)
    assert body.closed
    assert (
        not list(tmp_path.glob("*.zip"))
        and not list(tmp_path.glob("*.raw-part"))
        and not list(tmp_path.glob("*.part"))
    )
    assert db.execute("SELECT status FROM jobs").fetchone()[0] == "cancelled"
    db.close()


def test_scoped_fallback_scratch_is_cleaned_on_failure(tmp_path, monkeypatch):
    from app.raw_archive import reader

    db, job = make_job(tmp_path, [b"source"])
    configure(monkeypatch, {"wav/0": b"source"})

    def failure(*_):
        scratch = reader._export_scratch.get()
        assert scratch.is_relative_to(tmp_path / ".scratch")
        (scratch / "verified.wav").write_bytes(b"temporary fallback copy")
        raise RuntimeError("read failed")

    monkeypatch.setattr(worker, "source_for", failure)
    with pytest.raises(RuntimeError):
        worker.make_parts(tmp_path, job, db)
    assert not (tmp_path / ".scratch" / job["id"]).exists()
    assert reader._export_scratch.get() is None
    db.close()


def test_symlinks_and_parent_aliases_cannot_redirect_cleanup(tmp_path):
    from app.visualization.archive_export_storage import ExportStorageError, ExportStore

    db, job, name = recent_job(tmp_path, status="failed")
    source = tmp_path.parent / "original-canary"
    source.write_bytes(b"original untouched")
    (tmp_path / name).unlink()
    (tmp_path / name).symlink_to(source)
    with ExportStore(tmp_path) as store, store.locked():
        with pytest.raises(ExportStorageError):
            store.reap(db)
    assert source.read_bytes() == b"original untouched"
    alias = tmp_path.parent / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ExportStorageError):
        ExportStore(alias)
    db.close()


def test_empty_queue_worker_still_reaps_expired_exports(tmp_path, monkeypatch):
    from app.visualization.archive_jobs import EXPIRES

    db, job, name = recent_job(tmp_path)
    with db:
        db.execute("UPDATE jobs SET created=created-?", (EXPIRES,))
    db.close()
    monkeypatch.setattr(worker, "root", lambda: tmp_path)
    monkeypatch.setattr(worker, "headroom", lambda _: pytest.fail("No original work required"))
    assert worker.run_once()["status"] == "idle"
    assert not (tmp_path / name).exists()


def test_live_asgi_download_survives_expiry_reap_and_releases_on_disconnect(tmp_path):
    import asyncio
    import os

    from app.visualization.archive_export_storage import ExportStore
    from app.visualization.archive_exports import DownloadResponse
    from app.visualization.archive_jobs import EXPIRES

    db, job, name = recent_job(tmp_path)
    with ExportStore(tmp_path) as store, store.locked():
        lock = store.acquire_job(job["id"], shared=True)
        body = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store.fd)
    response = DownloadResponse(body, lock, name)

    async def send(message):
        if message["type"] == "http.response.body":
            with ExportStore(tmp_path) as reaper, reaper.locked():
                assert reaper.reap(db, now=job["created"] + EXPIRES) == 0
            assert (tmp_path / name).exists()
            raise OSError("Synthetic disconnect")

    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
        "extensions": {"http.response.pathsend": {}},
        "asgi": {"spec_version": "2.4"},
    }
    with pytest.raises(OSError, match="disconnect"):
        asyncio.run(response(scope, receive, send))
    with ExportStore(tmp_path) as reaper, reaper.locked():
        assert reaper.reap(db, now=job["created"] + EXPIRES) == 1
    assert not (tmp_path / name).exists()
    db.close()


def test_snapshot_ignores_only_ephemeral_locks_and_scratch(tmp_path):
    from app.raw_archive.export_checkpoint import checkpoint_candidates
    from app.visualization.archive_export_storage import ExportStore

    db, job, name = recent_job(tmp_path)
    with ExportStore(tmp_path) as store, store.locked():
        lock = store.acquire_job(job["id"])
        scratch = store.scratch_path(job["id"])
        (scratch / "verified.wav").write_bytes(b"incomplete export restore")
    import os

    os.close(lock)
    (tmp_path / "restoration.json").write_text("preserved")
    assert {p.name for p in checkpoint_candidates(tmp_path)} == {name}
    assert (tmp_path / "restoration.json").read_text() == "preserved"
    db.close()


def _hold_job_in_child(path, identity, ready, release):
    import os

    from app.visualization.archive_export_storage import ExportStore

    with ExportStore(path) as store, store.locked():
        lock = store.acquire_job(identity)
        assert lock is not None
    ready.set()
    release.wait(10)
    os._exit(0)  # Model a worker crash: no Python cleanup, OS releases flock.


def test_cross_process_worker_crash_releases_cleanup_lock(tmp_path):
    import multiprocessing

    from app.visualization.archive_export_storage import ExportStore
    from app.visualization.archive_jobs import EXPIRES

    db, job, name = recent_job(tmp_path, status="working")
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_hold_job_in_child, args=(tmp_path, job["id"], ready, release))
    child.start()
    try:
        assert ready.wait(10)
        with ExportStore(tmp_path) as store, store.locked():
            assert store.reap(db, now=job["created"] + EXPIRES) == 0
        release.set()
        child.join(10)
        assert child.exitcode == 0
        with ExportStore(tmp_path) as store, store.locked():
            assert store.reap(db, now=job["created"] + EXPIRES) == 1
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
        child.join()
    db.close()


def api_client(path, monkeypatch, items):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address

    from app.visualization import archive_exports as api

    monkeypatch.setattr(api, "limiter", Limiter(key_func=get_remote_address))
    app = FastAPI()
    app.state.limiter = api.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    api.install(app)
    monkeypatch.setattr(api, "root", lambda: path)
    monkeypatch.setattr(api, "authorize_gateway", lambda *_: None)
    monkeypatch.setattr(api, "select_gateway", lambda *_: items)
    app.dependency_overrides[api.get_current_user] = lambda: SimpleNamespace(id="user")
    app.dependency_overrides[api.get_db] = lambda: None
    return TestClient(app)


def test_download_ranges_and_cancel_preserve_live_reader(tmp_path, monkeypatch):
    import os

    from app.visualization.archive_export_storage import ExportStore

    db, job, name = recent_job(tmp_path)
    with api_client(tmp_path, monkeypatch, []) as client:
        url = f"/api/v1/visualization/archive-exports/{job['id']}"
        response = client.get(url + "/parts/0", headers={"Range": "bytes=0-5"})
        assert response.status_code == 206 and response.content == b"export"
        with ExportStore(tmp_path) as store, store.locked():
            lock = store.acquire_job(job["id"], shared=True)
        try:
            assert client.delete(url).status_code == 200
            assert (tmp_path / name).exists()
            assert client.get(url + "/parts/0").status_code == 404
        finally:
            os.close(lock)
        with ExportStore(tmp_path) as store, store.locked():
            store.reap(db)
        assert not (tmp_path / name).exists()
    db.close()


def test_admission_counts_retained_exports_and_serializes_open_job_cap(tmp_path, monkeypatch):
    import json
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    from app.visualization import archive_export_storage as storage
    from app.visualization.archive_jobs import connect

    db, job, name = recent_job(tmp_path)
    items = json.loads(job["items"])
    params = {
        "kind": "gateway",
        "sensor_id": str(uuid.uuid4()),
        "from_dt": "2026-01-01T00:00:00Z",
        "to_dt": "2026-01-02T00:00:00Z",
    }
    with api_client(tmp_path, monkeypatch, items) as client:
        monkeypatch.setattr(storage, "USER_BYTES", storage.reservation(items))
        response = client.post("/api/v1/visualization/archive-exports", params=params)
        assert response.status_code == 429 and "Speicherlimit" in response.json()["detail"]
        monkeypatch.setattr(storage, "USER_BYTES", 4 * 1024**3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: client.post(
                        "/api/v1/visualization/archive-exports", params=params
                    ).status_code,
                    range(2),
                )
            )
        assert results == [202, 202]
        with connect(tmp_path) as check:
            assert (
                check.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 2
            )
    db.close()


@pytest.mark.parametrize("point", ["link", "fsync", "read", "close"])
def test_failures_leave_no_raw_or_zip_copies(tmp_path, monkeypatch, point):
    import os

    db, job = make_job(tmp_path, [b"source"])
    configure(monkeypatch, {"wav/0": b"source"})
    source_canary = tmp_path / "source-original.wav"
    source_canary.write_bytes(b"original never modified")

    def fail(*args, **kwargs):
        raise OSError("Synthetic failure")

    if point == "link":
        original = os.link

        def linked(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(worker.os, "link", linked)
    elif point == "fsync":
        monkeypatch.setattr(worker.os, "fsync", fail)
    else:

        class FailedBody(Body):
            def read(self, size=-1):
                if point == "read":
                    fail()
                return super().read(size)

            def close(self):
                super().close()
                if point == "close":
                    fail()

        monkeypatch.setattr(worker, "source_for", lambda *_: FailedBody(b"source"))
    with pytest.raises(OSError):
        worker.make_parts(tmp_path, job, db)
    assert (
        not list(tmp_path.glob("*.zip"))
        and not list(tmp_path.glob("*.part"))
        and not list(tmp_path.glob("*.raw-part"))
    )
    assert source_canary.read_bytes() == b"original never modified"
    db.close()
