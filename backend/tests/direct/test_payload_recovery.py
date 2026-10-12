"""Restore identities, inline chunks and archived references into an empty PostgreSQL DB."""

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.direct.database import transaction
from app.direct.models import Base, Budget, Chunk, PayloadReference
from app.direct.models import Session as CaptureSession
from app.direct.payloads import WavReader, offload_verified_chunks, read_payload
from app.raw_archive import recovery
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.visualization.direct import initialize
from tests.direct.test_payloads import archive_copy, publish
from tests.direct.test_postgres import pg_pipeline as pg_pipeline

pytestmark = pytest.mark.integration


def test_private_snapshot_restores_exact_chunks_and_retry_identities(pg_pipeline, tmp_path):
    p = pg_pipeline
    original = bytes([1]) * 1200
    assert p.upload(original).status_code == 201
    publish(p)
    archived = archive_copy(p, tmp_path / "box")
    assert offload_verified_chunks(p.engine, p.store, archived) == 1200
    inline = bytes([2]) * 1200
    assert p.upload(inline, p.metadata(inline, sequence=1, first_frame=100)).status_code == 201
    initialize(p.engine)
    readonly = create_engine(
        p.engine.url, connect_args={"options": "-c default_transaction_read_only=on"}
    )
    name = "greenmind_restore_" + uuid.uuid4().hex
    admin = create_engine(p.engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target = None
    try:
        path = tmp_path / "copy-direct.jsonl.gz"
        receipt = recovery.backup_metadata(readonly, "direct", path)
        assert "direct_chunk_payload_ref" in receipt["tables"]
        with admin.connect() as connection:
            connection.execute(text('CREATE DATABASE "' + name + '"'))
        target = create_engine(p.engine.url.set(database=name))
        Base.metadata.create_all(target)
        initialize(target)
        assert recovery.restore_metadata(path, checksum(path), target) == receipt["rows"]
        with transaction(target) as db:
            assert db.get(Budget, 1).used_bytes == 1200
            assert db.query(PayloadReference).count() == 1
            chunks = db.query(Chunk).order_by(Chunk.sequence).all()
            config = db.get(CaptureSession, (chunks[0].device_id, chunks[0].session_id)).config
            assert [read_payload(db, chunk, config, WavReader(archived)) for chunk in chunks] == [
                original,
                inline,
            ]
            assert [chunk.digest for chunk in chunks] == [
                p.metadata(original).digest(),
                p.metadata(inline, sequence=1, first_frame=100).digest(),
            ]
        with pytest.raises(ArchiveBlocked, match="not empty"):
            recovery.restore_metadata(path, checksum(path), target)
    finally:
        readonly.dispose()
        if target:
            target.dispose()
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE IF EXISTS "' + name + '"'))
        admin.dispose()
        # These tables belong only to this test; the shared fixture drops Base next.
        with p.engine.begin() as connection:
            for table in ("direct_visual_point", "direct_visual_worker", "direct_visual_segment"):
                connection.execute(text('DROP TABLE IF EXISTS "' + table + '"'))
