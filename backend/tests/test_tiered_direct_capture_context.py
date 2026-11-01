"""Real SQL projections and Parquet retain new provenance, never legacy guesses."""

import copy
import hashlib
import io
import json
import time
import uuid
import wave
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, select
from sqlalchemy.orm import load_only

from app.direct.capture_assignment import FIELD, create
from app.direct.features import channel_features
from app.direct.models import Device, Revision, Segment, Session
from app.raw_archive.catalog import canonical
from app.raw_archive.direct_copy_catalog import PublishedDirectCopyCatalog
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.wav_catalog import COLUMNS
from app.raw_archive.wav_context import DIRECT_CAPTURE_SCOPE_V3, require_captured_direct
from app.tiered_archive import parquet
from app.tiered_archive.parent_restore import load_parent, verify_masters
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.policy import Candidate, eligibility
from tests import test_tiered_wav_metadata_projection as projection_fixtures

direct_adapter = projection_fixtures.direct_adapter


def narrow_get(db, model, identity):
    return db.get(
        model,
        identity,
        options=[
            load_only(*(getattr(model, name) for name in COLUMNS["direct"][model.__tablename__]))
        ],
    )


def captured(adapter_fixture):
    adapter, reference, sessions, engine = adapter_fixture
    body = io.BytesIO()
    with wave.open(body, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x01\x00" * 380)
    payload = body.getvalue()
    with sessions() as db:
        segment = narrow_get(db, Segment, reference[0])
        device = narrow_get(db, Device, segment.device_id)
        session = narrow_get(db, Session, (device.id, segment.session_id))
        proof = create(device, session.id, time.time())
        session.config = dict(session.config, **{FIELD: proof})
        row = db.get(Revision, tuple(reference))
        manifest = copy.deepcopy(row.manifest)
        manifest.update(metadata_version=2, capture_assignment=proof)
        manifest["runs"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest["runs"][0]["features"] = channel_features(payload[44:], manifest["config"])
        row.manifest, row.manifest_sha256 = manifest, canonical(manifest)
        db.commit()
    adapter.copy_published_history = True
    adapter.catalog = PublishedDirectCopyCatalog(sessions, adapter.catalog.bucket)
    return adapter, reference, sessions, proof, payload


@pytest.mark.parametrize("changed_organization", [False, True])
def test_actual_sql_projection_and_typed_parent_readback_bind_known_context(
    direct_adapter, tmp_path, changed_organization
):
    adapter, reference, sessions, proof, payload = captured(direct_adapter)
    if changed_organization:
        with sessions() as db:
            segment = narrow_get(db, Segment, reference[0])
            narrow_get(db, Device, segment.device_id).organization_id = str(uuid.uuid4())
            db.commit()
    envelope, headers, flags = adapter.metadata(reference)
    assert envelope["body"]["copy_scope"] == DIRECT_CAPTURE_SCOPE_V3
    assert envelope["organization"] == proof["organization_id"]
    assert flags["quarantined"] is False
    assert flags["complete"] is flags["dependencies_complete"] is False
    folder = tmp_path / "bundle"
    folder.mkdir()
    primary = dict(parquet.write(folder / "0000.parquet", [envelope]), format="parquet")
    at = datetime.fromtimestamp(envelope["time_us"] / 1e6, UTC)
    candidate = Candidate(
        "staging",
        "direct",
        "direct_wav",
        envelope["organization"],
        at,
        at + timedelta(seconds=1),
        primary["content_sha256"],
        1,
        "wav",
        at + timedelta(seconds=2),
        **flags,
    )
    require_captured_direct(envelope["body"]["metadata"], candidate)
    with pytest.raises(ArchiveBlocked, match="Processing, dependencies"):
        eligibility(candidate, {}, datetime.now(UTC))
    (folder / "0001.wav").write_bytes(payload)
    record = adapter.catalog.resolve(reference)[0]
    wav = dict(
        format="wav",
        sha256=record.sha256,
        bytes=len(payload),
        key=record.remote_key("staging"),
        wav_header=headers[record.key],
    )
    manifest = dict(
        candidate=candidate_json(candidate), identity=candidate.identity, files=[primary, wav]
    )
    (folder / "manifest.json").write_text(json.dumps(manifest))
    result = load_parent(
        folder,
        checksum(folder / "manifest.json"),
        candidate_id=candidate.identity,
        environment="staging",
        source="direct",
        organization=envelope["organization"],
        reference=reference,
        checkpoint=lambda: None,
    )
    assert result[3] == proof and result[0].quarantined is False
    with pytest.raises(ArchiveBlocked, match="pinned source identity"):
        load_parent(
            folder,
            checksum(folder / "manifest.json"),
            candidate_id=candidate.identity,
            environment="staging",
            source="direct",
            organization=str(uuid.uuid4()),
            reference=reference,
            checkpoint=lambda: None,
        )
    metadata = result[2]
    tampered = copy.deepcopy(metadata)
    tampered["capture_assignment"]["organization_id"] = str(uuid.uuid4())
    with pytest.raises(ArchiveBlocked):
        require_captured_direct(tampered, candidate)
    restored_masters_match_observed_not_historical_org(
        result[1], metadata, proof["organization_id"]
    )


def restored_masters_match_observed_not_historical_org(row, metadata, captured_org):
    """Real isolated Master SQL: observed snapshot stays exact; authorization differs."""
    schema = MetaData()
    tables = {
        "direct_device": Table(
            "direct_device",
            schema,
            *(
                Column(k, String, primary_key=k == "id")
                for k in ("id", "organization_id", "zone_id", "legacy_sensor_id", "mode")
            ),
        ),
        "direct_session": Table(
            "direct_session",
            schema,
            Column("device_id", String, primary_key=True),
            Column("id", String, primary_key=True),
            Column("config", JSON),
            Column("mode", String),
        ),
        "direct_enrollment": Table(
            "direct_enrollment",
            schema,
            Column("device_id", String, primary_key=True),
            Column("hardware_id", String),
        ),
    }
    engine = create_engine("sqlite://")
    schema.create_all(engine)
    observed = metadata["observed_assignment"]
    try:
        with engine.begin() as db:
            db.execute(
                tables["direct_device"]
                .insert()
                .values(
                    id=observed["device_id"],
                    **{
                        k: observed[k]
                        for k in ("organization_id", "zone_id", "legacy_sensor_id", "mode")
                    },
                )
            )
            db.execute(tables["direct_session"].insert().values(**metadata["session"]))
            db.execute(
                tables["direct_enrollment"]
                .insert()
                .values(device_id=observed["device_id"], hardware_id=observed["hardware_id"])
            )
            verify_masters(db, tables, "direct", row, metadata, captured_org)
            with pytest.raises(ArchiveBlocked, match="capture.*tenant|capture.*assignment"):
                verify_masters(db, tables, "direct", row, metadata, str(uuid.uuid4()))
            from app.direct.capture_assignment import digest as capture_digest

            altered = copy.deepcopy(metadata["session"]["config"])
            altered[FIELD]["organization_id"] = str(uuid.uuid4())
            altered[FIELD]["sha256"] = capture_digest(
                {k: v for k, v in altered[FIELD].items() if k != "sha256"}
            )
            db.execute(tables["direct_session"].update().values(config=altered))
            with pytest.raises(ArchiveBlocked, match="capture.*assignment"):
                verify_masters(db, tables, "direct", row, metadata, captured_org)
            db.execute(
                tables["direct_session"].update().values(config=metadata["session"]["config"])
            )
            device = db.execute(select(tables["direct_device"])).mappings().one()
            assert device["organization_id"] == observed["organization_id"]
            db.execute(tables["direct_device"].update().values(organization_id=str(uuid.uuid4())))
            with pytest.raises(ArchiveBlocked, match="master tenant"):
                verify_masters(db, tables, "direct", row, metadata, captured_org)
    finally:
        engine.dispose()


def test_historical_new_revision_keeps_captured_org_after_observed_device_change(direct_adapter):
    adapter, reference, sessions, proof, _ = captured(direct_adapter)
    with sessions() as db:
        segment = narrow_get(db, Segment, reference[0])
        row = db.get(Revision, tuple(reference))
        latest = copy.deepcopy(row.manifest)
        latest["revision"] = 2
        db.add(
            Revision(
                segment_id=segment.id,
                revision=2,
                manifest=latest,
                manifest_sha256=canonical(latest),
                verified_at=row.verified_at + 1,
            )
        )
        segment.revision = segment.published_revision = 2
        narrow_get(db, Device, segment.device_id).organization_id = str(uuid.uuid4())
        db.commit()
    envelope, _, flags = adapter.metadata(reference)
    assert envelope["organization"] == proof["organization_id"]
    assert (
        envelope["body"]["metadata"]["observed_assignment"]["organization_id"]
        != proof["organization_id"]
    )
    assert envelope["body"]["metadata"]["capture_assignment"] == proof
    assert flags["quarantined"] is False and flags["dependencies_complete"] is False


def test_session_and_revision_provenance_mismatch_holds(direct_adapter):
    adapter, reference, sessions, _, _ = captured(direct_adapter)
    with sessions() as db:
        segment = narrow_get(db, Segment, reference[0])
        session = narrow_get(db, Session, (segment.device_id, segment.session_id))
        session.config = {k: v for k, v in session.config.items() if k != FIELD}
        db.commit()
    with pytest.raises(ArchiveBlocked, match="Verified server capture"):
        adapter.metadata(reference)


def test_old_current_copy_is_still_unknown_without_server_proof(direct_adapter):
    adapter, reference, sessions, _ = direct_adapter
    adapter.copy_published_history = True
    adapter.catalog = PublishedDirectCopyCatalog(sessions, adapter.catalog.bucket)
    envelope, _, flags = adapter.metadata(reference)
    assert envelope["body"]["metadata"]["capture_assignment"]["status"] == "unknown"
    assert flags["quarantined"] is True


def test_legacy_metadata_archive_still_restores_without_relabeling_new_capture(tmp_path):
    from app.raw_archive import wav_catalog
    from tests.raw_archive.test_wav_catalog import fixture_bundle

    folder, manifest = fixture_bundle(tmp_path)
    manifest["allowlist_sha256"] = wav_catalog.LEGACY_ALLOWLIST_SHA256
    proof = wav_catalog.restore_parts(
        manifest,
        {e["file"]: folder / e["file"] for e in manifest["files"]},
        tmp_path / "legacy.sqlite3",
    )
    assert {part["kind"] for part in proof} == {"ledger", "gateway", "direct"}


def test_legacy_allowlist_cannot_label_new_server_context(direct_adapter, tmp_path):
    from app.raw_archive import wav_catalog
    from tests.raw_archive.test_wav_catalog import fixture_bundle

    _, reference, sessions, _, _ = captured(direct_adapter)
    with sessions() as db:
        segment = narrow_get(db, Segment, reference[0])
        row = narrow_get(db, Session, (segment.device_id, segment.session_id))
        values = {key: getattr(row, key) for key in COLUMNS["direct"]["direct_session"]}
    folder, manifest = fixture_bundle(tmp_path)
    parts = wav_catalog.Parts(folder, "direct-capture", lambda: None)
    parts.write(wav_catalog.validate_row("direct", "direct_session", values))
    parts.finish()
    for entry in parts.files:
        entry["kind"] = "direct"
    manifest["files"].extend(parts.files)
    manifest["tables"]["direct"]["direct_session"] = 1
    manifest["allowlist_sha256"] = wav_catalog.LEGACY_ALLOWLIST_SHA256
    with pytest.raises(ArchiveBlocked, match="versioned metadata allowlist"):
        wav_catalog.restore_parts(
            manifest,
            {e["file"]: folder / e["file"] for e in manifest["files"]},
            tmp_path / "mislabeled.sqlite3",
        )
