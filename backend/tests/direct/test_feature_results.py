import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.direct.config import DirectSettings
from app.direct.feature_results import ResultConflict, claim_job, persist_result
from app.direct.models import FeatureJob, FeatureResult
from app.services.signal_feature_contract import (
    canonical_sha256,
)
from tests.direct.conftest import build_pipeline
from tests.feature_contract_fixtures import feature_data as data


@pytest.fixture
def direct_pipeline(tmp_path):
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


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'results.db'}")
    FeatureResult.__table__.create(engine)
    FeatureJob.__table__.create(engine)
    return engine


def test_idempotent_persistence_and_conflicts(engine):
    inputs, payload = data()
    result = persist_result(engine, inputs, payload)
    assert persist_result(engine, inputs, payload) == result
    with Session(engine) as db:
        row = db.get(FeatureResult, result)
        assert row.verified_at is not None
        assert row.payload_sha256 == canonical_sha256(row.payload)
    modified = dict(payload, anomaly_index=1)
    with pytest.raises(ResultConflict):
        persist_result(engine, inputs, modified)
    with pytest.raises(ValueError):
        persist_result(engine, inputs, dict(payload, anomaly_index=float("nan")))


def test_two_writers_idempotent(engine):
    inputs, payload = data()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: persist_result(engine, inputs, payload), range(2)))
    assert results[0] == results[1]


def test_claim_expiry_and_attempt_bound(engine):
    job_id = str(uuid.uuid4())
    with Session(engine) as db, db.begin():
        db.add(
            FeatureJob(
                id=job_id,
                target_sha256="d" * 64,
                target={},
                status="pending",
                retry_at=0,
                attempts=0,
                created_at=1,
            )
        )
    assert claim_job(engine, job_id, now=10, lease_seconds=5, max_attempts=2)
    assert claim_job(engine, job_id, now=14, lease_seconds=5, max_attempts=2) is None
    assert claim_job(engine, job_id, now=15, lease_seconds=5, max_attempts=2)
    assert claim_job(engine, job_id, now=20, lease_seconds=5, max_attempts=2) is None


def test_sealed_reextraction_verifies_originals_and_preserves_manifest(direct_pipeline):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import FeatureJob, FeatureResult, Revision, Segment

    p = direct_pipeline
    raw = b"".join(
        int(value).to_bytes(3, "little", signed=True)
        for value in ([-8388608, -1, 1, 8388607] * 100)
    )
    assert p.upload(raw).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        segment = db.query(Segment).one()
        segment.sealed = True
        manifest_digest = db.get(Revision, (segment.id, segment.revision)).manifest_sha256
    assert enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job = db.query(FeatureJob).one()
        job_id = job.id
    token = claim_job(p.engine, job_id, lease_seconds=60)
    result_ids = process_claim(p.engine, p.store, p.cfg, job_id, token)
    assert len(result_ids) == 4
    with transaction(p.engine) as db:
        assert db.query(FeatureResult).count() == 4
        assert all(
            row.verified_at is not None
            and row.payload["quality"]["data_quality_status"] == "technical_fault"
            for row in db.query(FeatureResult)
        )
        assert db.query(Revision).one().manifest_sha256 == manifest_digest
        assert db.get(FeatureJob, job_id).status == "done"


def test_reextraction_rejects_corrupt_chunk_before_result(direct_pipeline):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import Chunk, FeatureJob, FeatureResult, Segment

    p = direct_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        db.query(Segment).one().sealed = True
        db.query(Chunk).one().payload = b"x" * 1200
    enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job_id = db.query(FeatureJob).one().id
    token = claim_job(p.engine, job_id, lease_seconds=60)
    with pytest.raises(ValueError, match="checksum"):
        process_claim(p.engine, p.store, p.cfg, job_id, token)
    with transaction(p.engine) as db:
        assert db.query(FeatureResult).count() == 0


def test_reextraction_reconstructs_referenced_original_and_rejects_foreign_revision(
    direct_pipeline,
):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import Chunk, FeatureJob, PayloadReference, Segment
    from app.direct.payloads import prepare_reference

    p = direct_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        db.query(Segment).one().sealed = True
        chunk = db.query(Chunk).one()
        _, ref = prepare_reference(db, chunk)
        db.add(ref)
        chunk.payload = None
    enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job_id = db.query(FeatureJob).one().id
    token = claim_job(p.engine, job_id, lease_seconds=60)
    assert len(process_claim(p.engine, p.store, p.cfg, job_id, token)) == 4
    with transaction(p.engine) as db:
        ref = db.query(PayloadReference).one()
        bad = [dict(part, revision=part["revision"] + 1) for part in ref.parts]
        ref.parts = bad
        job = db.get(FeatureJob, job_id)
        job.status = "pending"
        job.retry_at = 0
        job.attempts = 0
    token = claim_job(p.engine, job_id, lease_seconds=60)
    with pytest.raises(ValueError, match="revision"):
        process_claim(p.engine, p.store, p.cfg, job_id, token)


