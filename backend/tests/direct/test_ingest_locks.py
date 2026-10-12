"""One sensor's WAV or cleanup wait must not lock another sensor's quota."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event

from app.direct.assembler import assemble_one, release_verified_chunks
from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Device, Revision
from app.direct.payloads import offload_verified_chunks
from tests.direct.conftest import build_pipeline
from tests.direct.test_payloads import archive_copy, publish

pytest_plugins = ("tests.direct.test_postgres",)
pytestmark = pytest.mark.integration


def test_slow_wav_and_late_arrival_do_not_block_other_sensor(pg_pipeline, monkeypatch):
    p = pg_pipeline
    other = build_pipeline(p.cfg)
    entered, resume = Event(), Event()
    original_put = p.store.put

    def delayed_put(*args, **kwargs):
        entered.set()
        assert resume.wait(5), "Test WAV delay was not released"
        return original_put(*args, **kwargs)

    monkeypatch.setattr(p.store, "put", delayed_put)
    try:
        assert p.upload(bytes(1200)).status_code == 201
        with ThreadPoolExecutor(max_workers=3) as pool:
            assembly = pool.submit(assemble_one, p.engine, p.cfg, p.store, now=time.time() + 2)
            try:
                assert entered.wait(2)
                late = pool.submit(
                    p.upload, bytes(1200), p.metadata(bytes(1200), sequence=1, first_frame=100)
                )
                # Storage IO must not keep either sensor waiting on a row lock.
                assert late.result(timeout=1).status_code == 201
                response = pool.submit(other.upload, bytes(1200)).result(timeout=1)
                assert response.status_code == 201
            finally:
                resume.set()
            assert assembly.result(timeout=3) is False
            assert late.result(timeout=3).status_code == 201
        with transaction(p.engine) as db:
            assert db.query(Chunk).count() == 3
            assert db.get(Budget, 1).used_bytes == 3600
    finally:
        resume.set()
        other.client.close()
        other.engine.dispose()


def test_offload_cleanup_and_late_retries_keep_one_device_budget_exact(pg_pipeline, tmp_path):
    p = pg_pipeline
    original, late = bytes(1200), bytes([1]) * 1200
    assert p.upload(original).status_code == 201
    sealed = p.metadata(
        original, session_id=str(uuid.uuid4()), session_start_us=p.start_us + 599_800_000
    )
    assert p.upload(original, sealed).status_code == 201
    publish(p)
    archived = archive_copy(p, tmp_path / "independent-box")
    with transaction(p.engine) as db:
        keys = {run["key"] for rev in db.query(Revision) for run in rev.manifest["runs"]}

    def operate(index):
        if index % 4 == 0:
            return offload_verified_chunks(p.engine, p.store, archived)
        if index % 4 == 1:
            return release_verified_chunks(p.engine)
        if index % 4 == 2:
            assert p.upload(original).json()["status"] == "duplicate"
        else:
            result = p.upload(late, p.metadata(late, sequence=1, first_frame=100))
            assert result.status_code in (200, 201)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(operate, range(16)))
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 3
        inline = db.query(Chunk).filter(Chunk.payload.isnot(None)).all()
        assert len(inline) == 1 and inline[0].payload == late
        assert db.get(Budget, 1).used_bytes == db.get(Device, p.device_id).spool_bytes == 1200
    assert all(p.store.get_local(key) == archived(key) for key in keys)


def test_worker_chunk_selection_does_not_hold_other_sensor_quota(pg_pipeline):
    p = pg_pipeline
    other = build_pipeline(p.cfg)
    entered, resume = Event(), Event()

    def delayed_selection(connection, cursor, statement, parameters, context, executemany):
        if "FROM direct_chunk" in statement and "FOR UPDATE" in statement:
            entered.set()
            assert resume.wait(5), "Test chunk scan was not released"

    try:
        # A complete segment permits the existing sealed-chunk cleanup path.
        payload = bytes(1200)
        meta = p.metadata(payload, session_start_us=p.start_us + 599_800_000)
        assert p.upload(payload, meta).status_code == 201
        assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
        event.listen(p.engine, "before_cursor_execute", delayed_selection)
        with ThreadPoolExecutor(max_workers=2) as pool:
            cleanup = pool.submit(release_verified_chunks, p.engine)
            try:
                assert entered.wait(2)
                response = pool.submit(other.upload, payload).result(timeout=1)
                assert response.status_code == 201
            finally:
                resume.set()
            assert cleanup.result(timeout=3) == 1200
        with transaction(p.engine) as db:
            assert db.get(Budget, 1).used_bytes == 1200
            assert db.query(Chunk).filter(Chunk.payload.isnot(None)).count() == 1
    finally:
        resume.set()
        if event.contains(p.engine, "before_cursor_execute", delayed_selection):
            event.remove(p.engine, "before_cursor_execute", delayed_selection)
        other.client.close()
        other.engine.dispose()
