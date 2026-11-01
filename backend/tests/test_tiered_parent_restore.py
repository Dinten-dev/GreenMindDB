"""Archive-only parents and real SQLite FK/rollback; not a PostgreSQL lock proof."""

import hashlib
import json
import wave
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import IntegrityError

from app.raw_archive.policy import ArchiveBlocked, Recording, checksum
from app.raw_archive.wav_catalog import COLUMNS, FEATURE_FIELDS, raw_json
from app.tiered_archive import parquet
from app.tiered_archive.codec import digest, epoch_us
from app.tiered_archive.parent_restore import restore_wav_parent
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.policy import Candidate
from app.tiered_archive.recovery import restore_database
from app.tiered_archive.wav_source import record_json

AT = datetime(2026, 9, 7, tzinfo=UTC)
ORG, SENSOR, GATEWAY, ZONE, PARENT, SESSION = [
    f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 7)
]
URL = "postgresql+psycopg2://restore@127.0.0.1:35432/greenmind_restore_parents"
MAC = "00:11:22:33:44:55"


def scientific(kind, table, **values):
    return dict.fromkeys(COLUMNS[kind][table]) | values


def bundle(tmp_path, source, mutate=lambda metadata: None, historical=False, observed=False):
    folder = tmp_path / "bundle"
    folder.mkdir()
    wav = folder / "0001.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(380)
        out.writeframes(b"\x01\x00" * 380)
    reference = [PARENT] if source == "gateway" else [PARENT, 1]
    sha, size = checksum(wav), wav.stat().st_size
    if source == "gateway":
        feature = {key: 0 for key in FEATURE_FIELDS}
        for key in ("technical_fault_reasons", "biological_candidate_reasons", "anomaly_reasons"):
            feature[key] = []
        for key in ("quantiles", "spectral_bands", "source_quality_counts"):
            feature[key] = {}
        feature.update(source_sha256=sha)
        metadata = dict(
            wav=scientific(
                source,
                "wav_file",
                id=PARENT,
                sensor_id=SENSOR,
                gateway_id=GATEWAY,
                sensor_mac=MAC,
                s3_key="original.wav",
                content_sha256=sha,
                file_size_bytes=size,
            ),
            feature=scientific(
                source,
                "wav_feature",
                **feature,
                id="feature",
                wav_file_id=PARENT,
                feature_checksum="f" * 64,
            ),
            feature_versions=[
                scientific(
                    source,
                    "wav_feature_version",
                    id="version",
                    wav_file_id=PARENT,
                    source_sha256=sha,
                    feature_payload={},
                )
            ],
            assignment=dict(
                sensor_id=SENSOR, gateway_id=GATEWAY, zone_id=ZONE, organization_id=ORG
            ),
        )
        feature_digest = "f" * 64
    else:
        config = dict(
            protocol_version=1,
            device_id=SENSOR,
            session_id=SESSION,
            session_start_us=epoch_us(AT),
            sample_rate=380,
            channels=1,
            sample_bits=16,
            channel_labels=["leaf0"],
            calibration_version="cal-v1",
            firmware_version="fw-v1",
        )
        revision_manifest = dict(
            protocol_version=1,
            device_id=SENSOR,
            session_id=SESSION,
            bucket_start_us=epoch_us(AT),
            revision=1,
            config=config,
            source_mode="DIRECT",
            analytics_scope="direct",
            first_frame=0,
            end_frame=380,
            initial_partial=False,
            status="complete",
            missing_frame_ranges=[],
            runs=[
                dict(
                    first_frame=0,
                    frame_count=380,
                    started_at_us=epoch_us(AT),
                    key="original.wav",
                    sha256=sha,
                    features=[],
                )
            ],
        )
        feature_digest = hashlib.sha256(raw_json(revision_manifest)).hexdigest()
        metadata = dict(
            segment=scientific(
                source,
                "direct_segment",
                id=PARENT,
                device_id=SENSOR,
                session_id=SESSION,
                bucket=epoch_us(AT) // 600_000_000,
                first_frame=0,
                end_frame=380,
                received_frames=380,
                revision=2 if historical else 1,
                published_revision=2 if historical else 1,
                sealed=True,
                updated_at=1.0,
            ),
            revision=scientific(
                source,
                "direct_revision",
                segment_id=PARENT,
                revision=1,
                manifest=revision_manifest,
                manifest_sha256=feature_digest,
                verified_at=1.0,
            ),
            session=scientific(
                source, "direct_session", device_id=SENSOR, id=SESSION, config=config, mode="DIRECT"
            ),
            assignment=dict(
                device_id=SENSOR,
                organization_id=ORG,
                zone_id=ZONE,
                legacy_sensor_id=None,
                mode="DIRECT",
                hardware_id=None,
            ),
        )
        if historical:
            metadata["observed_segment"] = metadata.pop("segment")
            metadata["observed_assignment"] = metadata.pop("assignment") | {
                "semantics": "observed_at_archive"
            }
            metadata["capture_assignment"] = dict(
                status="unknown",
                quarantined=True,
                reason="source_has_no_immutable_capture_assignment_history",
            )
    if observed:
        from app.raw_archive.wav_context import observe

        metadata = observe(metadata, source)
    mutate(metadata)
    record = Recording(
        source,
        PARENT if source == "gateway" else PARENT + ":1",
        "test-bucket",
        "original.wav",
        sha,
        size,
        AT + timedelta(seconds=1),
        AT + timedelta(seconds=2),
        feature_digest,
        AT,
        source + "-" + SENSOR,
    )
    body = dict(
        reference=reference, metadata=metadata, originals=[{"recording": record_json(record)}]
    )
    if historical:
        from app.raw_archive.direct_history import COPY_SCOPE

        body["copy_scope"] = COPY_SCOPE
    if observed:
        from app.raw_archive.wav_context import COPY_SCOPE

        body["copy_scope"] = COPY_SCOPE
    envelope = dict(
        identity=digest(reference), organization=ORG, sensor=SENSOR, time_us=epoch_us(AT), body=body
    )
    primary = dict(parquet.write(folder / "0000.parquet", [envelope]), format="parquet")
    candidate = Candidate(
        "production",
        source,
        source + "_wav",
        ORG,
        AT,
        AT + timedelta(seconds=1),
        primary["content_sha256"],
        1,
        "wav",
        AT + timedelta(seconds=2),
        **(
            dict(
                complete=False,
                dependencies_complete=False,
                projections_verified=False,
                features_verified=True,
                quarantined=True,
            )
            if observed
            else dict(quarantined=historical)
        ),
    )
    files = [
        primary,
        dict(
            format="wav",
            bytes=size,
            sha256=sha,
            key=record.remote_key("production"),
            wav_header=dict(frames=380, rate=380, channels=1, width=2),
        ),
    ]
    manifest = dict(candidate=candidate_json(candidate), identity=candidate.identity, files=files)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return (
        folder,
        checksum(folder / "manifest.json"),
        dict(
            candidate_id=candidate.identity,
            environment="production",
            source=source,
            organization=ORG,
            reference=reference,
        ),
        metadata,
    )


