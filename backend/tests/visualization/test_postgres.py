"""Only a disposable local Timescale database is permitted."""

import gzip
import hashlib
import io
import os
import uuid
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.auth import get_current_user
from app.database import get_db
from app.visualization import worker
from app.visualization.api import app, query_series
from app.visualization.core import bucket
from app.visualization.storage import read_archive

pytestmark = pytest.mark.integration


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    url = os.getenv("VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable local visual_test TimescaleDB")
    parsed = make_url(url)
    assert parsed.host in ("localhost", "127.0.0.1") and parsed.database == "visual_test"
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        db.execute(
            text("""CREATE TABLE IF NOT EXISTS organization(id uuid PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS zone(id uuid PRIMARY KEY,organization_id uuid);
        CREATE TABLE IF NOT EXISTS gateway(id uuid PRIMARY KEY,zone_id uuid);
        CREATE TABLE IF NOT EXISTS sensor(id uuid PRIMARY KEY,gateway_id uuid);
        CREATE TABLE IF NOT EXISTS sensor_reading(timestamp timestamptz,sensor_id uuid,kind text,value double precision,unit text,PRIMARY KEY(timestamp,sensor_id,kind));
        ALTER TABLE sensor_reading ADD COLUMN IF NOT EXISTS p05 double precision;
        ALTER TABLE sensor_reading ADD COLUMN IF NOT EXISTS p95 double precision;
        SELECT create_hypertable('sensor_reading','timestamp',chunk_time_interval=>interval '1 day',if_not_exists=>true);
        CREATE TABLE IF NOT EXISTS wav_file(id uuid PRIMARY KEY,sensor_id uuid,started_at timestamptz,ended_at timestamptz,feature_status text,
          raw_deleted_at timestamptz,timing_status text,coverage_ratio double precision,s3_key text,sample_rate integer,pcm_scale_mv double precision,pcm_offset_mv double precision);
        CREATE TABLE IF NOT EXISTS wav_feature(wav_file_id uuid PRIMARY KEY,source_sha256 text);""")
        )
    worker.initialize(engine)
    with engine.begin() as db:
        db.execute(
            text(
                "TRUNCATE visual_wave,visual_wav,wav_feature,wav_file,visual_reading,visual_chunk,sensor_reading,sensor,gateway,zone,organization CASCADE"
            )
        )
        ids = {key: uuid.uuid4() for key in ("org", "zone", "gateway", "sensor")}
        db.execute(
            text(
                "INSERT INTO organization(id) VALUES (:org); INSERT INTO zone VALUES (:zone,:org); INSERT INTO gateway VALUES (:gateway,:zone); INSERT INTO sensor VALUES (:sensor,:gateway);"
            ),
            ids,
        )
    monkeypatch.setattr(worker, "ARCHIVE", tmp_path)
    monkeypatch.setenv("VISUAL_BATCH_PAUSE", "0")
    start = bucket(datetime.now(UTC) - timedelta(days=21), 86400) + timedelta(hours=12)

    def add(stamp, value):
        with engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO sensor_reading(timestamp,sensor_id,kind,value,unit) VALUES (:stamp,:sid,'bio_signal',:value,'mV') ON CONFLICT DO NOTHING"
                ),
                {"stamp": stamp, "sid": ids["sensor"], "value": value},
            )

    yield engine, ids, start, add
    app.dependency_overrides.clear()
    engine.dispose()


def chunk_for(engine, start):
    worker.track_chunks(engine, datetime.now(UTC))
    with engine.connect() as db:
        return (
            db.execute(
                text(
                    "SELECT v.* FROM visual_chunk v JOIN timescaledb_information.chunks c ON v.chunk_name=c.chunk_schema||'.'||c.chunk_name WHERE v.range_start<=:start AND v.range_end>:start"
                ),
                {"start": start},
            )
            .mappings()
            .one()
        )


