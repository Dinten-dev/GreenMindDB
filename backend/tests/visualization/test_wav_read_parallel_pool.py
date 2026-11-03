"""Slow WAV I/O must leave the real one-connection SQL pool available."""

import concurrent.futures
import hashlib
import io
import json
import threading
import uuid
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import JSON, DateTime, bindparam, create_engine
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.types import TypeDecorator

from app.auth import get_current_user
from app.database import Base, get_db
from app.direct.auth import issue_token
from app.direct.models import Base as DirectBase
from app.direct.models import Device, Revision, Segment
from app.direct.models import Session as CaptureSession
from app.models.master import Gateway, Sensor, Zone
from app.models.user import Organization, Role
from app.models.wav_file import WavFile
from app.rate_limit import limiter
from app.raw_archive import reader as raw_reader
from app.services import wav_service
from app.visualization import compat_downloads, direct, direct_api


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class SQLiteReadSession(Session):
    def execute(self, statement, *args, **kwargs):
        # Supply PostgreSQL's native text-query types; retain real SQL and checkout.
        if isinstance(statement, TextClause):
            if "SELECT r.manifest,r.manifest_sha256 FROM direct_revision" in statement.text:
                statement = statement.columns(manifest=JSON())
            elif "SELECT w.*,coalesce(f.source_sha256" in statement.text:
                statement = statement.bindparams(
                    bindparam("sid", type_=PG_UUID(as_uuid=True)),
                    bindparam("at", type_=DateTime()),
                ).columns(started_at=UTCDateTime(), ended_at=UTCDateTime())
        return super().execute(statement, *args, **kwargs)


class BlockFirstObject:
    def __init__(self, engine, payload):
        self.engine, self.payload = engine, payload
        self.entered, self.release = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.calls = 0
        self.checkedout_at_first_object = None

    def read(self, *args, **kwargs):
        with self.lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.checkedout_at_first_object = self.engine.pool.checkedout()
            self.entered.set()
            if not self.release.wait(4):
                raise RuntimeError("Test object gate was not released")
        return self.payload


def outcome(future):
    try:
        response = future.result(timeout=2)
        return response.status_code, response.content
    except Exception as error:
        return type(error).__name__, str(error)


