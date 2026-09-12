import io
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.direct.assembler import assemble_one, release_verified_chunks
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.features import channel_features
from app.direct.main import create_app
from app.direct.models import Budget, Chunk, Device, Revision, Segment
from app.direct.retention import run_retention


def packed24(values):
    return b"".join(int(value).to_bytes(3, "little", signed=True) for value in values)


def current_manifest(p):
    with transaction(p.engine) as db:
        segment = db.query(Segment).order_by(Segment.bucket).first()
        return db.get(Revision, (segment.id, segment.published_revision)).manifest


def test_exact_pcm24_all_channels_and_extremes(pipeline):
    p = pipeline
    values = [-8388608, -1, 1, 8388607, 5, -5, 65537, -65537] * 100
    payload = packed24(values)
    assert p.upload(payload).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    manifest = current_manifest(p)
    assert manifest["status"] == "partial"
    run = manifest["runs"][0]
    with wave.open(io.BytesIO(p.store.get(run["key"])), "rb") as wav:
        assert (wav.getsampwidth(), wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (
            3,
            4,
            500,
            200,
        )
        assert wav.readframes(200) == payload
    assert [item["channel"] for item in run["features"]] == ["CH1", "CH2", "CH3", "CH4"]
    assert all(item["unit"] == "adc_counts" for item in run["features"])


def test_durable_ack_retry_and_conflict_after_restart(pipeline):
    p = pipeline
    payload = packed24(range(400))
    meta = p.metadata(payload)
    assert p.upload(payload, meta).json()["status"] == "persisted"
    p.engine.dispose()
    engine = make_engine(p.cfg)
    with transaction(engine) as db:
        assert db.query(Chunk).one().payload == payload
        assert db.get(Budget, 1).used_bytes == len(payload)
    assert p.upload(payload, meta).json()["status"] == "duplicate"
    changed = bytes(len(payload))
    assert p.upload(changed, p.metadata(changed)).status_code == 409
    assert p.upload(payload, meta.model_copy(update={"first_frame": 1})).status_code == 409
    engine.dispose()


def test_reorder_gap_late_repair_and_stable_latest_revision(pipeline):
    p = pipeline
    one, two, three = [packed24([value] * 400) for value in (1, 2, 3)]
    assert p.upload(three, p.metadata(three, sequence=2, first_frame=200)).status_code == 201
    assert p.upload(one, p.metadata(one)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    before = current_manifest(p)
    assert before["missing_frame_ranges"][0] == [100, 200]
    assert len(before["runs"]) == 2
    assert p.upload(two, p.metadata(two, sequence=1, first_frame=100)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    after = current_manifest(p)
    assert len(after["runs"]) == 1
    with wave.open(io.BytesIO(p.store.get(after["runs"][0]["key"])), "rb") as wav:
        assert wav.readframes(300) == one + two + three
    response = p.client.get(
        "/api/v1/direct-ingest/segments", headers={"Authorization": "Bearer " + p.token}
    )
    assert len(response.json()) == 1
    assert response.json()[0]["manifest"]["revision"] == 3


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"payload_sha256": "0" * 64}, 422),
        ({"device_id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")}, 403),
        ({"session_start_us": int((time.time() + 86400) * 1e6)}, 422),
        ({"session_start_us": int((time.time() - 8 * 86400) * 1e6)}, 410),
    ],
)
def test_rejects_invalid_metadata_without_persistence(pipeline, change, expected):
    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload, p.metadata(payload).model_copy(update=change)).status_code == expected
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 0
        assert db.get(Budget, 1).used_bytes == 0


def test_auth_size_disable_and_tls_do_not_change_legacy(pipeline):
    p = pipeline
    assert p.upload(bytes(1200), token_override="wrong").status_code == 401
    assert p.upload(bytes(1200), p.metadata(bytes(1188))).status_code == 413
    assert p.upload(bytes(1188), p.metadata(bytes(1200))).status_code == 422
    p.cfg.require_tls = True
    assert p.upload(bytes(1200)).json()["error"] == "tls_required"
    p.cfg.ingest_enabled = False
    assert p.upload(bytes(1200)).json()["error"] == "direct_disabled"
    assert p.client.get("/health").json() == {"direct_ingest": "disabled"}
    disabled = TestClient(create_app(DirectSettings()))
    assert disabled.get("/health").status_code == 200
    assert disabled.post("/api/v1/direct-ingest/chunks").status_code == 503
    from app.main import app as legacy_app

    assert TestClient(legacy_app).get("/health").status_code == 200
    assert not any("direct-ingest" in route.path for route in legacy_app.routes)