def test_snapshot_prune_late_arrival_and_retry(fixture):
    engine, ids, start, add = fixture
    add(start, 10)
    add(start + timedelta(seconds=1), 20)
    chunk = chunk_for(engine, start)
    manifest = worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    assert manifest["rows"] == 2 and len(list(read_archive(manifest))) == 2
    assert worker.prune_chunk(engine, chunk)
    with engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 0
        series = query_series(
            db, ids["sensor"], start - timedelta(minutes=1), start + timedelta(minutes=10), 60
        )
        assert series[0]["data"][0]["value"] == 15
        assert series[0]["data"][0]["resolution_seconds"] == 600
    # The unmodified old ingest path may replay an old identity under a fresh request ID.
    add(start, 999)
    add(start + timedelta(seconds=2), 30)
    new_chunk = chunk_for(engine, start)
    worker.snapshot_chunk(engine, new_chunk, datetime.now(UTC))
    assert worker.prune_chunk(engine, new_chunk)
    with engine.begin() as db:
        result = db.execute(text("SELECT n,total FROM visual_reading")).one()
        assert result == (3, 60)
        # Recent/current reception is entirely unaffected.
    add(datetime.now(UTC), 42)
    with engine.connect() as db:
        assert db.execute(text("SELECT value FROM sensor_reading")).scalar_one() == 42


def test_late_write_invalidates_snapshot_before_prune(fixture):
    engine, ids, start, add = fixture
    add(start, 5)
    chunk = chunk_for(engine, start)
    worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    add(start + timedelta(seconds=1), 7)
    assert worker.prune_chunk(engine, chunk) is False
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 2


def test_busy_historical_chunk_is_skipped_without_waiting(fixture):
    engine, ids, start, add = fixture
    add(start, 5)
    chunk = chunk_for(engine, start)
    worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    with engine.connect() as reader:
        reader.execute(text("SELECT * FROM sensor_reading")).all()
        with pytest.raises(Exception, match="lock"):
            worker.prune_chunk(engine, chunk)
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 1


def test_unverified_wav_blocks_pruning(fixture):
    engine, ids, start, add = fixture
    add(start, 5)
    chunk = chunk_for(engine, start)
    worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO wav_file(id,sensor_id,started_at,ended_at,feature_status) VALUES (:id,:sid,:start,:end,'pending')"
            ),
            {
                "id": uuid.uuid4(),
                "sid": ids["sensor"],
                "start": start,
                "end": start + timedelta(minutes=10),
            },
        )
    assert worker.prune_chunk(engine, chunk) is False


def test_waveform_peaks_scale_and_bins_are_from_verified_pcm(fixture, monkeypatch):
    engine, ids, start, add = fixture
    from app.services import wav_service

    output = io.BytesIO()
    samples = np.array([1, 2, 100, 4] * 380, dtype="<i2")
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(samples.tobytes())
    payload = output.getvalue()
    item = {
        "id": uuid.uuid4(),
        "sensor_id": ids["sensor"],
        "started_at": start,
        "ended_at": start + timedelta(seconds=4),
        "timing_status": "complete",
        "coverage_ratio": 1,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "s3_key": "test.wav",
        "sample_rate": 380,
        "pcm_scale_mv": 2,
        "pcm_offset_mv": 1,
    }
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO wav_file(id,sensor_id,started_at,ended_at,feature_status) VALUES (:id,:sensor_id,:started_at,:ended_at,'verified')"
            ),
            item,
        )
    monkeypatch.setattr(
        wav_service,
        "_get_s3_client",
        lambda: SimpleNamespace(
            get_object=lambda **kwargs: {"ContentLength": len(payload), "Body": io.BytesIO(payload)}
        ),
    )
    worker.process_wav(engine, item, datetime.now(UTC))
    with engine.connect() as db:
        row = db.execute(text("SELECT n,minimum,maximum,total2 FROM visual_wave")).one()
        assert row.n == len(samples) and row.minimum == 3 and row.maximum == 201
        assert row.total2 == float(np.sum((samples.astype(float) * 2 + 1) ** 2))
    item["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        worker.process_wav(engine, item, datetime.now(UTC))


def test_sidecar_auth_and_cross_tenant_isolation(fixture):
    engine, ids, start, add = fixture
    from sqlalchemy.orm import Session

    add(start, 5)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    route = f"/api/v1/sensors/{ids['sensor']}/data?date={start.date()}"
    assert client.get(route).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        organization_id=uuid.uuid4()
    )
    assert client.get(route).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(organization_id=ids["org"])
    result = client.get(route)
    assert result.status_code == 200, result.text
    assert result.json()[0]["data"][0]["value"] == 5
    assert client.get(f"/api/v1/sensors/{ids['sensor']}/export?range=all").status_code == 200


