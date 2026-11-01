import hashlib
import time
import uuid

import pytest
from pydantic import ValidationError

from app.direct.assembler import assemble_one
from app.direct.database import transaction
from app.direct.models import Chunk, Piece, Revision, Segment
from app.direct.payloads import WavReader, prepare_reference, reconstruct
from app.direct.protocol import parse_metadata
from app.raw_archive import wav_catalog
from app.tiered_archive.recovery import archived_direct_original
from app.visualization.direct_api import wav_download_name


def meta(p, payload, **changes):
    v1 = p.metadata(payload).model_dump(mode="json")
    return parse_metadata(
        v1
        | {
            "protocol_version": 2,
            "boot_counter": 1,
            "counter_generation": str(uuid.UUID(int=7)),
            "timing": {
                "source": "ntp",
                "sync_epoch_us": p.start_us - 1000,
                "time_reference": "nominal_sample_clock",
                "uncertainty_us": None,
            },
        }
        | changes
    )


def latest(p):
    with transaction(p.engine) as db:
        segment = db.query(Segment).order_by(Segment.bucket).first()
        revision = db.get(Revision, (segment.id, segment.published_revision))
        return revision.manifest


def test_v2_delay_retry_gap_repair_and_restore(pipeline, tmp_path):
    p = pipeline
    p.cfg.timestamp_object_keys_enabled = True
    payload = bytes(1200)
    first, third = meta(p, payload), meta(p, payload, sequence=2, first_frame=200)
    assert p.upload(payload, first).json()["status"] == "persisted"
    assert p.upload(payload, first).json()["status"] == "duplicate"
    assert p.upload(payload, third).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 122)
    before = latest(p)
    assert before["missing_frame_ranges"][0] == [100, 200]
    first_run, third_run = before["runs"]
    assert first_run["started_at_us"] == p.start_us
    assert third_run["started_at_us"] == p.start_us + 400000
    assert f"_e{p.start_us // 1000}_b1_q0_f0_" in first_run["key"]
    assert first_run["chunks"][0]["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert p.store.get(first_run["key"])
    assert p.upload(payload, meta(p, payload, sequence=1, first_frame=100)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 123)
    after = latest(p)
    assert len(after["runs"]) == 1
    assert after["runs"][0]["started_at_us"] == first_run["started_at_us"]
    assert [c["sequence"] for c in after["runs"][0]["chunks"]] == [0, 1, 2]
    wav_catalog.validate_row(
        "direct",
        "direct_revision",
        {
            "segment_id": str(uuid.uuid4()),
            "revision": after["revision"],
            "manifest": after,
            "manifest_sha256": hashlib.sha256(wav_catalog.raw_json(after)).hexdigest(),
            "verified_at": 1.0,
            "raw_deleted_at": None,
        },
    )
    assert "_b1_" in wav_download_name(after, after["revision"], 0)
    with transaction(p.engine) as db:
        row = db.query(Chunk).filter_by(sequence=0).one()
        pieces = [
            {
                "chunk_id": row.id,
                "payload_offset": piece.payload_offset,
                "frame_count": piece.frame_count,
            }
            for piece in db.query(Piece).filter_by(chunk_id=row.id).all()
        ]
        original = {
            "row": {
                "payload_bytes": row.payload_bytes,
                "payload_sha256": row.payload_sha256,
                "payload": row.payload,
                "sequence": row.sequence,
                "first_frame": row.first_frame,
                "frame_count": row.frame_count,
                "digest": row.digest,
                "id": row.id,
            },
            "context": {
                "original_payload": payload,
                "session_config": first.session_config(),
                "pieces": pieces,
            },
        }
        assert archived_direct_original(original) == payload
        config, reference = prepare_reference(db, row)
        assert reconstruct(row, reference, config, WavReader(p.store.get)) == payload
        from app.tiered_archive import parquet

        path = tmp_path / "original.parquet"
        envelope = {
            "identity": "0",
            "organization": str(uuid.UUID(int=3)),
            "sensor": p.device_id,
            "time_us": p.start_us,
            "body": original,
        }
        parquet_metadata = parquet.write(path, [envelope])
        restored = list(parquet.read(path, parquet_metadata))[0]
        assert archived_direct_original(restored["body"]) == payload


def test_v1_and_v2_validation_and_default_key(pipeline):
    p = pipeline
    payload = bytes(1200)
    old = p.metadata(payload).model_dump(mode="json")
    with pytest.raises(ValidationError):
        parse_metadata(old | {"boot_counter": 1})
    good = meta(p, payload).model_dump(mode="json")
    for changes in (
        {"protocol_version": 3},
        {"boot_counter": 0},
        {"timing": good["timing"] | {"source": "unknown"}},
        {"timing": good["timing"] | {"unexpected": 1}},
    ):
        with pytest.raises((ValidationError, ValueError)):
            parse_metadata(good | changes)
    assert p.upload(payload, meta(p, payload)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    manifest = latest(p)
    assert len(manifest["runs"][0]["key"].split("/")[-1]) == 68
    assert p.store.get(manifest["runs"][0]["key"])
    legacy = dict(manifest, config=old, runs=[dict(manifest["runs"][0])])
    legacy["config"].pop("boot_counter", None)
    assert "_bunknown_" in wav_download_name(legacy, 1, 0)


def test_boundary_boot_session_and_download_identity(pipeline):
    p = pipeline
    p.cfg.timestamp_object_keys_enabled = True
    p.start_us = (int(time.time()) // 600 - 2) * 600_000_000 - 100_000
    payload = bytes(1200)
    first = meta(p, payload)
    assert p.upload(payload, first).status_code == 201
    changed = meta(p, payload, boot_counter=2, sequence=1, first_frame=100)
    assert p.upload(payload, changed).status_code == 409
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    with transaction(p.engine) as db:
        manifests = [
            db.get(Revision, (s.id, s.published_revision)).manifest
            for s in db.query(Segment).order_by(Segment.bucket).all()
        ]
    left, right = [manifest["runs"][0] for manifest in manifests]
    assert (left["frame_count"], right["first_frame"]) == (50, 50)
    assert right["started_at_us"] == p.start_us + 100_000
    assert "_q0_f50_" in right["key"]
    assert p.store.get(left["key"]) and p.store.get(right["key"])
    assert wav_download_name(manifests[0], 1, 0) != wav_download_name(manifests[1], 1, 0)
    p.session_id = str(uuid.uuid4())
    assert p.upload(payload, meta(p, payload, boot_counter=2)).status_code == 201


def test_v2_catalog_roundtrip_and_old_allowlist_rejection(pipeline, tmp_path):
    from app.raw_archive.policy import ArchiveBlocked
    from tests.raw_archive.test_wav_catalog import fixture_bundle

    p = pipeline
    payload = bytes(1200)
    assert p.upload(payload, meta(p, payload)).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    revision = latest(p)
    row = {
        "segment_id": str(uuid.uuid4()),
        "revision": revision["revision"],
        "manifest": revision,
        "manifest_sha256": hashlib.sha256(wav_catalog.raw_json(revision)).hexdigest(),
        "verified_at": 1.0,
        "raw_deleted_at": None,
    }
    root, bundle = fixture_bundle(tmp_path)
    temp = tmp_path / "v2-parts"
    temp.mkdir()
    parts = wav_catalog.Parts(temp, "direct", lambda: None)
    parts.write(wav_catalog.validate_row("direct", "direct_revision", row))
    parts.finish()
    for entry in parts.files:
        source = temp / entry["file"]
        entry["file"] = "direct-000001.jsonl.gz"
        source.rename(root / entry["file"])
    bundle["files"].extend(parts.files)
    bundle["tables"]["direct"]["direct_revision"] = 1
    files = {entry["file"]: root / entry["file"] for entry in bundle["files"]}
    wav_catalog.restore_parts(bundle, files, tmp_path / "restored.sqlite")
    bundle["allowlist_sha256"] = wav_catalog.CAPTURE_ALLOWLIST_SHA256
    with pytest.raises(ArchiveBlocked, match="protocol v2"):
        wav_catalog.restore_parts(bundle, files, tmp_path / "old.sqlite")


def test_old_feature_backup_hashes_remain_readable():
    from app.raw_archive import feature_backup as features

    for fmt, schema, digest, expected in (
        (features.FORMAT, 1, wav_catalog.LEGACY_ALLOWLIST_SHA256, features.TABLES),
        (features.FORMAT, 1, wav_catalog.CAPTURE_ALLOWLIST_SHA256, features.TABLES),
        (
            features.FORMAT_V2,
            2,
            features.PRE_DIRECT_V2_FEATURE_ALLOWLIST_SHA256,
            features.TABLES_V2,
        ),
        (
            features.FORMAT_DIRECT_REVISION,
            3,
            features.PRE_DIRECT_V2_REVISION_ALLOWLIST_SHA256,
            features.TABLES_DIRECT_REVISION,
        ),
    ):
        assert (
            features._tables_for({"format": fmt, "schema": schema, "allowlist_sha256": digest})
            == expected
        )
