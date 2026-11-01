"""Scientific WAV parents for isolated SQL restoration, never source backfill.

Call after schema/master restoration and before SQL child restoration. The caller
pins the bundle and source reference independently. Existing masters must match;
no device, sensor, enrollment or historical assignment is synthesized.
"""

import json

from app.direct.capture_assignment import split as split_direct_config
from app.raw_archive.policy import ArchiveBlocked, Recording, checksum
from app.raw_archive.wav_catalog import COLUMNS, validate_row

from . import parquet
from .codec import digest, encode
from .pipeline import verify_file
from .recovery import restore_url
from .runtime import candidate_from_json


def load_parent(
    folder,
    manifest_sha256,
    *,
    candidate_id,
    environment,
    source,
    organization,
    reference,
    checkpoint,
):
    """Fully validate captured files before opening the isolated target database."""
    path = folder / "manifest.json"
    if (
        path.is_symlink()
        or not 0 < path.stat().st_size <= 1024**2
        or checksum(path) != manifest_sha256
    ):
        raise ArchiveBlocked("Restore manifest changed or unbounded")
    manifest = json.loads(path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    candidate.validate()
    if (
        manifest["identity"] != candidate.identity
        or candidate.identity != candidate_id
        or candidate.environment != environment
        or candidate.source != source
        or candidate.organization != organization
        or candidate.dataset != source + "_wav"
        or candidate.kind != "wav"
        or candidate.rows != 1
    ):
        raise ArchiveBlocked("WAV restore pinned source identity mismatch")
    if (
        not isinstance(reference, list)
        or (source == "gateway" and len(reference) != 1)
        or (source == "direct" and len(reference) != 2)
    ):
        raise ArchiveBlocked("Explicit source reference list required")
    parts = manifest["files"]
    if (
        not 2 <= len(parts) <= 1025
        or parts[0]["format"] != "parquet"
        or any(part["format"] != "wav" for part in parts[1:])
        or parts[0]["content_sha256"] != candidate.content_sha256
        or parts[0]["rows"] != 1
        or sum(part["bytes"] for part in parts[1:]) > 64 * 1024**2
    ):
        raise ArchiveBlocked("Complete bounded scientific WAV bundle required")
    if (folder / "0000.parquet").is_symlink():
        raise ArchiveBlocked("Symlink is not isolated restore metadata")
    values = list(parquet.read(folder / "0000.parquet", parts[0], checkpoint=checkpoint))
    if len(values) != 1:
        raise ArchiveBlocked("Exactly one scientific WAV metadata row required")
    envelope = values[0]
    body = envelope["body"]
    if (
        envelope["organization"] != organization
        or envelope["identity"] != digest(reference)
        or body["reference"] != reference
    ):
        raise ArchiveBlocked("WAV restore reference or tenant mismatch")
    originals = body["originals"]
    if len(originals) != len(parts) - 1:
        raise ArchiveBlocked("WAV original inventory mismatch")
    records = []
    for number, (original, part) in enumerate(zip(originals, parts[1:], strict=True), 1):
        checkpoint()
        record = Recording.from_receipt(original["recording"])
        expected_id = reference[0] if source == "gateway" else ":".join(map(str, reference))
        if (
            record.kind != source
            or record.identity != expected_id
            or record.sha256 != part["sha256"]
            or record.size != part["bytes"]
            or record.remote_key(environment) != part["key"]
        ):
            raise ArchiveBlocked("WAV original source revision or checksum mismatch")
        artifact = folder / f"{number:04d}.wav"
        if artifact.is_symlink():
            raise ArchiveBlocked("Symlink is not an isolated restore artifact")
        verify_file(artifact, part, checkpoint)
        records.append(record)
    metadata = body["metadata"]
    from app.raw_archive.direct_history import COPY_SCOPE as HISTORICAL_DIRECT
    from app.raw_archive.wav_context import COPY_SCOPE as OBSERVED_CONTEXT
    from app.raw_archive.wav_context import (
        DIRECT_CAPTURE_SCOPE_V3,
        DIRECT_COPY_SCOPE_V2,
        GATEWAY_COPY_SCOPE_V2,
        require_captured_direct,
        require_observed,
    )

    observed = body.get("copy_scope") in {
        OBSERVED_CONTEXT,
        GATEWAY_COPY_SCOPE_V2,
        DIRECT_COPY_SCOPE_V2,
    }
    if "copy_scope" in body and body["copy_scope"] not in {
        DIRECT_CAPTURE_SCOPE_V3,
        HISTORICAL_DIRECT,
        OBSERVED_CONTEXT,
        GATEWAY_COPY_SCOPE_V2,
        DIRECT_COPY_SCOPE_V2,
    }:
        raise ArchiveBlocked("Unknown scientific WAV COPY contract")
    if observed:
        require_observed(metadata, source, candidate, copy_scope=body["copy_scope"])
    captured = body.get("copy_scope") == DIRECT_CAPTURE_SCOPE_V3
    if captured:
        if source != "direct":
            raise ArchiveBlocked("Direct server capture cannot authorize another source")
        require_captured_direct(metadata, candidate)
    if source == "gateway":
        if (
            (not observed and set(metadata) != {"wav", "feature", "feature_versions", "assignment"})
            or ("copy_scope" in body and not observed)
            or len(records) != 1
        ):
            raise ArchiveBlocked("Incomplete Gateway scientific metadata")
        row = metadata["wav"]
        validate_row(source, "wav_file", row)
        validate_row(source, "wav_feature", metadata["feature"])
        feature_format = body.get("feature_history_format")
        if feature_format not in (None, "gateway-signal-history-v2"):
            raise ArchiveBlocked("Unknown Gateway feature history format")
        for version in metadata["feature_versions"]:
            payload = version.get("feature_payload")
            if isinstance(payload, dict) and payload.get("schema") == "gateway-signal-v1":
                if feature_format != "gateway-signal-history-v2":
                    raise ArchiveBlocked("Gateway signal result needs the versioned history format")
                from app.raw_archive.feature_backup import validate_v2_row

                validate_v2_row(source, "wav_feature_version", version)
            else:
                validate_row(source, "wav_feature_version", version)
        record = records[0]
        if (
            str(row["id"]) != reference[0]
            or row["s3_key"] != record.key
            or row["content_sha256"] != record.sha256
            or row["file_size_bytes"] != record.size
            or str(row["sensor_id"]) != envelope["sensor"]
            or metadata["feature"]["source_sha256"] != record.sha256
            or metadata["feature"]["feature_checksum"] != record.feature_digest
            or any(
                str(feature["wav_file_id"]) != str(row["id"])
                for feature in [metadata["feature"], *metadata["feature_versions"]]
            )
        ):
            raise ArchiveBlocked("Gateway scientific parent or feature source mismatch")
        assignment = metadata["observed_assignment"] if observed else metadata["assignment"]
        if (
            set(assignment)
            != {"sensor_id", "gateway_id", "zone_id", "organization_id"}
            | ({"semantics"} if observed else set())
            or str(row["sensor_id"]) != assignment["sensor_id"]
            or str(row["gateway_id"]) != assignment["gateway_id"]
            or assignment["organization_id"] != organization
        ):
            raise ArchiveBlocked("Gateway captured assignment mismatch")
        return candidate, row, metadata, metadata.get("capture_assignment")
    historical = body.get("copy_scope") == HISTORICAL_DIRECT
    if historical:
        from app.raw_archive.direct_history import COPY_SCOPE

        if (
            body["copy_scope"] != COPY_SCOPE
            or set(metadata)
            != {
                "observed_segment",
                "revision",
                "session",
                "capture_assignment",
                "observed_assignment",
            }
            or metadata["capture_assignment"]
            != {
                "status": "unknown",
                "quarantined": True,
                "reason": "source_has_no_immutable_capture_assignment_history",
            }
            or not candidate.quarantined
        ):
            raise ArchiveBlocked("Unknown Direct capture assignment must remain quarantined")
        row, assignment = metadata["observed_segment"], metadata["observed_assignment"]
        if assignment.get("semantics") != "observed_at_archive":
            raise ArchiveBlocked("Historical assignment observation required")
    else:
        if (
            not observed
            and not captured
            and set(metadata) != {"segment", "revision", "session", "assignment"}
        ):
            raise ArchiveBlocked("Incomplete Direct scientific metadata")
        row = metadata["segment"]
        assignment = (
            metadata["observed_assignment"] if observed or captured else metadata["assignment"]
        )
    if set(assignment) != {
        "device_id",
        "organization_id",
        "zone_id",
        "legacy_sensor_id",
        "mode",
        "hardware_id",
    } | ({"semantics"} if historical or observed or captured else set()):
        raise ArchiveBlocked("Unexpected Direct assignment fields")
    validate_row(source, "direct_segment", row)
    revision, session = metadata["revision"], metadata["session"]
    validate_row(source, "direct_revision", revision)
    validate_row(source, "direct_session", session)
    manifest = revision["manifest"]
    if "scientific_history" in body:
        from app.raw_archive.direct_feature_history import validate_history

        validate_history(body["scientific_history"], revision)
    if (
        not isinstance(reference, list)
        or len(reference) != 2
        or type(reference[1]) is not int
        or [row["id"], revision["revision"]] != reference
        or revision["segment_id"] != row["id"]
        or (
            manifest["device_id"],
            manifest["session_id"],
            manifest["revision"],
            manifest["bucket_start_us"],
        )
        != (row["device_id"], row["session_id"], reference[1], row["bucket"] * 600_000_000)
        or row["published_revision"] < reference[1]
        or row["revision"] < reference[1]
        or session["device_id"] != row["device_id"]
        or session["id"] != row["session_id"]
        or split_direct_config(session["config"])[0] != manifest["config"]
        or session["mode"] != manifest["source_mode"]
        or assignment["device_id"] != row["device_id"]
        or (
            metadata["capture_assignment"]["organization_id"]
            if captured
            else assignment["organization_id"]
        )
        != organization
        or envelope["sensor"] != row["device_id"]
        or any(record.feature_digest != revision["manifest_sha256"] for record in records)
        or sorted((record.key, record.sha256) for record in records)
        != sorted((run["key"], run["sha256"]) for run in manifest["runs"])
    ):
        raise ArchiveBlocked("Direct scientific source revision or tenant mismatch")
    return candidate, row, metadata, metadata.get("capture_assignment")


def restore_wav_parent(
    folder,
    database_url,
    manifest_sha256,
    *,
    candidate_id,
    environment,
    source,
    organization,
    reference,
    checkpoint,
):
    """Insert one captured parent; target URL is restricted exactly like SQL restore."""
    from sqlalchemy import MetaData, Table, and_, create_engine, select, text

    url = restore_url(database_url)
    candidate, row, metadata, unknown_assignment = load_parent(
        folder,
        manifest_sha256,
        candidate_id=candidate_id,
        environment=environment,
        source=source,
        organization=organization,
        reference=reference,
        checkpoint=checkpoint,
    )
    scientific_history = None
    gateway_history_v2 = False
    if source in ("direct", "gateway"):
        current_manifest = json.loads((folder / "manifest.json").read_text())
        if checksum(folder / "manifest.json") != manifest_sha256:
            raise ArchiveBlocked("Restore manifest changed")
        part = current_manifest["files"][0]
        verify_file(folder / "0000.parquet", part, checkpoint)
        envelope = list(parquet.read(folder / "0000.parquet", part, checkpoint=checkpoint))[0]
        if source == "direct":
            scientific_history = envelope["body"].get("scientific_history")
            if scientific_history is not None:
                from app.raw_archive.direct_feature_history import validate_history

                validate_history(scientific_history, metadata["revision"])
        else:
            gateway_history_v2 = (
                envelope["body"].get("feature_history_format") == "gateway-signal-history-v2"
            )
    table_name = "wav_file" if source == "gateway" else "direct_segment"
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 3, "options": "-c default_transaction_read_only=off"},
    )
    try:
        with engine.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            db.execute(text("SET LOCAL idle_in_transaction_session_timeout='0'"))
            schema = MetaData()
            table = Table(table_name, schema, autoload_with=db)
            names = (
                ("sensor", "gateway", "zone")
                if source == "gateway"
                else ("direct_device", "direct_session", "direct_enrollment")
            )
            masters = {name: Table(name, schema, autoload_with=db) for name in names}
            visual_name = "visual_wav" if source == "gateway" else "direct_visual_segment"
            visual_key = "wav_id" if source == "gateway" else "segment_id"
            visual_row = metadata.get(visual_name)
            visual = (
                Table(visual_name, schema, autoload_with=db) if visual_row is not None else None
            )
            gateway_feature = (
                Table("wav_feature", schema, autoload_with=db) if gateway_history_v2 else None
            )
            gateway_versions = (
                Table("wav_feature_version", schema, autoload_with=db)
                if gateway_history_v2
                else None
            )
            revision_table = (
                Table("direct_revision", schema, autoload_with=db)
                if scientific_history is not None
                else None
            )
            result_table = (
                Table("direct_feature_result", schema, autoload_with=db)
                if scientific_history is not None
                else None
            )
            for target in (
                table,
                *masters.values(),
                *((visual,) if visual is not None else ()),
                *((revision_table, result_table) if revision_table is not None else ()),
                *((gateway_feature, gateway_versions) if gateway_feature is not None else ()),
            ):
                db.execute(text(f'LOCK TABLE "{target.name}" IN SHARE ROW EXCLUSIVE MODE'))
            verify_masters(db, masters, source, row, metadata, organization)
            if set(COLUMNS[source][table_name]) - set(table.c.keys()):
                raise ArchiveBlocked("Scientific parent schema is incomplete")
            existing = (
                db.execute(select(table).where(table.c.id == row["id"])).mappings().one_or_none()
            )
            # Never overwrite an existing PK, including apparently identical
            # observations: callers must account for each distinct bundle once.
            if existing is not None:
                raise ArchiveBlocked("Restore never overwrites an existing parent")
            checkpoint()
            insert_row = dict(row)
            if source == "direct":
                # Deliberate local scheduler initialization, not reconstructed
                # source state. Private retry/error metadata was never archived.
                insert_row.update(retry_at=0, error=None)
            db.execute(table.insert().values(**insert_row))
            if scientific_history is not None:
                from app.raw_archive.feature_backup import FEATURE_V2_COLUMNS

                revision_row = metadata["revision"]
                if set(revision_row) - set(revision_table.c.keys()) or set(
                    FEATURE_V2_COLUMNS["direct_feature_result"]
                ) != set(result_table.c.keys()):
                    raise ArchiveBlocked("Scientific result restore schema differs")
                db.execute(revision_table.insert().values(**revision_row))
                for result_row in scientific_history["results"]:
                    db.execute(result_table.insert().values(**result_row))
                    actual_result = (
                        db.execute(
                            select(result_table).where(result_table.c.id == result_row["id"])
                        )
                        .mappings()
                        .one()
                    )
                    if encode({key: actual_result[key] for key in result_row}) != encode(
                        result_row
                    ):
                        raise ArchiveBlocked("Restored scientific result differs")
                actual_revision = (
                    db.execute(
                        select(revision_table).where(
                            revision_table.c.segment_id == row["id"],
                            revision_table.c.revision == revision_row["revision"],
                        )
                    )
                    .mappings()
                    .one()
                )
                if encode({key: actual_revision[key] for key in revision_row}) != encode(
                    revision_row
                ):
                    raise ArchiveBlocked("Restored scientific revision differs")
            if gateway_history_v2:
                for target, rows_to_restore in (
                    (gateway_feature, [metadata["feature"]]),
                    (gateway_versions, metadata["feature_versions"]),
                ):
                    for feature_row in rows_to_restore:
                        if set(feature_row) - set(target.c.keys()):
                            raise ArchiveBlocked("Gateway feature restore schema differs")
                        db.execute(target.insert().values(**feature_row))
                        actual = (
                            db.execute(select(target).where(target.c.id == feature_row["id"]))
                            .mappings()
                            .one()
                        )
                        if encode({key: actual[key] for key in feature_row}) != encode(feature_row):
                            raise ArchiveBlocked("Restored Gateway feature differs")
            if visual is not None:
                required = (
                    set(visual_row) if source == "direct" else set(COLUMNS["gateway"]["visual_wav"])
                )
                if required - set(visual.c.keys()):
                    raise ArchiveBlocked("Observed visual parent schema is incomplete")
                if (
                    db.execute(
                        select(visual.c[visual_key]).where(
                            visual.c[visual_key] == visual_row[visual_key]
                        )
                    ).first()
                    is not None
                ):
                    raise ArchiveBlocked("Restore never overwrites an existing visual parent")
                # Gateway's old contract excluded error text. Direct V2 instead
                # preserves the explicitly bounded, observed parent state.
                db.execute(
                    visual.insert().values(
                        **(dict(visual_row, error=None) if source == "gateway" else visual_row)
                    )
                )
                actual_visual = (
                    db.execute(select(visual).where(visual.c[visual_key] == visual_row[visual_key]))
                    .mappings()
                    .one()
                )
                if encode({key: actual_visual[key] for key in visual_row}) != encode(visual_row):
                    raise ArchiveBlocked("Stored visual parent differs from observed source")
            stored = db.execute(select(table).where(and_(table.c.id == row["id"]))).mappings().one()
            if encode({key: stored[key] for key in row}) != encode(row):
                raise ArchiveBlocked("Stored scientific parent differs from archive")
            checkpoint()
            # Full readback again catches changes to any input before commit.
            fresh = load_parent(
                folder,
                manifest_sha256,
                candidate_id=candidate_id,
                environment=environment,
                source=source,
                organization=organization,
                reference=reference,
                checkpoint=checkpoint,
            )
            if encode(fresh[1:]) != encode((row, metadata, unknown_assignment)):
                raise ArchiveBlocked("Restore metadata changed during parent insertion")
        return {
            "restored_parents": 1,
            "gateway_feature_versions_restored": len(metadata["feature_versions"])
            if gateway_history_v2
            else 0,
            "direct_feature_results_restored": len(scientific_history["results"])
            if scientific_history is not None
            else 0,
            "direct_scientific_history_sha256": scientific_history["sha256"]
            if scientific_history is not None
            else None,
            "table": table_name,
            "candidate": candidate.identity,
            "source_accesses": 0,
            "typed_values_verified": True,
            "capture_assignment": unknown_assignment,
            "quarantined": bool(candidate.quarantined),
            "visual_parent_restored": visual_row is not None,
            "visual_wave_children_supported": source == "gateway" and visual_row is not None,
            "direct_visual_point_children_supported": source == "direct" and visual_row is not None,
            "direct_visual_original_state_preserved": source == "direct" and visual_row is not None,
            "visual_parent_error_reconstructed": False,
        }
    finally:
        engine.dispose()