def test_scientific_history_reads_all_run_channels(direct_pipeline):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import FeatureJob, Revision, Segment
    from app.raw_archive.direct_feature_history import read_history

    p = direct_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        db.query(Segment).one().sealed = True
    enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job_id = db.query(FeatureJob).one().id
    token = claim_job(p.engine, job_id, lease_seconds=60)
    process_claim(p.engine, p.store, p.cfg, job_id, token)
    with transaction(p.engine) as db:
        revision = db.query(Revision).one()
        data = {
            name: getattr(revision, name)
            for name in ("segment_id", "revision", "manifest_sha256", "manifest")
        }
        history = read_history(db, data)
        assert len(history["results"]) == 4
        assert [item["inputs"]["channel_label"] for item in history["results"]] != []
        assert all(
            item["payload"]["quality"]["data_quality_status"] == "technical_fault"
            for item in history["results"]
        )
        assert all(
            item["payload"]["quality"]["observation_status"]["source_flags"] is False
            for item in history["results"]
        )


def test_two_workers_cannot_claim_same_job(engine):
    job_id = str(uuid.uuid4())
    with Session(engine) as db, db.begin():
        db.add(
            FeatureJob(
                id=job_id,
                target_sha256="e" * 64,
                target={},
                status="pending",
                retry_at=0,
                attempts=0,
                created_at=1,
            )
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(
            pool.map(lambda _: claim_job(engine, job_id, now=10, lease_seconds=60), range(2))
        )
    assert sum(token is not None for token in tokens) == 1


def test_reextraction_rejects_source_change_during_wav_read(direct_pipeline):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import Segment

    p = direct_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        db.query(Segment).one().sealed = True
    enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job_id = db.query(FeatureJob).one().id
    token = claim_job(p.engine, job_id, lease_seconds=60)

    class ChangingStore:
        def get(self, key):
            value = p.store.get(key)
            with transaction(p.engine) as db:
                db.query(Segment).one().sealed = False
            return value

    with pytest.raises(ValueError, match="revision changed"):
        process_claim(p.engine, ChangingStore(), p.cfg, job_id, token)
    with transaction(p.engine) as db:
        assert db.query(FeatureResult).count() == 0


def test_historical_contract_does_not_reinterpret_when_current_version_changes(monkeypatch):
    from app.direct import feature_results

    inputs, payload = data()
    feature_results.validate_result(inputs, payload, require_current=False)
    monkeypatch.setattr(feature_results, "CONTRACT_VERSION", "signal-contract-v2")
    feature_results.validate_result(inputs, payload, require_current=False)
    with pytest.raises(ValueError, match="identity"):
        feature_results.validate_result(inputs, payload, require_current=True)


def test_scoped_export_contains_only_the_requested_revision(direct_pipeline, tmp_path):
    import time

    from app.direct.assembler import assemble_one
    from app.direct.database import transaction
    from app.direct.feature_results import enqueue_sealed_page, process_claim
    from app.direct.models import Revision, Segment
    from app.raw_archive.feature_backup import (
        FORMAT_DIRECT_REVISION,
        export_direct_revision,
        restore,
    )

    p = direct_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        db.query(Segment).one().sealed = True
    enqueue_sealed_page(p.engine)
    with transaction(p.engine) as db:
        job_id = db.query(FeatureJob).one().id
    token = claim_job(p.engine, job_id, lease_seconds=60)
    process_claim(p.engine, p.store, p.cfg, job_id, token)
    output = tmp_path / "scoped"
    output.mkdir()
    with transaction(p.engine) as db:
        revision = db.query(Revision).one()
        revision_value = {
            name: getattr(revision, name)
            for name in ("segment_id", "revision", "manifest_sha256", "manifest")
        }
        manifest, history = export_direct_revision(db, revision_value, output)
    assert manifest["format"] == FORMAT_DIRECT_REVISION
    assert manifest["scientific_coverage"]["segment_id"] == revision_value["segment_id"]
    assert manifest["tables"] == {"direct": {"direct_feature_result": 4}}
    files = {entry["file"]: output / entry["file"] for entry in manifest["files"]}
    assert restore(manifest, files, tmp_path / "scoped.sqlite3") == manifest["tables"]
    assert len(history["results"]) == 4