def test_promotion_preserves_counts_extrema_and_exact_identities(fixture):
    engine, ids, start, add = fixture
    from app.visualization.core import merge_readings, unpack_ids

    with engine.begin() as db:
        for offset, value in ((0, 1), (60, 9), (61, 20)):
            point = {"timestamp": start + timedelta(seconds=offset), "value": value}
            stamp = bucket(point["timestamp"], 60)
            previous = (
                db.execute(text("SELECT * FROM visual_reading WHERE bucket=:b"), {"b": stamp})
                .mappings()
                .first()
            )
            state = merge_readings(previous, [point])
            worker.save_reading(
                db,
                state
                | {
                    "sensor_id": ids["sensor"],
                    "kind": "bio_signal",
                    "bucket": stamp,
                    "seconds": 60,
                    "unit": "mV",
                },
            )
    worker.promote(engine, datetime.now(UTC))
    with engine.connect() as db:
        row = db.execute(text("SELECT * FROM visual_reading")).mappings().one()
        assert (row["seconds"], row["n"], row["total"], row["minimum"], row["maximum"]) == (
            600,
            3,
            30,
            1,
            20,
        )
        assert len(unpack_ids(row["identities"])) == 3


def test_corrupt_backup_prevents_data_removal(fixture):
    engine, ids, start, add = fixture
    from pathlib import Path

    add(start, 5)
    chunk = chunk_for(engine, start)
    manifest = worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    Path(manifest["path"]).write_bytes(b"broken backup")
    with pytest.raises(gzip.BadGzipFile):
        worker.prune_chunk(engine, chunk)
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 1


def test_current_ingestion_continues_during_historical_snapshot(fixture):
    from concurrent.futures import ThreadPoolExecutor

    engine, ids, start, add = fixture
    for index in range(50):
        add(start + timedelta(seconds=index), index)
    chunk = chunk_for(engine, start)
    now = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(worker.snapshot_chunk, engine, chunk, now)
        reception = pool.submit(
            lambda: [add(now + timedelta(microseconds=i), i) for i in range(100)]
        )
        assert snapshot.result()["rows"] == 50
        reception.result()
    assert worker.prune_chunk(engine, chunk)
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM sensor_reading")).scalar_one() == 100


def test_resnapshot_across_seven_day_boundary_does_not_double_count(fixture):
    engine, ids, start, add = fixture
    add(start, 10)
    add(start + timedelta(seconds=60), 20)
    chunk = chunk_for(engine, start)
    worker.snapshot_chunk(engine, chunk, start + timedelta(days=3))
    worker.snapshot_chunk(engine, chunk, start + timedelta(days=8))
    with engine.connect() as db:
        assert db.execute(text("SELECT seconds,n,total FROM visual_reading")).one() == (600, 2, 30)


def test_archive_restore_round_trip_preserves_original_rows(fixture, monkeypatch, tmp_path):
    import json
    import sys

    engine, ids, start, add = fixture
    add(start, 1.25)
    add(start + timedelta(microseconds=12345), 7.5)
    chunk = chunk_for(engine, start)
    manifest = worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    assert worker.prune_chunk(engine, chunk)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, "argv", ["worker", "restore", "--manifest", str(path)])
    monkeypatch.setattr(worker, "engine_for_worker", lambda: engine)
    worker.main()
    worker.main()
    with engine.connect() as db:
        assert db.execute(
            text("SELECT timestamp,value FROM sensor_reading ORDER BY timestamp")
        ).all() == [(start, 1.25), (start + timedelta(microseconds=12345), 7.5)]


def test_compressed_chunk_supports_snapshot_late_write_and_pruning(fixture):
    engine, ids, start, add = fixture
    add(start, 10)
    chunk = chunk_for(engine, start)
    with engine.begin() as db:
        db.execute(
            text(
                "ALTER TABLE sensor_reading SET (timescaledb.compress,timescaledb.compress_segmentby='sensor_id,kind')"
            )
        )
        db.execute(
            text("SELECT compress_chunk(CAST(:name AS regclass))"), {"name": chunk["chunk_name"]}
        )
    worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    add(start + timedelta(seconds=1), 20)
    assert not worker.prune_chunk(engine, chunk)
    worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    assert worker.prune_chunk(engine, chunk)
    with engine.connect() as db:
        assert db.execute(text("SELECT n,total FROM visual_reading")).one() == (2, 30)


