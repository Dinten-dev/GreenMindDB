"""Direct display data must preserve peaks, units, ownership and source independence."""

import hashlib
import io
import os
import time
import uuid
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.direct.assembler import assemble_one
from app.direct.config import DirectSettings
from app.direct.models import Base, Chunk, Device, Piece, Segment
from app.direct.models import Session as CaptureSession
from app.direct.storage import ArtifactStore
from app.visualization import direct
from app.visualization.api import app


def config(start=1789828951562068):
    return dict(
        sample_bits=16,
        channels=1,
        sample_rate=380,
        session_start_us=start,
        calibration_version="unsigned-mv-linear-int16-v1",
        channel_labels=["CH1"],
    )


def test_exact_calibration_utc_buckets_and_peaks():
    cfg = config(1_500_000)
    raw = np.full(380, 10000, dtype="<i2")
    raw[200] = 32767
    rows = sorted(direct.aggregate([(0, raw.tobytes())], cfg, 1), key=lambda r: r["bucket"])
    assert [r["n"] for r in rows] == [190, 190]
    assert [r["bucket"] for r in rows] == [1, 2]
    assert max(r["maximum"] for r in rows) == pytest.approx(3300)
    assert sum(r["duration"] for r in rows) == 1
    coarse = direct.aggregate([(0, raw.tobytes())], cfg, 60)
    assert coarse[0]["n"] == 380
    assert coarse[0]["total2"] == pytest.approx(sum(r["total2"] for r in rows))
    assert coarse[0]["maximum"] == 3300


def test_gaps_do_not_create_samples_and_overlap_is_rejected():
    cfg = config(0)
    raw = np.ones(380, dtype="<i2").tobytes()
    rows = direct.aggregate([(0, raw), (760, raw)], cfg, 1)
    assert {r["bucket"] for r in rows} == {0, 2}
    with pytest.raises(ValueError, match="Overlapping"):
        direct.aggregate([(0, raw), (379, raw)], cfg, 1)


def test_pcm24_channels_and_age_policy(monkeypatch):
    cfg = config()
    cfg.update(sample_bits=24, channels=2, calibration_version="raw-adc-counts-v1")
    values, unit = direct.decode(bytes.fromhex("ffffff ffff7f 000080 010000"), cfg)
    assert values.tolist() == [[-1, 8388607], [-8388608, 1]] and unit == "adc_counts"
    monkeypatch.setenv("VISUAL_DIRECT_FINE_DAYS", "1")
    now = time.time()
    assert direct.resolution_for(now - 86399, now) == 1
    assert direct.resolution_for(now - 2 * 86400, now) == 60
    assert direct.resolution_for(now - 8 * 86400, now) == 600


@pytest.fixture
def live(tmp_path, monkeypatch):
    url = os.environ.get("VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable local PostgreSQL required")
    parsed = make_url(url)
    assert parsed.host in ("localhost", "127.0.0.1") and parsed.database == "visual_test"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    direct.initialize(engine)
    with engine.begin() as db:
        db.execute(
            text(
                "TRUNCATE direct_visual_point,direct_visual_segment,direct_visual_worker,direct_piece,direct_revision,direct_segment,direct_chunk,direct_session,direct_enrollment,direct_device,direct_pairing CASCADE"
            )
        )
    device = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    segid = str(uuid.uuid4())
    org = uuid.uuid4()
    zone = uuid.uuid4()
    start = (int(time.time()) // 600 - 2) * 600
    cfg = config(start * 1000000)
    cfg.update(device_id=device, session_id=sid, protocol_version=1, firmware_version="test")
    settings = DirectSettings(artifact_root=tmp_path, idle_seconds=1)
    store = ArtifactStore(settings)
    raw = np.full(380, 10000, dtype="<i2")
    raw[19] = 32767
    with Session(engine) as db, db.begin():
        db.add(
            Device(
                id=device,
                organization_id=str(org),
                zone_id=str(zone),
                mode="DIRECT",
                key_hash="f" * 64,
            )
        )
        db.flush()
        db.add(CaptureSession(device_id=device, id=sid, config=cfg, mode="DIRECT"))
        seg = Segment(
            id=segid,
            device_id=device,
            session_id=sid,
            bucket=start // 600,
            first_frame=0,
            end_frame=228000,
            received_frames=380,
            revision=1,
            updated_at=time.time() - 10,
        )
        c = Chunk(
            id=str(uuid.uuid4()),
            device_id=device,
            session_id=sid,
            sequence=0,
            first_frame=0,
            frame_count=380,
            payload=raw.tobytes(),
            payload_bytes=760,
            payload_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
            digest="f" * 64,
        )
        db.add_all([seg, c])
        db.flush()
        db.add(Piece(segment_id=segid, chunk_id=c.id, payload_offset=0, frame_count=380))
    monkeypatch.setattr(direct, "resources", lambda: (engine, store))

    class Legacy:
        def execute(self, *args, **kwargs):
            return SimpleNamespace(
                first=lambda: (1,), scalar_one_or_none=lambda: SimpleNamespace(id=zone)
            )

    app.dependency_overrides[get_db] = lambda: Legacy()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        organization_id=org, role="admin"
    )
    yield SimpleNamespace(
        engine=engine,
        store=store,
        settings=settings,
        device=device,
        session=sid,
        segment=segid,
        start=start,
        raw=raw,
        cfg=cfg,
        org=org,
        client=TestClient(app),
    )
    app.dependency_overrides.clear()
    engine.dispose()