def target(tmp_path, monkeypatch, source, metadata):
    engine = create_engine("sqlite:///" + str(tmp_path / "restore.sqlite"))

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def directives(conn, cursor, statement, parameters, context, many):
        if statement.startswith(("SET LOCAL ", "LOCK TABLE ")):
            return "SELECT 1", ()
        return statement, parameters

    schema = MetaData()
    parent_name = "wav_file" if source == "gateway" else "direct_segment"
    row = metadata.get("wav", metadata.get("segment", metadata.get("observed_segment")))
    columns = [
        Column(
            key,
            Boolean
            if isinstance(value, bool)
            else Integer
            if isinstance(value, int)
            else Float
            if isinstance(value, float)
            else String,
            primary_key=key == "id",
        )
        for key, value in row.items()
    ]
    if source == "direct":
        columns += [Column("retry_at", Float, nullable=False), Column("error", String)]
    Table(parent_name, schema, *columns)
    if source == "direct":
        Table(
            "direct_device",
            schema,
            Column("id", String, primary_key=True),
            *(
                Column(key, String)
                for key in ("organization_id", "zone_id", "legacy_sensor_id", "mode")
            ),
        )
        Table(
            "direct_session",
            schema,
            Column("device_id", String, primary_key=True),
            Column("id", String, primary_key=True),
            Column("config", JSON),
            Column("mode", String),
        )
        Table(
            "direct_enrollment",
            schema,
            Column("hardware_id", String, primary_key=True),
            Column("device_id", String),
        )
    else:
        Table(
            "sensor",
            schema,
            Column("id", String, primary_key=True),
            Column("gateway_id", String),
            Column("mac_address", String),
        )
        Table("gateway", schema, Column("id", String, primary_key=True), Column("zone_id", String))
        Table(
            "zone",
            schema,
            Column("id", String, primary_key=True),
            Column("organization_id", String),
        )
    child_name = "wav_feature_version" if source == "gateway" else "direct_revision"
    child_row = metadata["feature_versions"][0] if source == "gateway" else metadata["revision"]
    child_columns = []
    for key, value in child_row.items():
        datatype = (
            JSON
            if isinstance(value, dict)
            else Integer
            if isinstance(value, int)
            else Float
            if isinstance(value, float)
            else String
        )
        foreign = [ForeignKey(parent_name + ".id")] if key in {"wav_file_id", "segment_id"} else []
        child_columns.append(
            Column(
                key,
                datatype,
                *foreign,
                primary_key=key in ({"id"} if source == "gateway" else {"segment_id", "revision"}),
            )
        )
    Table(child_name, schema, *child_columns)
    schema.create_all(engine)
    with engine.begin() as db:
        if source == "direct":
            assignment = metadata.get("assignment", metadata.get("observed_assignment"))
            db.execute(
                schema.tables["direct_device"]
                .insert()
                .values(
                    **{
                        key: assignment[key]
                        for key in schema.tables["direct_device"].c.keys()
                        if key != "id"
                    },
                    id=SENSOR,
                )
            )
            db.execute(schema.tables["direct_session"].insert().values(**metadata["session"]))
        else:
            db.execute(
                text("INSERT INTO sensor VALUES(:id,:gateway,:mac)"),
                dict(id=SENSOR, gateway=GATEWAY, mac=MAC),
            )
            db.execute(text("INSERT INTO gateway VALUES(:id,:zone)"), dict(id=GATEWAY, zone=ZONE))
            db.execute(text("INSERT INTO zone VALUES(:id,:org)"), dict(id=ZONE, org=ORG))
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    return engine, parent_name


