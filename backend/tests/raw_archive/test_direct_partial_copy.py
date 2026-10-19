"""Unsealed immutable revisions may be copied, never approved for source eviction."""

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.direct.assembler import assemble_one
from app.direct.config import DirectSettings
from app.direct.models import Segment
from app.raw_archive.catalog import DirectCatalog
from app.raw_archive.policy import ArchiveBlocked
from tests.direct.conftest import build_pipeline


def test_copy_old_partial_revision_survives_late_ingest(tmp_path):
    cfg = DirectSettings(
        database_url=f"sqlite:///{tmp_path}/direct.db",
        ingest_enabled=True,
        require_tls=False,
        artifact_root=tmp_path / "artifacts",
        idle_seconds=1,
    )
    p = build_pipeline(cfg)
    sessions = sessionmaker(bind=p.engine)
    copy = DirectCatalog(sessions, "greenmind-direct-test", copy_only=True)
    eviction = DirectCatalog(sessions, "greenmind-direct-test")
    try:
        assert p.upload(bytes(1200)).status_code == 201
        assert assemble_one(p.engine, cfg, p.store, now=time.time() + 2)
        with sessions() as db:
            segment_id = db.query(Segment).one().id
        first = copy.inspect(segment_id, 1)
        before = copy.fingerprints([[segment_id, 1]])
        assert (
            p.upload(
                bytes([2]) * 1200, p.metadata(bytes([2]) * 1200, sequence=1, first_frame=100)
            ).status_code
            == 201
        )
        copy.revalidate(first[0])
        assert copy.inspect(segment_id, 1) == first
        assert assemble_one(p.engine, cfg, p.store, now=time.time() + 2)
        assert copy.inspect(segment_id, 1) == first
        assert copy.fingerprints([[segment_id, 1]]) == before
        boundary = datetime.fromtimestamp(time.time() + 4, UTC)
        assert sorted(copy.page(None, boundary, 10)) == [[segment_id, 1], [segment_id, 2]]
        recent = copy.recent_page(None, datetime.fromtimestamp(0, UTC), boundary, 10)
        assert sorted(item[0] for item in recent) == [[segment_id, 1], [segment_id, 2]]
        with pytest.raises(ArchiveBlocked, match="sealed"):
            eviction.inspect(segment_id, 1)
        with pytest.raises(ArchiveBlocked, match="sealed"):
            eviction.inspect(segment_id, 2)
    finally:
        p.client.close()
        p.engine.dispose()