def test_concurrent_duplicate_requests_do_not_double_spool(pipeline):
    p = pipeline
    payload = bytes(1200)
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: p.upload(payload).json()["status"], range(16)))
    assert statuses.count("persisted") == 1
    assert statuses.count("duplicate") == 15
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 1
        assert db.get(Budget, 1).used_bytes == 1200


def test_quota_failure_is_atomic_and_retryable(pipeline):
    p = pipeline
    p.cfg.max_device_spool_bytes = 1024
    assert p.upload(bytes(1200)).status_code == 429
    p.cfg.max_device_spool_bytes = 5000
    p.cfg.max_spool_bytes = 1024
    assert p.upload(bytes(1200)).status_code == 503
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 0
        assert db.get(Budget, 1).used_bytes == 0
        assert db.get(Device, p.device_id).spool_bytes == 0


def test_worker_failure_keeps_acknowledged_data(pipeline, monkeypatch):
    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    original = p.store.put
    monkeypatch.setattr(
        p.store, "put", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected"))
    )
    with pytest.raises(OSError, match="injected"):
        assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    assert release_verified_chunks(p.engine) == 0
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == payload
        assert db.query(Segment).one().published_revision == 0
    monkeypatch.setattr(p.store, "put", original)
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 63)


def test_full_ten_minute_segment_cleanup_and_duplicate_tombstone(pipeline):
    p = pipeline
    payload = packed24([1, -2, 3, -4] * 5000)
    for sequence in range(60):
        assert (
            p.upload(
                payload, p.metadata(payload, sequence=sequence, first_frame=sequence * 5000)
            ).status_code
            == 201
        )
    assert assemble_one(p.engine, p.cfg, p.store)
    manifest = current_manifest(p)
    assert manifest["status"] == "complete"
    assert manifest["runs"][0]["frame_count"] == 300_000
    assert manifest["missing_frame_ranges"] == []
    assert release_verified_chunks(p.engine) == 3_600_000
    assert p.upload(payload, p.metadata(payload)).json()["status"] == "duplicate"
    with transaction(p.engine) as db:
        assert db.get(Budget, 1).used_bytes == 0
        assert db.get(Device, p.device_id).spool_bytes == 0
        assert all(chunk.payload is None for chunk in db.query(Chunk).all())


def test_chunk_crosses_utc_boundary_without_precision_loss(pipeline):
    p = pipeline
    payload = packed24(range(800))
    meta = p.metadata(payload, first_frame=299_900)
    assert p.upload(payload, meta).status_code == 201
    for _ in range(2):
        assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        revisions = (
            db.execute(select(Revision).join(Segment).order_by(Segment.bucket)).scalars().all()
        )
        assert len(revisions) == 2
        restored = b""
        for revision in revisions:
            run = revision.manifest["runs"][0]
            with wave.open(io.BytesIO(p.store.get(run["key"])), "rb") as wav:
                restored += wav.readframes(100)
        assert restored == payload


def test_session_restart_and_configuration_change(pipeline):
    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    assert (
        p.upload(
            payload, p.metadata(payload, sequence=1, first_frame=100, firmware_version="changed")
        ).status_code
        == 409
    )
    assert p.upload(payload, p.metadata(payload, session_id=uuid.uuid4())).status_code == 201


def test_dual_is_server_provisioned_and_isolated(pipeline):
    p = pipeline
    with transaction(p.engine) as db:
        db.get(Device, p.device_id).mode = "DUAL"
    assert p.upload(bytes(1200)).status_code == 422
    values = np.array([0, 99, 32767, 16383] * 95, dtype="<i2")
    payload = values.tobytes()
    meta = p.metadata(
        payload,
        channels=1,
        channel_labels=["CH1"],
        sample_bits=16,
        sample_rate=380,
        frame_count=380,
        calibration_version="unsigned-mv-linear-int16-v1",
    )
    assert p.upload(payload, meta).status_code == 201
    assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    manifest = current_manifest(p)
    assert manifest["source_mode"] == "DUAL"
    assert manifest["analytics_scope"] == "comparison"
    with wave.open(io.BytesIO(p.store.get(manifest["runs"][0]["key"])), "rb") as wav:
        assert wav.readframes(380) == payload


