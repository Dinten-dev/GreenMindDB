"""Legacy GET-only compatibility: same authorization, no ingestion surface."""

import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.direct.auth import issue_token
from app.direct.models import Base, Device, Revision, Segment
from app.visualization import compat_downloads as compat


def test_gateway_download_keeps_real_zone_grants(db, setup_test_data, monkeypatch):
    from app.auth import create_access_token
    from app.database import get_db
    from app.models.user import Role, User
    from app.models.wav_file import WavFile
    from app.models.zone_access import ZoneAccess
    from app.services import wav_service

    member = User(
        email="isolated-reader@example.test",
        password_hash="unused",
        role=Role.MEMBER,
        organization_id=setup_test_data["org"].id,
        is_active=True,
        is_verified=True,
    )
    sensor = setup_test_data["sensor"]
    recording = WavFile(
        id=uuid.uuid4(),
        sensor_id=sensor.id,
        gateway_id=sensor.gateway_id,
        sensor_mac=sensor.mac_address,
        s3_key="private.wav",
        file_size_bytes=4,
        duration_seconds=1,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    db.add_all([member, recording])
    db.commit()
    calls = []

    def original(key):
        calls.append(key)
        yield b"data"

    def database():
        yield db

    monkeypatch.setattr(wav_service, "stream_wav_bytes", original)
    monkeypatch.setenv("ARCHIVE_COMPAT_READS_ENABLED", "true")
    app = FastAPI()
    app.dependency_overrides[get_db] = database
    compat.install(app)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(member.id)})}
    with TestClient(app) as client:
        path = f"/api/v1/wav/download/{recording.id}"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).status_code == 404 and calls == []
        db.add(ZoneAccess(user_id=member.id, zone_id=setup_test_data["zone"].id))
        db.commit()
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.content == b"data"
        assert "attachment" in response.headers["content-disposition"]
        assert calls == ["private.wav"]


def test_disabled_by_default_and_never_registers_intake(monkeypatch):
    monkeypatch.delenv("ARCHIVE_COMPAT_READS_ENABLED", raising=False)
    disabled = FastAPI()
    compat.install(disabled)
    assert not any(route.path.startswith("/api/v1") for route in disabled.routes)
    monkeypatch.setenv("ARCHIVE_COMPAT_READS_ENABLED", "true")
    reader = FastAPI()
    compat.install(reader)
    assert {route.path for route in reader.routes if route.path.startswith("/api/v1/wav/")} == {
        "/api/v1/wav/files",
        "/api/v1/wav/count",
        "/api/v1/wav/features",
        "/api/v1/wav/download/{wav_id}",
        "/api/v1/wav/download-bundle",
    }
    assert all(
        route.methods == {"GET"} for route in reader.routes if route.path.startswith("/api/v1")
    )
    with TestClient(reader) as client:
        assert client.post("/api/v1/wav/upload").status_code == 404
        assert client.post("/api/v1/direct-ingest/chunks").status_code == 404


@pytest.fixture
def device_reader(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path}/reader.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    actor, foreign = str(uuid.uuid4()), str(uuid.uuid4())
    token, digest = issue_token(actor)
    payload = b"bounded-verified-original"
    segment_id = str(uuid.uuid4())
    manifest = {"runs": [{"key": "private.wav", "sha256": hashlib.sha256(payload).hexdigest()}]}
    with Session(engine) as db:
        db.add(
            Device(
                id=actor,
                organization_id="org",
                zone_id="zone",
                mode="DIRECT",
                key_hash=digest,
                active=True,
            )
        )
        db.add(
            Device(
                id=foreign,
                organization_id="other",
                zone_id="other",
                mode="DIRECT",
                key_hash="a" * 64,
                active=True,
            )
        )
        db.flush()
        db.add(
            Segment(
                id=segment_id,
                device_id=actor,
                session_id="session",
                bucket=1,
                first_frame=0,
                end_frame=1,
                published_revision=1,
                sealed=True,
            )
        )
        db.add(
            Segment(
                id="foreign",
                device_id=foreign,
                session_id="session",
                bucket=2,
                first_frame=0,
                end_frame=1,
                published_revision=1,
                sealed=True,
            )
        )
        db.flush()
        for identity in (segment_id, "foreign"):
            db.add(
                Revision(
                    segment_id=identity,
                    revision=1,
                    manifest=manifest,
                    manifest_sha256="a" * 64,
                    raw_deleted_at=1,
                )
            )
        db.commit()
    reads = []

    def get(key):
        reads.append(key)
        return payload

    store = SimpleNamespace(settings=SimpleNamespace(s3_bucket="bucket"), get=get)
    monkeypatch.setattr(compat.direct, "resources", lambda: (engine, store))
    monkeypatch.setattr(compat, "verified_objects", lambda *_: {"private.wav"})
    monkeypatch.setenv("ARCHIVE_COMPAT_READS_ENABLED", "true")
    app = FastAPI()
    compat.install(app)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, {"Authorization": "Bearer " + token}, segment_id, payload, reads, store
    engine.dispose()


def test_device_reads_own_verified_archived_run_and_keeps_tls_auth(device_reader):
    client, headers, segment, payload, reads, _ = device_reader
    assert client.get("/api/v1/direct-ingest/segments").status_code == 401
    listing = client.get("/api/v1/direct-ingest/segments", headers=headers).json()
    assert len(listing) == 1 and listing[0]["segment_id"] == segment and listing[0]["raw_available"]
    assert (
        client.get(f"/api/v1/direct-ingest/segments/{segment}/runs/0", headers=headers).content
        == payload
    )
    assert (
        client.get("/api/v1/direct-ingest/segments/foreign/runs/0", headers=headers).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/direct-ingest/segments/{segment}/runs/1", headers=headers).status_code
        == 404
    )
    assert (
        client.get("http://testserver/api/v1/direct-ingest/segments", headers=headers).status_code
        == 400
    )
    assert reads == ["private.wav"]


def test_device_hash_mismatch_never_releases_original(device_reader):
    client, headers, segment, _, _, store = device_reader
    store.get = lambda _: b"corrupted"
    assert (
        client.get(f"/api/v1/direct-ingest/segments/{segment}/runs/0", headers=headers).status_code
        == 503
    )