def test_quality_metadata_blocks_removal_for_existing_feature_workers(fixture):
    engine, ids, start, add = fixture
    add(start, 10)
    with engine.begin() as db:
        db.execute(
            text(
                "ALTER TABLE sensor_reading ADD COLUMN IF NOT EXISTS source_dropped_samples_total bigint"
            )
        )
        db.execute(text("UPDATE sensor_reading SET source_dropped_samples_total=2"))
    chunk = chunk_for(engine, start)
    manifest = worker.snapshot_chunk(engine, chunk, datetime.now(UTC))
    assert manifest["protected_rows"] == 1
    assert not worker.prune_chunk(engine, chunk)


def test_authenticated_original_waveform_reads_real_scaled_samples(fixture, monkeypatch):
    from sqlalchemy.orm import Session

    from app.services import wav_service

    engine, ids, start, add = fixture
    output = io.BytesIO()
    samples = np.array([1, 2, 100, 4] * 380, dtype="<i2")
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(samples.tobytes())
    payload = output.getvalue()
    item = {
        "id": uuid.uuid4(),
        "sid": ids["sensor"],
        "start": start,
        "end": start + timedelta(seconds=4),
        "sha": hashlib.sha256(payload).hexdigest(),
    }
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO wav_file(id,sensor_id,started_at,ended_at,feature_status,timing_status,coverage_ratio,s3_key,sample_rate,pcm_scale_mv,pcm_offset_mv)
          VALUES(:id,:sid,:start,:end,'verified','complete',1,'preview.wav',380,2,1);
          INSERT INTO wav_feature(wav_file_id,source_sha256) VALUES(:id,:sha)"""),
            item,
        )
    monkeypatch.setattr(
        wav_service,
        "_get_s3_client",
        lambda: SimpleNamespace(
            get_object=lambda **kwargs: {"ContentLength": len(payload), "Body": io.BytesIO(payload)}
        ),
    )

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(organization_id=ids["org"])
    client = TestClient(app)
    route = f"/api/v1/visualization/sensors/{ids['sensor']}/waveform"
    response = client.get(route, params={"at": start.isoformat(), "seconds": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sample_rate"] == 380 and len(body["data"]) == 760
    assert [p["value"] for p in body["data"][:4]] == [3, 5, 201, 9]
    assert (
        client.get(route, params={"at": (start + timedelta(hours=1)).isoformat()}).status_code
        == 404
    )
    with engine.begin() as db:
        db.execute(text("UPDATE wav_feature SET source_sha256=:sha"), {"sha": "0" * 64})
    assert client.get(route, params={"at": start.isoformat()}).status_code == 503
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        organization_id=uuid.uuid4()
    )
    assert client.get(route, params={"at": start.isoformat()}).status_code == 404


def test_future_migration_autogeneration_preserves_visual_tables(fixture):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import MetaData

    from app.visualization.schema_guard import include_object

    engine, ids, start, add = fixture
    with engine.connect() as db:
        context = MigrationContext.configure(db, opts={"include_object": include_object})
        changes = compare_metadata(context, MetaData())
    dropped = {change[1].name for change in changes if change[0] == "remove_table"}
    assert not any(name.startswith("visual_") for name in dropped)
    assert "sensor" in dropped  # The filter does not hide ordinary application tables.


def test_release_readiness_uses_authenticated_real_api_and_database(fixture, monkeypatch):
    import json
    import time
    import urllib.error
    from urllib.parse import urlsplit

    from sqlalchemy.orm import Session

    from app.direct.models import Base as DirectBase
    from app.models.user import Role, User
    from app.services import wav_service
    from app.visualization import direct, release_check

    engine, ids, start, add = fixture
    with engine.begin() as db:
        db.execute(
            text(
                "ALTER TABLE organization ADD COLUMN IF NOT EXISTS name text; ALTER TABLE organization ADD COLUMN IF NOT EXISTS created_at timestamptz"
            )
        )
    User.__table__.create(engine, checkfirst=True)
    DirectBase.metadata.create_all(engine)
    direct.initialize(engine)
    with engine.begin() as db:
        db.execute(text("TRUNCATE users,direct_device CASCADE"))
        db.execute(
            text(
                "INSERT INTO visual_worker(name,status,updated_at) VALUES('compactor','healthy',now()) ON CONFLICT(name) DO UPDATE SET status='healthy',updated_at=now()"
            )
        )
        db.execute(
            text(
                "INSERT INTO direct_visual_worker(id,status,updated_at) VALUES(1,'healthy',now()) ON CONFLICT(id) DO UPDATE SET status='healthy',updated_at=now()"
            )
        )
    with Session(engine) as db, db.begin():
        db.add(
            User(
                email="release-local@example.invalid",
                password_hash="unused",
                role=Role.ADMIN,
                organization_id=ids["org"],
                is_active=True,
                is_verified=True,
            )
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(np.ones(1520, dtype="<i2").tobytes())
    payload = output.getvalue()
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO wav_file(id,sensor_id,started_at,ended_at,feature_status,timing_status,coverage_ratio,s3_key,sample_rate,pcm_scale_mv,pcm_offset_mv)
        VALUES(:id,:sid,:start,:end,'verified','complete',1,'release.wav',380,1,0);
        INSERT INTO wav_feature(wav_file_id,source_sha256) VALUES(:id,:sha)"""),
            {
                "id": uuid.uuid4(),
                "sid": ids["sensor"],
                "start": start,
                "end": start + timedelta(seconds=4),
                "sha": hashlib.sha256(payload).hexdigest(),
            },
        )
    add(start, 1)
    monkeypatch.setattr(
        wav_service,
        "_get_s3_client",
        lambda: SimpleNamespace(
            get_object=lambda **kwargs: {"ContentLength": len(payload), "Body": io.BytesIO(payload)}
        ),
    )
    monkeypatch.setattr(release_check, "read_engine", engine)
    monkeypatch.setattr(
        release_check,
        "resources",
        lambda: (
            engine,
            SimpleNamespace(
                client=SimpleNamespace(list_objects_v2=lambda **kwargs: {}),
                settings=SimpleNamespace(s3_bucket="local-only"),
            ),
        ),
    )
    monkeypatch.setenv("FRONTEND_URL", "https://test.green-mind.ch")
    monkeypatch.setenv("RELEASE_REVISION", "a" * 40)
    monkeypatch.setenv("RELEASE_ID", "local-candidate")
    monkeypatch.setenv("RELEASE_WORKERS_STARTED_AFTER", str(time.time() - 60))
    monkeypatch.delenv("RELEASE_CHECK_BASE", raising=False)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    statuses = []
    stale_route = [False]

    class Opener:
        def open(self, request, timeout):
            url = urlsplit(request.full_url)
            response = client.get(
                url.path + ("?" + url.query if url.query else ""),
                headers=dict(request.header_items()),
            )
            statuses.append(response.status_code)
            if response.status_code >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, response.status_code, "test", {}, io.BytesIO(response.content)
                )
            content = response.content
            if stale_route[0] and url.path == "/health":
                content = json.dumps(response.json() | {"release_revision": "previous"}).encode()
            body = io.BytesIO(content)
            body.status = response.status_code
            return body

    monkeypatch.setattr(release_check.urllib.request, "build_opener", lambda *_: Opener())
    assert release_check.workers_ready() == {"passed": True}
    result = release_check.check()
    assert result["passed"] and result["gateway_waveform_samples"] == 760
    assert result["direct_bootstrap_without_devices"] and not result["direct_data_verified"]
    assert statuses.count(401) == 2 and 404 in statuses
    stale_route[0] = True
    monkeypatch.setattr(release_check.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="not the candidate release"):
        release_check.check()
    stale_route[0] = False
    # A stale heartbeat must fail even when the HTTP health route still returns 200.
    with engine.begin() as db:
        db.execute(text("UPDATE direct_visual_worker SET updated_at=now()-interval '10 minutes'"))
    with pytest.raises(RuntimeError, match="Direct projection worker unavailable"):
        release_check.check()