def test_retention_defaults_and_verified_segment_deletion(pipeline):
    p = pipeline
    p.upload(bytes(1200))
    now = time.time() + p.cfg.late_seconds + 100
    assert assemble_one(p.engine, p.cfg, p.store, now=now)
    manifest = current_manifest(p)
    key = manifest["runs"][0]["key"]
    future = time.time() + 91 * 86400
    assert run_retention(p.engine, p.cfg, p.store, now=future) == {"disabled": True}
    p.cfg.retention_enabled = True
    assert run_retention(p.engine, p.cfg, p.store, now=future)["segments"] == 1
    assert p.store.get(key)
    p.cfg.retention_dry_run = False
    assert run_retention(p.engine, p.cfg, p.store, now=future)["segments"] == 1
    with pytest.raises(FileNotFoundError):
        p.store.get(key)
    with transaction(p.engine) as db:
        assert db.query(Revision).one().raw_deleted_at == future


def test_config_rejects_unsafe_deployed_direct_settings():
    with pytest.raises(ValidationError):
        DirectSettings(environment="staging", require_tls=False)
    with pytest.raises(ValidationError):
        DirectSettings(environment="staging", storage="s3", s3_bucket="greenmind-raw")
    staging = {
        "environment": "staging",
        "storage": "s3",
        "s3_bucket": "greenmind-direct-staging",
        "s3_access_key": "test-scoped-key",
        "s3_secret_key": "test-secret",
        "s3_endpoint_url": "https://objects.example.invalid",
        "database_url": "postgresql+psycopg2://test:test@localhost/direct_test",
    }
    assert not DirectSettings(**staging).ingest_enabled
    for unsafe in (
        {"s3_bucket": "greenmind-direct-production"},
        {"s3_access_key": ""},
        {"s3_secret_key": ""},
        {"s3_endpoint_url": "http://objects.example.invalid"},
        {"database_url": "sqlite:///direct.db"},
    ):
        with pytest.raises(ValidationError):
            DirectSettings(**(staging | unsafe))


def test_channel_features_keep_low_bits(pipeline):
    p = pipeline
    payload = packed24([65536, 65537, -65536, -65537])
    meta = p.metadata(payload)
    features = channel_features(payload, meta.session_config())
    assert [value["mean"] for value in features] == [65536, 65537, -65536, -65537]


def test_segment_fragmentation_is_bounded_and_rolls_back_quota(pipeline):
    p = pipeline
    p.cfg.max_chunks_per_segment = 1
    assert p.upload(bytes(1200)).status_code == 201
    response = p.upload(bytes(1200), p.metadata(bytes(1200), sequence=1, first_frame=100))
    assert response.status_code == 413
    assert response.json()["error"] == "segment_fragmentation_limit"
    with transaction(p.engine) as db:
        assert db.get(Budget, 1).used_bytes == 1200
        assert db.query(Chunk).count() == 1


def test_database_commit_failure_returns_no_ack(pipeline, monkeypatch):
    p = pipeline
    # The transaction context commits through SessionTransaction, so inject
    # failure at the DBAPI commit after data was prepared.
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    def fail_commit(connection):
        raise OperationalError("commit", {}, RuntimeError("injected"))

    event.listen(p.engine, "commit", fail_commit)
    response = p.upload(bytes(1200))
    assert response.status_code == 503
    event.remove(p.engine, "commit", fail_commit)
    assert p.upload(bytes(1200)).status_code == 201


def test_stored_corruption_is_not_published_or_released(pipeline):
    p = pipeline
    assert p.upload(bytes(1200)).status_code == 201
    with transaction(p.engine) as db:
        db.query(Chunk).one().payload = b"\x01" * 1200
    with pytest.raises(ValueError, match="checksum"):
        assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    assert release_verified_chunks(p.engine) == 0
    with transaction(p.engine) as db:
        assert db.query(Revision).count() == 0


def test_one_device_cannot_read_another_device_segments(pipeline):
    p = pipeline
    p.upload(bytes(1200))
    assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        segment_id = db.query(Segment).one().id
    from tests.direct.conftest import build_pipeline

    other = build_pipeline(p.cfg)
    try:
        response = other.client.get(
            f"/api/v1/direct-ingest/segments/{segment_id}/runs/0",
            headers={"Authorization": "Bearer " + other.token},
        )
        assert response.status_code == 404
        response = other.client.get(
            "/api/v1/direct-ingest/segments", headers={"Authorization": "Bearer " + other.token}
        )
        assert response.json() == []
    finally:
        other.client.close()
        other.engine.dispose()