def sql_child_bundle(tmp_path, source, metadata):
    folder = tmp_path / "sqlhistory"
    folder.mkdir()
    dataset = "wav_feature_version" if source == "gateway" else "direct_revision"
    row = metadata["feature_versions"][0] if source == "gateway" else metadata["revision"]
    envelope = dict(
        identity="child",
        organization=ORG,
        sensor=SENSOR,
        time_us=epoch_us(AT),
        body=dict(row=row, context={}),
    )
    part = dict(parquet.write(folder / "0000.parquet", [envelope]), format="parquet")
    candidate = Candidate(
        "production",
        source,
        dataset,
        ORG,
        AT,
        AT + timedelta(seconds=1),
        part["content_sha256"],
        1,
        "features" if source == "gateway" else "direct_payload",
        AT,
    )
    path = folder / "manifest.json"
    path.write_text(
        json.dumps(
            dict(candidate=candidate_json(candidate), identity=candidate.identity, files=[part])
        )
    )
    return folder, checksum(path)


@pytest.mark.parametrize(
    "source,historical", [("gateway", False), ("direct", False), ("direct", True)]
)
def test_captured_parent_unlocks_real_fk_without_inventing_assignment(
    tmp_path, monkeypatch, source, historical
):
    folder, sha, pins, metadata = bundle(tmp_path, source, historical=historical)
    engine, table = target(tmp_path, monkeypatch, source, metadata)
    sql_folder, sql_sha = sql_child_bundle(tmp_path, source, metadata)
    with pytest.raises(IntegrityError):
        restore_database(sql_folder, URL, sql_sha, checkpoint=lambda: None)
    result = restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    assert result["restored_parents"] == 1 and result["source_accesses"] == 0
    assert result["quarantined"] is historical
    assert result["capture_assignment"] == metadata.get("capture_assignment")
    sql_result = restore_database(sql_folder, URL, sql_sha, checkpoint=lambda: None)
    assert sql_result == {"restored_rows": 1, "typed_values_verified": True, "source_accesses": 0}
    with engine.begin() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1
        if historical:
            assert (
                db.execute(text("SELECT published_revision FROM direct_segment")).scalar_one() == 2
            )
            assert (
                db.execute(text("SELECT legacy_sensor_id FROM direct_device")).scalar_one() is None
            )


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_duplicate_parent_conflict_never_overwrites(tmp_path, monkeypatch, source):
    folder, sha, pins, metadata = bundle(tmp_path, source)
    engine, table = target(tmp_path, monkeypatch, source, metadata)
    restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.begin() as db:
        db.execute(
            text(
                f"UPDATE {table} SET {'s3_key' if source == 'gateway' else 'session_id'}='existing-conflict'"
            )
        )
    with pytest.raises(ArchiveBlocked, match="overwrites"):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.connect() as db:
        assert (
            db.execute(
                text(f"SELECT {'s3_key' if source == 'gateway' else 'session_id'} FROM {table}")
            ).scalar_one()
            == "existing-conflict"
        )


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_foreign_tenant_master_rolls_back_without_reassignment(tmp_path, monkeypatch, source):
    folder, sha, pins, metadata = bundle(tmp_path, source)
    engine, table = target(tmp_path, monkeypatch, source, metadata)
    master = "zone" if source == "gateway" else "direct_device"
    with engine.begin() as db:
        db.execute(text(f"UPDATE {master} SET organization_id='foreign-tenant'"))
    with pytest.raises(ArchiveBlocked, match="tenant"):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.connect() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert (
            db.execute(text(f"SELECT organization_id FROM {master}")).scalar_one()
            == "foreign-tenant"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization", ZONE),
        ("candidate_id", "f" * 64),
        ("environment", "staging"),
        ("reference", [ZONE]),
    ],
)
def test_pinned_identity_mismatch_before_any_connection(tmp_path, monkeypatch, field, value):
    folder, sha, pins, _ = bundle(tmp_path, "gateway")
    pins[field] = value
    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda *a, **kw: pytest.fail("Must not connect")
    )
    with pytest.raises(ArchiveBlocked):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)