def test_live_read_model_idempotent_api_and_tenant(live):
    x = live
    for _ in range(2):
        assert direct.refresh_segment(x.engine, x.store, x.segment, time.time())
    with Session(x.engine) as db:
        assert db.execute(text("SELECT sum(n) FROM direct_visual_point")).scalar() == 380
        # Projection must not mutate source payload or reception identity.
        assert db.query(Chunk).one().payload == x.raw.tobytes()
    response = x.client.get(f"/api/v1/visualization/direct/{x.device}/data?range=1h")
    assert response.status_code == 200, response.text
    series = response.json()[0]
    assert series["source"] == "direct" and series["data"][0]["maximum"] == 3300
    assert series["data"][0]["reading_count"] == 380
    wave = x.client.get(
        f"/api/v1/visualization/direct/{x.device}/waveform",
        params={"at": datetime.fromtimestamp(x.start, UTC).isoformat(), "seconds": 1},
    )
    assert wave.status_code == 200, wave.text
    assert len(wave.json()["data"]) == 380 and wave.json()["data"][19]["value"] == 3300
    csv = x.client.get(f"/api/v1/visualization/direct/{x.device}/export?range=1h")
    assert csv.status_code == 200 and "resolution_seconds" in csv.text
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        organization_id=uuid.uuid4()
    )
    for tail in ("data", "export", "recordings", "waveform?at=2026-09-19T00:00:00Z"):
        assert x.client.get(f"/api/v1/visualization/direct/{x.device}/{tail}").status_code == 404
    app.dependency_overrides.pop(get_current_user)
    assert x.client.get(f"/api/v1/visualization/direct/{x.device}/data").status_code == 401


def test_archived_backfill_verified_wav_and_resolution_change(live):
    x = live
    assert assemble_one(x.engine, x.settings, x.store)
    with Session(x.engine) as db, db.begin():
        db.query(Chunk).update({"payload": None})
    assert direct.refresh_segment(x.engine, x.store, x.segment, time.time())
    response = x.client.get(f"/api/v1/visualization/direct/{x.device}/recordings?range=1h")
    assert response.status_code == 200 and len(response.json()) == 1
    file = response.json()[0]
    url = f"/api/v1/visualization/direct/{x.device}/wav/{file['segment_id']}/{file['revision']}/{file['run']}"
    download = x.client.get(url)
    assert download.status_code == 200
    with wave.open(io.BytesIO(download.content), "rb") as wav:
        assert wav.readframes(380) == x.raw.tobytes()
    assert direct.refresh_segment(x.engine, x.store, x.segment, time.time() + 2 * 86400)
    with Session(x.engine) as db:
        row = db.execute(text("SELECT seconds,n,maximum FROM direct_visual_point")).one()
        assert row == (60, 380, 3300)
    assert direct.refresh_segment(x.engine, x.store, x.segment, time.time() + 8 * 86400)
    with Session(x.engine) as db:
        assert db.execute(text("SELECT seconds,n,maximum FROM direct_visual_point")).one() == (
            600,
            380,
            3300,
        )


def test_corrupt_wav_keeps_previous_verified_projection(live):
    x = live
    direct.refresh_segment(x.engine, x.store, x.segment, time.time())
    assert assemble_one(x.engine, x.settings, x.store)
    for file in x.store.settings.artifact_root.rglob("*.wav"):
        file.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        direct.refresh_segment(x.engine, x.store, x.segment, time.time())
    with Session(x.engine) as db:
        assert db.execute(text("SELECT sum(n) FROM direct_visual_point")).scalar() == 380


def test_worker_cycle_backfills_without_mutating_receiver(live, monkeypatch):
    monkeypatch.setenv("VISUAL_MAX_HOST_LOAD", "100000")
    assert direct.cycle(live.engine, live.store) == "healthy"
    with Session(live.engine) as db:
        assert db.execute(text("SELECT sum(n) FROM direct_visual_point")).scalar() == 380
        assert db.query(Chunk).one().payload == live.raw.tobytes()
    assert direct.cycle(live.engine, live.store) == "healthy"
