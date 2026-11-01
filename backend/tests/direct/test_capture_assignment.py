"""Real isolated acceptance/assembly plus strict archive provenance contracts."""

import copy
import hashlib
import io
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.direct.assembler import assemble_one
from app.direct.capture_assignment import FIELD, create, digest, manifest_assignment, split
from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Device, Revision, Session
from app.direct.protocol import ChunkMetadata


@pytest.fixture(autouse=True)
def enable_future_capture(pipeline):
    pipeline.cfg.capture_assignment_enabled = True


def session(p):
    with transaction(p.engine) as db:
        return db.get(Session, (p.device_id, p.session_id))


def revision(p):
    with transaction(p.engine) as db:
        return db.query(Revision).one()


def test_real_first_accept_commits_snapshot_with_pcm_and_exact_duplicate_ack(pipeline):
    p = pipeline
    payload = bytes(1200)
    before = p.metadata(payload).session_config()
    assert p.upload(payload).status_code == 201
    config, proof = split(session(p).config)
    assert config == before and proof["device_id"] == p.device_id
    with transaction(p.engine) as db:
        device = db.get(Device, p.device_id)
        assert proof["organization_id"] == device.organization_id
        assert db.query(Chunk).one().payload == payload
    assert p.upload(payload).json()["status"] == "duplicate"
    assert session(p).config[FIELD] == proof
    assert FIELD not in p.metadata(payload).session_config()


def test_assignment_conflict_blocks_new_pcm_but_saved_duplicate_is_still_acknowledged(pipeline):
    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    original = copy.deepcopy(session(p).config)
    with transaction(p.engine) as db:
        db.get(Device, p.device_id).organization_id = str(uuid.uuid4())
    assert p.upload(payload).json()["status"] == "duplicate"
    meta = p.metadata(payload, sequence=1, first_frame=100)
    response = p.upload(payload, meta)
    assert (
        response.status_code == 409
        and response.json()["error"] == "session_capture_assignment_conflict"
    )
    assert session(p).config == original
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 1 and db.get(Budget, 1).used_bytes == 1200


def test_quota_rollback_has_no_orphan_proof_or_session(pipeline):
    p = pipeline
    p.cfg.max_device_spool_bytes = 1
    assert p.upload(bytes(1200)).status_code == 429
    with transaction(p.engine) as db:
        assert db.query(Session).count() == db.query(Chunk).count() == 0
        assert db.get(Budget, 1).used_bytes == 0


def test_default_disabled_preserves_existing_deployments_and_does_not_backfill(pipeline):
    p = pipeline
    p.cfg.capture_assignment_enabled = False
    assert p.upload(bytes(1200)).status_code == 201
    p.cfg.capture_assignment_enabled = True
    assert (
        p.upload(bytes(1200), p.metadata(bytes(1200), sequence=1, first_frame=100)).status_code
        == 201
    )
    assert FIELD not in session(p).config


def test_new_context_keeps_lossless_reference_and_original_protocol_digest(pipeline):
    from app.direct.payloads import WavReader, prepare_reference, reconstruct

    p = pipeline
    payload = bytes(range(100)) * 12
    meta = p.metadata(payload)
    original_digest = meta.digest()
    assert p.upload(payload, meta).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        chunk = db.query(Chunk).one()
        config, reference = prepare_reference(db, chunk)
        assert FIELD not in config and chunk.digest == original_digest
        assert reconstruct(chunk, reference, config, WavReader(p.store.get_local)) == payload
        assert chunk.payload == payload


def test_existing_legacy_session_never_gets_first_accept_claim_or_manifest_upgrade(pipeline):
    p = pipeline
    payload = bytes(1200)
    with transaction(p.engine) as db:
        db.add(
            Session(
                device_id=p.device_id,
                id=p.session_id,
                config=p.metadata(payload).session_config(),
                mode="DIRECT",
            )
        )
    assert p.upload(payload).status_code == 201
    assert FIELD not in session(p).config
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    manifest = revision(p).manifest
    assert manifest_assignment(manifest) is None
    assert "metadata_version" not in manifest


def test_client_cannot_supply_capture_claim(pipeline):
    value = pipeline.metadata(bytes(1200)).model_dump(mode="json")
    value[FIELD] = {"organization_id": str(uuid.uuid4())}
    with pytest.raises(ValidationError):
        ChunkMetadata.model_validate(value)


def test_real_manifest_and_s3_object_metadata_bind_server_proof_without_pcm_changes(pipeline):
    p = pipeline
    objects = {}

    class S3:
        def put_object(self, **values):
            objects[values["Key"]] = values

        def get_object(self, **values):
            return {"Body": io.BytesIO(objects[values["Key"]]["Body"])}

    p.store.client = S3()
    payload = bytes(range(100)) * 12
    assert p.upload(payload).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    row = revision(p)
    manifest = row.manifest
    proof = session(p).config[FIELD]
    assert manifest_assignment(manifest) == proof
    assert manifest["config"] == p.metadata(payload).session_config()
    assert (
        row.manifest_sha256
        == hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    stored = objects[manifest["runs"][0]["key"]]
    assert stored["Body"][44:] == payload
    assert stored["Metadata"]["capture-assignment-sha256"] == proof["sha256"]
    assert stored["Metadata"]["capture-organization-id"] == proof["organization_id"]
    assert set(stored["Metadata"]) == {
        "sha256",
        "capture-assignment-sha256",
        "capture-organization-id",
        "capture-version",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"metadata_version": True},
        {"metadata_version": 3},
        {"capture_assignment": {}},
        {"metadata_version": 2},
    ],
)
def test_partial_unknown_or_corrupted_proof_never_classifies_known(change):
    manifest = dict(device_id=str(uuid.uuid4()), session_id=str(uuid.uuid4())) | change
    with pytest.raises(ValueError):
        manifest_assignment(manifest)


def test_proof_rejects_other_source_or_tampering_even_if_a_hash_is_present():
    device = SimpleNamespace(
        id=str(uuid.uuid4()),
        organization_id=str(uuid.uuid4()),
        zone_id=str(uuid.uuid4()),
        mode="DIRECT",
    )
    sid = str(uuid.uuid4())
    proof = create(device, sid, time.time())
    proof["organization_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError):
        split({"device_id": device.id, "session_id": sid, FIELD: proof})
    proof["sha256"] = digest({k: v for k, v in proof.items() if k != "sha256"})
    with pytest.raises(ValueError):
        split({"device_id": str(uuid.uuid4()), "session_id": sid, FIELD: proof})
