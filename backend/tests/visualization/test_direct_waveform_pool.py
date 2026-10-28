"""Direct previews must reuse a one-connection pool after authorization."""

import hashlib
import io
import json
import uuid
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.auth import get_current_user
from app.database import get_db
from app.direct.models import Base, Device, Revision, Segment
from app.direct.models import Session as CaptureSession
from app.models.user import Role
from app.rate_limit import limiter
from app.visualization import direct, direct_api


@pytest.mark.parametrize("authorized", [True, False], ids=["owner", "other-tenant"])
def test_waveform_uses_single_connection_without_bypassing_authorization(
    tmp_path, monkeypatch, db, setup_test_data, authorized
):
    engine = create_engine(
        f"sqlite:///{tmp_path}/direct.db",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
    )

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def sqlite_read_snapshot(connection, cursor, statement, parameters, context, executemany):
        # SQLite lacks PostgreSQL's SET syntax; keep the real second checkout.
        if statement == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return "SELECT 1", ()
        return statement, parameters

    device_id, session_id, segment_id = (str(uuid.uuid4()) for _ in range(3))
    started = 1_800_000_000
    samples = np.array([2, 5, -7, 9], dtype="<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setparams((1, 2, 4, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    payload = target.getvalue()
    checksum = hashlib.sha256(payload).hexdigest()
    key = f"direct/{device_id}/{session_id}/{checksum}.wav"
    cfg = dict(
        sample_bits=16,
        channels=1,
        sample_rate=4,
        session_start_us=started * 1_000_000,
        calibration_version="raw-adc-counts-v1",
    )
    manifest = dict(
        device_id=device_id,
        session_id=session_id,
        revision=1,
        config=cfg,
        runs=[dict(key=key, sha256=checksum, first_frame=0, frame_count=4)],
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as seed, seed.begin():
            seed.add(
                Device(
                    id=device_id,
                    organization_id=str(setup_test_data["org"].id)
                    if authorized
                    else str(uuid.uuid4()),
                    zone_id=str(setup_test_data["zone"].id),
                    mode="DIRECT",
                    key_hash="f" * 64,
                )
            )
            seed.add(CaptureSession(device_id=device_id, id=session_id, config=cfg, mode="DIRECT"))
            seed.add(
                Segment(
                    id=segment_id,
                    device_id=device_id,
                    session_id=session_id,
                    bucket=started // 600,
                    first_frame=0,
                    end_frame=4,
                    received_frames=4,
                    revision=1,
                    published_revision=1,
                    sealed=True,
                )
            )
            seed.add(
                Revision(
                    segment_id=segment_id,
                    revision=1,
                    manifest=manifest,
                    manifest_sha256=hashlib.sha256(
                        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                )
            )
        # Only the object transport is synthetic; load_source and SQL sessions are real.
        store = SimpleNamespace(get=lambda requested: payload if requested == key else None)
        monkeypatch.setattr(direct, "resources", lambda: (engine, store))
        monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
        app = FastAPI()
        app.state.limiter = limiter
        direct_api.install(app)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            organization_id=setup_test_data["org"].id,
            role=Role.ADMIN,
            is_active=True,
            is_verified=True,
            email="bounded-reader@example.test",
        )
        with TestClient(app) as client:
            for _ in range(2):
                response = client.get(
                    f"/api/v1/visualization/direct/{device_id}/waveform",
                    params={"at": datetime.fromtimestamp(started, UTC).isoformat(), "seconds": 1},
                )
                assert response.status_code == (200 if authorized else 404), response.text
                if authorized:
                    assert response.json()["sample_rate"] == 4
                    assert [row["value"] for row in response.json()["data"]] == samples.tolist()
                assert engine.pool.checkedout() == 0
        assert engine.pool.size() == 1 and engine.pool.overflow() == 0
    finally:
        engine.dispose()