@pytest.mark.parametrize(
    "mutation", ["wav", "manifest", "feature_source", "revision_hash", "unknown_assignment"]
)
def test_corrupt_or_misbound_captured_inputs_rejected(tmp_path, monkeypatch, mutation):
    def mutate(metadata):
        if mutation == "feature_source":
            metadata["feature"]["source_sha256"] = "f" * 64
        elif mutation == "revision_hash":
            metadata["revision"]["manifest_sha256"] = "f" * 64
        elif mutation == "unknown_assignment":
            metadata["capture_assignment"]["status"] = "known"

    source = "direct" if mutation in {"revision_hash", "unknown_assignment"} else "gateway"
    folder, sha, pins, _ = bundle(
        tmp_path, source, mutate, historical=mutation == "unknown_assignment"
    )
    if mutation in {"wav", "manifest"}:
        with (folder / ("0001.wav" if mutation == "wav" else "manifest.json")).open("ab") as out:
            out.write(b"x")
    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda *a, **kw: pytest.fail("Must not connect")
    )
    with pytest.raises(ArchiveBlocked):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)


def test_readback_trigger_mutation_rolls_back_real_parent(tmp_path, monkeypatch):
    folder, sha, pins, metadata = bundle(tmp_path, "gateway")
    engine, table = target(tmp_path, monkeypatch, "gateway", metadata)
    with engine.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER mutate_parent AFTER INSERT ON wav_file BEGIN UPDATE wav_file SET s3_key='changed'; END"
            )
        )
    with pytest.raises(ArchiveBlocked, match="differs"):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.connect() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_nonisolated_target_rejected_without_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda *a, **kw: pytest.fail("Must not connect")
    )
    with pytest.raises(ArchiveBlocked, match="dedicated local"):
        restore_wav_parent(
            tmp_path,
            URL.replace("greenmind_restore_parents", "plantdb"),
            "a" * 64,
            candidate_id="a" * 64,
            environment="production",
            source="gateway",
            organization=ORG,
            reference=[PARENT],
            checkpoint=lambda: None,
        )


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_missing_restored_master_blocks_parent_without_synthesis(tmp_path, monkeypatch, source):
    folder, sha, pins, metadata = bundle(tmp_path, source)
    engine, table = target(tmp_path, monkeypatch, source, metadata)
    master = "sensor" if source == "gateway" else "direct_session"
    with engine.begin() as db:
        db.execute(text(f"DELETE FROM {master}"))
    with pytest.raises(ArchiveBlocked, match="master is missing"):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.connect() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert db.execute(text(f"SELECT count(*) FROM {master}")).scalar_one() == 0