@pytest.fixture
def bounded_readers(tmp_path, monkeypatch):
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("ARCHIVE_COMPAT_READS_ENABLED", "true")
    engines = [
        create_engine(
            f"sqlite:///{tmp_path / (name + '.db')}",
            connect_args={"check_same_thread": False},
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.15,
        )
        for name in ("gateway", "direct")
    ]
    gateway_engine, direct_engine = engines
    Base.metadata.create_all(gateway_engine)
    DirectBase.metadata.create_all(direct_engine)
    organization, zone_id, gateway_id, sensor_id, wav_id = [uuid.uuid4() for _ in range(5)]
    device_id, session_id, segment_id, foreign_device = [str(uuid.uuid4()) for _ in range(4)]
    started = datetime(2026, 10, 1, tzinfo=UTC)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 4, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x02\0\x05\0\xf9\xff\x09\0")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    key = f"direct/{device_id}/{session_id}/{digest}.wav"
    token, token_hash = issue_token(device_id)
    foreign_token, foreign_hash = issue_token(foreign_device)
    cfg = dict(
        sample_bits=16, channels=1, sample_rate=4, session_start_us=int(started.timestamp() * 1e6)
    )
    run = dict(
        key=key,
        sha256=digest,
        first_frame=0,
        frame_count=4,
        started_at_us=cfg["session_start_us"],
    )
    manifest = dict(device_id=device_id, session_id=session_id, revision=1, config=cfg, runs=[run])
    with Session(gateway_engine) as db, db.begin():
        db.add_all(
            [
                Organization(id=organization, name="synthetic"),
                Zone(id=zone_id, organization_id=organization, name="synthetic"),
                Gateway(id=gateway_id, zone_id=zone_id, hardware_id="synthetic"),
                Sensor(id=sensor_id, gateway_id=gateway_id, mac_address="00:00:00:00:00:01"),
                WavFile(
                    id=wav_id,
                    sensor_id=sensor_id,
                    gateway_id=gateway_id,
                    sensor_mac="00:00:00:00:00:01",
                    s3_key="synthetic.wav",
                    content_sha256=digest,
                    feature_status="verified",
                    sample_rate=4,
                    duration_seconds=1,
                    file_size_bytes=len(payload),
                    raw_deleted_at=started,
                    started_at=started,
                    ended_at=started + timedelta(seconds=1),
                ),
            ]
        )
    with Session(direct_engine) as db, db.begin():
        db.add_all(
            [
                Device(
                    id=device_id,
                    organization_id=str(organization),
                    zone_id=str(zone_id),
                    mode="DIRECT",
                    key_hash=token_hash,
                    active=True,
                ),
                Device(
                    id=foreign_device,
                    organization_id=str(uuid.uuid4()),
                    zone_id=str(uuid.uuid4()),
                    mode="DIRECT",
                    key_hash=foreign_hash,
                    active=True,
                ),
                CaptureSession(device_id=device_id, id=session_id, config=cfg, mode="DIRECT"),
                Segment(
                    id=segment_id,
                    device_id=device_id,
                    session_id=session_id,
                    bucket=int(started.timestamp()) // 600,
                    first_frame=0,
                    end_frame=4,
                    received_frames=4,
                    revision=1,
                    published_revision=1,
                    sealed=True,
                ),
                Revision(
                    segment_id=segment_id,
                    revision=1,
                    manifest=manifest,
                    raw_deleted_at=1,
                    manifest_sha256=hashlib.sha256(
                        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                ),
            ]
        )

    def database():
        with SQLiteReadSession(gateway_engine) as session:
            yield session

    def direct_database():
        with SQLiteReadSession(direct_engine) as session:
            yield session

    def actor(request: Request):
        # Only identity is synthetic; actual tenant/zone checks remain.
        return SimpleNamespace(
            organization_id=organization
            if request.headers.get("x-test-tenant") != "foreign"
            else uuid.uuid4(),
            role=Role.ADMIN,
            is_active=True,
            is_verified=True,
            email="synthetic-reader@example.test",
        )

    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[direct_api.connection] = direct_database
    store = SimpleNamespace(get=None, settings=SimpleNamespace(s3_bucket="synthetic"))
    monkeypatch.setattr(direct, "resources", lambda: (direct_engine, store))
    direct_api.install(app)
    compat_downloads.install(app)
    from app.visualization import api

    route = next(
        route
        for route in api.app.routes
        if route.path == "/api/v1/visualization/sensors/{sensor_id}/waveform"
    )
    app.add_api_route(route.path, route.endpoint, methods=["GET"])
    try:
        yield SimpleNamespace(
            app=app,
            gateway_engine=gateway_engine,
            direct_engine=direct_engine,
            store=store,
            payload=payload,
            device_id=device_id,
            segment_id=segment_id,
            sensor_id=sensor_id,
            wav_id=wav_id,
            started=started,
            token=token,
            foreign_token=foreign_token,
        )
    finally:
        for engine in engines:
            engine.dispose()


@pytest.mark.parametrize(
    "path_kind",
    [
        "direct-download",
        "gateway-waveform",
        "gateway-download",
        "gateway-bundle",
        "gateway-files",
        "direct-segments",
        "direct-run-control",
    ],
)
def test_parallel_wav_reads_release_pool_without_bypassing_ownership(
    bounded_readers, monkeypatch, path_kind
):
    fixture = bounded_readers
    engine = fixture.direct_engine if path_kind.startswith("direct") else fixture.gateway_engine
    gate = BlockFirstObject(engine, fixture.payload)
    fixture.store.get = gate.read

    def get_object(*args, **kwargs):
        payload = gate.read()
        return {"ContentLength": len(payload), "Body": io.BytesIO(payload)}

    def stream(*args, **kwargs):
        yield gate.read()

    def verified(kind, bucket, keys):
        if keys:
            gate.read()
        return set(keys)

    monkeypatch.setattr(raw_reader, "get_object", get_object)
    monkeypatch.setattr(raw_reader, "verified_objects", verified)
    monkeypatch.setattr(compat_downloads, "verified_objects", verified)
    monkeypatch.setattr(wav_service, "_get_s3_client", lambda: object())
    monkeypatch.setattr(wav_service, "stream_wav_bytes", stream)
    monkeypatch.setattr(wav_service, "stream_wav_zip", stream)
    paths = {
        "direct-download": f"/api/v1/visualization/direct/{fixture.device_id}/wav/{fixture.segment_id}/1/0",
        "gateway-waveform": f"/api/v1/visualization/sensors/{fixture.sensor_id}/waveform",
        "gateway-download": f"/api/v1/wav/download/{fixture.wav_id}",
        "gateway-bundle": "/api/v1/wav/download-bundle",
        "gateway-files": "/api/v1/wav/files",
        "direct-segments": "/api/v1/direct-ingest/segments",
        "direct-run-control": f"/api/v1/direct-ingest/segments/{fixture.segment_id}/runs/0",
    }
    params = {}
    if path_kind == "gateway-waveform":
        params = {"at": fixture.started.isoformat(), "seconds": 1}
    elif path_kind == "gateway-bundle":
        params = {
            "sensor_id": str(fixture.sensor_id),
            "from_dt": fixture.started.isoformat(),
            "to_dt": (fixture.started + timedelta(seconds=2)).isoformat(),
        }
    headers, foreign_headers = None, {"x-test-tenant": "foreign"}
    if path_kind in {"direct-segments", "direct-run-control"}:
        headers = {"Authorization": "Bearer " + fixture.token}
        foreign_headers = {"Authorization": "Bearer " + fixture.foreign_token}
    with TestClient(fixture.app, base_url="https://testserver") as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(client.get, paths[path_kind], params=params, headers=headers)
            try:
                assert gate.entered.wait(2), outcome(first)
                second = executor.submit(
                    client.get, paths[path_kind], params=params, headers=headers
                )
                parallel = outcome(second)
            finally:
                gate.release.set()
            initial = outcome(first)
        assert initial[0] == 200, initial
        assert parallel[0] == 200, parallel
        assert initial[1] == parallel[1]
        assert gate.checkedout_at_first_object == 0
        assert engine.pool.checkedout() == 0
        calls = gate.calls
        foreign = client.get(paths[path_kind], params=params, headers=foreign_headers)
        if path_kind in {"gateway-files", "direct-segments"}:
            assert foreign.status_code == 200 and foreign.json() == []
        else:
            assert foreign.status_code == 404, foreign.text
        assert gate.calls == calls
        assert engine.pool.checkedout() == 0
        assert engine.pool.size() == 1 and engine.pool.overflow() == 0


def test_legacy_gateway_download_retains_session_and_streaming_headers(
    bounded_readers, monkeypatch
):
    fixture = bounded_readers
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "false")
    gate = BlockFirstObject(fixture.gateway_engine, fixture.payload)
    gate.release.set()

    def stream(key):
        assert key == "synthetic.wav"
        yield gate.read()

    monkeypatch.setattr(wav_service, "stream_wav_bytes", stream)
    with TestClient(fixture.app) as client:
        response = client.get(f"/api/v1/wav/download/{fixture.wav_id}")
    assert response.status_code == 200 and response.content == fixture.payload
    assert response.headers["content-length"] == str(len(fixture.payload))
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "audio/wav"
    assert gate.checkedout_at_first_object == 1
    assert fixture.gateway_engine.pool.checkedout() == 0
