import hashlib
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.direct.auth import issue_token
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.main import create_app
from app.direct.models import Base, Budget, Device
from app.direct.protocol import ChunkMetadata
from app.direct.storage import ArtifactStore


def build_pipeline(cfg):
    engine = make_engine(cfg)
    Base.metadata.create_all(engine)
    device_id = str(uuid.uuid4())
    token, digest = issue_token(device_id)
    with transaction(engine) as db:
        if db.get(Budget, 1) is None:
            db.add(Budget(id=1, used_bytes=0))
        db.add(
            Device(
                id=device_id,
                organization_id=str(uuid.uuid4()),
                zone_id=str(uuid.uuid4()),
                mode="DIRECT",
                key_hash=digest,
                active=True,
                spool_bytes=0,
            )
        )
    store = ArtifactStore(cfg)
    app = create_app(cfg, engine, store)
    result = SimpleNamespace(
        cfg=cfg,
        engine=engine,
        store=store,
        app=app,
        client=TestClient(app),
        device_id=device_id,
        token=token,
        session_id=str(uuid.uuid4()),
        start_us=(int(time.time()) // 600 - 3) * 600_000_000,
    )

    def metadata(payload, **overrides):
        data = {
            "protocol_version": 1,
            "device_id": device_id,
            "session_id": result.session_id,
            "sequence": 0,
            "first_frame": 0,
            "session_start_us": result.start_us,
            "sample_rate": 500,
            "channels": 4,
            "sample_bits": 24,
            "frame_count": len(payload) // 12,
            "channel_labels": ["CH1", "CH2", "CH3", "CH4"],
            "calibration_version": "raw-adc-counts-v1",
            "firmware_version": "direct-test-v1",
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            **overrides,
        }
        return ChunkMetadata.model_validate(data)

    def upload(payload, meta=None, token_override=None):
        meta = meta or metadata(payload)
        return result.client.post(
            "/api/v1/direct-ingest/chunks",
            content=payload,
            headers={
                "Authorization": "Bearer " + (token_override or token),
                "Content-Type": "application/octet-stream",
                "X-GreenMind-Metadata": meta.model_dump_json(),
            },
        )

    result.metadata, result.upload = metadata, upload
    return result


@pytest.fixture
def pipeline(tmp_path):
    cfg = DirectSettings(
        database_url=f"sqlite:///{tmp_path}/direct.db",
        ingest_enabled=True,
        require_tls=False,
        artifact_root=tmp_path / "artifacts",
        idle_seconds=1,
    )
    value = build_pipeline(cfg)
    yield value
    value.client.close()
    value.engine.dispose()
