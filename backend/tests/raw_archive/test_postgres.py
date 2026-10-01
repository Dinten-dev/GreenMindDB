"""Opt-in disposable local PostgreSQL catalog/readonly integration tests."""

import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.direct.models import Base as DirectBase
from app.direct.models import Device, Revision, Segment
from app.models.wav_file import WavFile
from app.raw_archive.catalog import DirectCatalog, GatewayCatalog, canonical
from app.raw_archive.daily import Limits, run_daily
from app.raw_archive.policy import ArchiveBlocked, Config
from app.raw_archive.runner import require_scan_index

from .test_daily import Destination, Source, wav_bytes


@pytest.fixture
def postgres():
    url = os.environ.get("RAW_ARCHIVE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set RAW_ARCHIVE_TEST_POSTGRES_URL to a disposable local archive_test DB")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database == "archive_test"
    schema = "raw_archive_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    writable = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    readonly = create_engine(
        url,
        connect_args={
            "options": f"-c search_path={schema} -c default_transaction_read_only=on "
            "-c statement_timeout=3000 -c lock_timeout=500",
        },
    )
    try:
        WavFile.__table__.create(writable)
        DirectBase.metadata.create_all(writable)
        with writable.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX raw_backup_gateway_arrival "
                    "ON wav_file(created_at,id) WHERE raw_deleted_at IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX raw_backup_direct_arrival ON "
                    "direct_revision(verified_at,segment_id,revision) "
                    "WHERE raw_deleted_at IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE direct_visual_segment "
                    "(segment_id text, source_revision int, error text)"
                )
            )
            connection.execute(text("CREATE TABLE direct_visual_point (segment_id text)"))
        yield sessionmaker(bind=writable), sessionmaker(bind=readonly)
    finally:
        writable.dispose()
        readonly.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_postgres_both_catalogs_all_runs_pagination_and_readonly(postgres, tmp_path):
    writes, reads = postgres
    now = datetime.now(UTC)
    old = now - timedelta(hours=2)
    source, destination = Source(), Destination()
    with writes() as db:
        for number in range(1, 15):
            key, data = f"gateway/{number}.wav", wav_bytes(number)
            source.data[key] = data
            db.add(
                WavFile(
                    id=UUID(int=number),
                    sensor_id=uuid4(),
                    gateway_id=uuid4(),
                    sensor_mac="11:22:33:44:55:66",
                    s3_key=key,
                    content_sha256=hashlib.sha256(data).hexdigest(),
                    file_size_bytes=len(data),
                    duration_seconds=2,
                    started_at=old,
                    ended_at=old + timedelta(seconds=2),
                    created_at=old,
                )
            )
        device_id, session_id, segment_id = str(uuid4()), str(uuid4()), str(uuid4())
        db.add(
            Device(
                id=device_id,
                organization_id=str(uuid4()),
                zone_id=str(uuid4()),
                mode="raw",
                key_hash="a" * 64,
            )
        )
        db.flush()
        db.add(
            Segment(
                id=segment_id,
                device_id=device_id,
                session_id=session_id,
                bucket=int(old.timestamp() // 600),
                first_frame=0,
                end_frame=4000,
                revision=1,
                published_revision=1,
                sealed=True,
                updated_at=old.timestamp(),
            )
        )
        db.flush()
        runs = []
        for number in range(20, 25):
            key, data = f"direct/{number}.wav", wav_bytes(number)
            source.data[key] = data
            runs.append(
                dict(
                    key=key,
                    sha256=hashlib.sha256(data).hexdigest(),
                    frame_count=760,
                    started_at_us=int(old.timestamp() * 1e6),
                    features=[{"mean": number}],
                )
            )
        manifest = dict(
            device_id=device_id,
            session_id=session_id,
            revision=1,
            config=dict(channels=1, sample_bits=16, sample_rate=380),
            runs=runs,
        )
        db.add(
            Revision(
                segment_id=segment_id,
                revision=1,
                manifest=manifest,
                manifest_sha256=canonical(manifest),
                verified_at=old.timestamp(),
            )
        )
        db.commit()
    catalogs = {
        "gateway": GatewayCatalog(reads, copy_only=True),
        "direct": DirectCatalog(reads, "greenmind-direct-staging-test", copy_only=True),
    }
    for kind in catalogs:
        require_scan_index(reads.kw["bind"], kind)
    config = Config(enabled=True, root=tmp_path, namespace="staging", max_files=4, min_free_bytes=0)
    for _ in range(16):
        run_daily(
            config,
            catalogs,
            dict.fromkeys(catalogs, source),
            destination,
            healthy=lambda: True,
            source_identities=dict.fromkeys(catalogs, "isolated-pg"),
            limits=Limits(references=8, page_size=2),
        )
    assert len(destination.data) == len(source.data) == 19
    assert len(source.reads) == 19
    assert all(payload in source.data.values() for payload in destination.data.values())
    with reads() as db, pytest.raises(DBAPIError, match="read-only transaction"):
        db.execute(text("DELETE FROM wav_file"))
    with writes() as db:
        assert db.query(WavFile).count() == 14
        assert db.query(Revision).one().raw_deleted_at is None
    # Feature/projection gating stays strict for future deletion, even after copy succeeds.
    with pytest.raises(ArchiveBlocked):
        DirectCatalog(reads, "greenmind-direct-staging-test").inspect(segment_id, 1)
    record = catalogs["gateway"].inspect(UUID(int=1))
    with pytest.raises(ArchiveBlocked):
        replace(record, feature_digest="").eligible(now, config)
    with writes() as db:
        revision = db.get(Revision, (segment_id, 1))
        revision.manifest_sha256 = "f" * 64
        db.commit()
    with pytest.raises(ArchiveBlocked, match="manifest checksum"):
        catalogs["direct"].inspect(segment_id, 1)


def test_postgres_source_changes_are_blocked(postgres):
    writes, reads = postgres
    now = datetime.now(UTC) - timedelta(hours=1)
    identifier = uuid4()
    with writes() as db:
        db.add(
            WavFile(
                id=identifier,
                sensor_id=uuid4(),
                gateway_id=uuid4(),
                sensor_mac="11:22:33:44:55:66",
                s3_key="original.wav",
                content_sha256="a" * 64,
                file_size_bytes=1520,
                duration_seconds=2,
                started_at=now,
                ended_at=now,
                created_at=now,
            )
        )
        db.commit()
    catalog = GatewayCatalog(reads, copy_only=True)
    recording = catalog.inspect(identifier)
    fingerprint = catalog.fingerprints([[str(identifier)]])
    with writes() as db:
        wav = db.get(WavFile, identifier)
        wav.content_sha256 = "b" * 64
        db.commit()
    assert fingerprint != catalog.fingerprints([[str(identifier)]])
    with pytest.raises(ArchiveBlocked, match="identity changed"):
        catalog.revalidate(recording)


def test_missing_index_and_writable_connection_are_rejected(postgres):
    writes, reads = postgres
    with pytest.raises(ArchiveBlocked, match="not read-only"):
        require_scan_index(writes.kw["bind"], "gateway")
    with writes() as db:
        db.execute(text("DROP INDEX raw_backup_gateway_arrival"))
        db.commit()
    with pytest.raises(ArchiveBlocked, match="Missing valid read index"):
        require_scan_index(reads.kw["bind"], "gateway")