def verify_masters(db, tables, source, row, metadata, organization):
    """Read and compare restored masters, never repair or invent assignments."""
    from sqlalchemy import and_, select

    def one(name, **keys):
        table = tables[name]
        value = (
            db.execute(
                select(table).where(and_(*(table.c[key] == item for key, item in keys.items())))
            )
            .mappings()
            .one_or_none()
        )
        if value is None:
            raise ArchiveBlocked("Required restored master is missing")
        return value

    if source == "gateway":
        assignment = metadata.get("assignment", metadata.get("observed_assignment"))
        sensor = one("sensor", id=row["sensor_id"])
        gateway = one("gateway", id=row["gateway_id"])
        zone = one("zone", id=gateway["zone_id"])
        if (
            str(sensor["gateway_id"]) != assignment["gateway_id"]
            or str(gateway["zone_id"]) != assignment["zone_id"]
            or str(zone["organization_id"]) != organization
            or sensor["mac_address"] != row["sensor_mac"]
        ):
            raise ArchiveBlocked("Restored Gateway master tenant or assignment mismatch")
    else:
        assignment = metadata.get("assignment", metadata.get("observed_assignment"))
        device = one("direct_device", id=row["device_id"])
        session = one("direct_session", device_id=row["device_id"], id=row["session_id"])
        from app.direct.capture_assignment import manifest_assignment

        capture = manifest_assignment(metadata["revision"]["manifest"])
        if capture is not None:
            if (
                capture != split_direct_config(session["config"])[1]
                or capture != split_direct_config(metadata["session"]["config"])[1]
                or capture["organization_id"] != organization
            ):
                raise ArchiveBlocked("Restored Direct capture assignment or tenant mismatch")
        elif assignment["organization_id"] != organization:
            raise ArchiveBlocked("Restored Direct master tenant or assignment mismatch")
        if any(
            str(device[key]) != str(assignment[key])
            for key in ("organization_id", "zone_id", "legacy_sensor_id", "mode")
        ):
            raise ArchiveBlocked("Restored Direct master tenant or assignment mismatch")
        if encode({key: session[key] for key in COLUMNS["direct"]["direct_session"]}) != encode(
            metadata["session"]
        ):
            raise ArchiveBlocked("Restored Direct session differs from captured metadata")
        enrollment = tables["direct_enrollment"]
        enrolled = db.execute(
            select(enrollment.c.hardware_id).where(enrollment.c.device_id == row["device_id"])
        ).scalar_one_or_none()
        if enrolled != assignment["hardware_id"]:
            raise ArchiveBlocked("Restored Direct hardware enrollment mismatch")