def test_historical_feature_versions_keep_distinct_source_identities(tmp_path, monkeypatch):
    def mutate(metadata):
        metadata["feature_versions"][0]["source_sha256"] = "b" * 64

    folder, sha, pins, metadata = bundle(tmp_path, "gateway", mutate)
    engine, _ = target(tmp_path, monkeypatch, "gateway", metadata)
    restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    sql_folder, sql_sha = sql_child_bundle(tmp_path, "gateway", metadata)
    restore_database(sql_folder, URL, sql_sha, checkpoint=lambda: None)
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT source_sha256 FROM wav_feature_version")).scalar_one()
            == "b" * 64
        )


def test_modified_artifact_after_insert_rolls_back_parent(tmp_path, monkeypatch):
    folder, sha, pins, metadata = bundle(tmp_path, "gateway")
    engine, table = target(tmp_path, monkeypatch, "gateway", metadata)

    @event.listens_for(engine, "after_cursor_execute")
    def mutate_input(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO wav_file"):
            with (folder / "0001.wav").open("ab") as out:
                out.write(b"changed")

    with pytest.raises(ArchiveBlocked, match="differs"):
        restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    with engine.connect() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_observed_context_parent_restores_real_fk_without_promoting_unknown(
    tmp_path, monkeypatch, source
):
    from app.raw_archive.wav_context import UNKNOWN

    folder, sha, pins, metadata = bundle(tmp_path, source, observed=True)
    engine, table = target(tmp_path, monkeypatch, source, metadata)
    sql_folder, sql_sha = sql_child_bundle(tmp_path, source, metadata)
    with pytest.raises(IntegrityError):
        restore_database(sql_folder, URL, sql_sha, checkpoint=lambda: None)
    result = restore_wav_parent(folder, URL, sha, **pins, checkpoint=lambda: None)
    assert result["quarantined"] is True and result["capture_assignment"] == UNKNOWN
    assert result["source_accesses"] == 0
    assert "assignment" not in metadata
    assert metadata["observed_assignment"]["semantics"] == "observed_at_archive"
    assert restore_database(sql_folder, URL, sql_sha, checkpoint=lambda: None)["restored_rows"] == 1
    with engine.connect() as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 1


@pytest.mark.parametrize("source", ["gateway", "direct"])
@pytest.mark.parametrize("change", ["known", "unquarantined", "capture_semantics", "extra_field"])
def test_observed_context_tampering_blocks_before_connection(tmp_path, monkeypatch, source, change):
    from app.tiered_archive.parent_restore import load_parent

    def mutate(metadata):
        if change == "known":
            metadata["capture_assignment"]["status"] = "known"
        elif change == "unquarantined":
            metadata["capture_assignment"]["quarantined"] = False
        elif change == "capture_semantics":
            metadata["observed_assignment"]["semantics"] = "at_capture"
        else:
            metadata["observed_assignment"]["secret"] = "must_not_enter_archive"

    folder, sha, pins, _ = bundle(tmp_path, source, mutate=mutate, observed=True)
    with pytest.raises(ArchiveBlocked, match="COPY-only quarantined"):
        load_parent(folder, sha, **pins, checkpoint=lambda: None)


@pytest.mark.parametrize("source", ["gateway", "direct"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("quarantined", False),
        ("complete", True),
        ("dependencies_complete", True),
        ("projections_verified", True),
        ("features_verified", False),
    ],
)
def test_observed_candidate_cannot_promote_copy_to_complete(tmp_path, source, field, value):
    from app.tiered_archive.parent_restore import load_parent
    from app.tiered_archive.runtime import candidate_from_json

    folder, _, pins, _ = bundle(tmp_path, source, observed=True)
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["candidate"][field] = value
    candidate = candidate_from_json(manifest["candidate"])
    manifest["identity"] = candidate.identity
    pins["candidate_id"] = candidate.identity
    path.write_text(json.dumps(manifest))
    with pytest.raises(ArchiveBlocked, match="COPY-only quarantined"):
        load_parent(folder, checksum(path), **pins, checkpoint=lambda: None)
