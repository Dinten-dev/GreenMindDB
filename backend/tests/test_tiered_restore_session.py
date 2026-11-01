"""Real SQLite transaction/FK tests; no native PostgreSQL/OID acceptance."""

import json

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import parquet
from app.tiered_archive import restore_session as rs
from app.tiered_archive import restore_universe as universe
from app.tiered_archive.codec import digest, epoch_us
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.policy import Candidate
from app.tiered_archive.source import DATASETS, row_identity
from tests.restore_bounds_sqlite import install_bounds_adapter
from tests.restore_fence_sqlite import install_sqlite_adapter
from tests.test_tiered_archive import AT, ORG, SENSOR
from tests.test_tiered_restore import URL

GATEWAY = "10000000-0000-0000-0000-000000000001"
ZONE = "10000000-0000-0000-0000-000000000002"


@pytest.fixture
def target(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "restore.sqlite"))

    @event.listens_for(engine, "connect")
    def fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("gm_restore_origin", 0, lambda: "origin")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def directives(conn, cursor, statement, parameters, context, many):
        if statement.startswith(("SET LOCAL ", "LOCK TABLE ")):
            return "SELECT 1", ()
        if statement.startswith("SELECT pg_try_advisory_xact_lock"):
            return "SELECT true AS acquired", ()
        return statement, parameters

    with engine.begin() as db:
        for statement in [
            "CREATE TABLE zone(id TEXT PRIMARY KEY,organization_id TEXT)",
            "CREATE TABLE gateway(id TEXT PRIMARY KEY,zone_id TEXT REFERENCES zone(id))",
            "CREATE TABLE ingest_log(measurement_id TEXT PRIMARY KEY,gateway_id TEXT REFERENCES gateway(id),payload BLOB)",
        ]:
            db.execute(text(statement))
        db.execute(text("INSERT INTO zone VALUES(:id,:org)"), dict(id=ZONE, org=ORG))
        db.execute(text("INSERT INTO gateway VALUES(:id,:zone)"), dict(id=GATEWAY, zone=ZONE))

    def sqlite_inventory(db):
        names = db.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
        return {
            ("public", name)
            for name in names
            if name not in universe.OWN_TABLES and not name.startswith("sqlite_")
        }

    monkeypatch.setattr(universe, "relation_inventory", sqlite_inventory)

    def current_universe(source):
        with engine.connect() as db:
            names = sqlite_inventory(db)
        return {
            "version": 1,
            "tables": {
                name: "data" if name in universe.allowed_data(source) else "master"
                for _, name in sorted(names)
            },
        }

    monkeypatch.setattr(
        __import__(__name__, fromlist=["x"]), "universe_inventory", current_universe
    )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(rs, "target_identity", install_sqlite_adapter(monkeypatch))
    install_bounds_adapter(monkeypatch)
    yield engine
    engine.dispose()


def log(identity, payload=b"\x00\xff", gateway=GATEWAY):
    return dict(measurement_id=identity, gateway_id=gateway, payload=payload)


def universe_inventory(source):
    return {"version": 1, "tables": {"ingest_log": "data", "gateway": "master", "zone": "master"}}


def plan(
    tmp_path,
    groups,
    *,
    source="gateway",
    dataset="ingest_log",
    changes=None,
    organization=ORG,
    freeze=False,
):
    spec = DATASETS[dataset]
    page_count = rows = 0
    batches = []
    for group in groups:
        pages = []
        for values in group:
            folder = tmp_path / f"page-{page_count}"
            folder.mkdir()
            bodies = [
                item if set(item) == {"row", "context"} else dict(row=item, context={})
                for item in values
            ]
            envelopes = [
                dict(
                    identity=row_identity(spec, body["row"]),
                    organization=organization,
                    sensor=SENSOR,
                    time_us=epoch_us(AT) + i,
                    body=body,
                )
                for i, body in enumerate(bodies)
            ]
            part = dict(parquet.write(folder / "0000.parquet", envelopes), format="parquet")
            candidate = Candidate(
                "production",
                source,
                dataset,
                organization,
                AT,
                AT.replace(year=AT.year + 1),
                part["content_sha256"],
                len(values),
                spec.kind,
                AT,
            )
            manifest = dict(
                identity=candidate.identity, candidate=candidate_json(candidate), files=[part]
            )
            file = folder / "manifest.json"
            file.write_text(json.dumps(manifest))
            if freeze:
                from app.tiered_archive.restore_inputs import freeze_restore_page

                frozen_folder = tmp_path / f"frozen-page-{page_count}"
                freeze_restore_page(folder, frozen_folder, checksum(file), checkpoint=lambda: None)
                folder = frozen_folder
            pages.append(
                dict(
                    folder=str(folder),
                    candidate_id=candidate.identity,
                    manifest_sha256=checksum(file),
                )
            )
            page_count += 1
            rows += len(values)
        batches.append(pages)
    head = dict(
        format=rs.FORMAT,
        universe=universe_inventory(source),
        environment="production",
        source=source,
        dataset=dataset,
        organization=organization,
        schema_master_sha256="a" * 64,
        expected_pages=page_count,
        expected_rows=rows,
    )
    if changes:
        head.update(changes)
    next_sha = None
    descriptors = []
    for index in reversed(range(len(batches))):
        path = tmp_path / f"batch-{index}.json"
        path.write_text(
            json.dumps(
                dict(
                    version=1,
                    scope_sha256=digest(head),
                    sequence=index,
                    next_sha256=next_sha,
                    pages=batches[index],
                )
            )
        )
        next_sha = checksum(path)
        if freeze:
            from app.tiered_archive.restore_inputs import freeze_restore_descriptor

            frozen_path = tmp_path / f"frozen-batch-{index}.json"
            freeze_restore_descriptor(path, frozen_path, next_sha, checkpoint=lambda: None)
            path = frozen_path
        descriptors.insert(0, (path, next_sha))
    head["first_batch_sha256"] = next_sha
    return head, digest(head), descriptors


def begin(head, sha):
    result = rs.begin_restore_session(URL, head, sha, checkpoint=lambda: None)
    if not result["master_baseline_ready"]:
        for _ in range(100):
            step = rs.advance_restore_baseline(
                URL, head, sha, result["session_id"], checkpoint=lambda: None
            )
            if step["ready"]:
                break
        else:
            pytest.fail("Synthetic master baseline failed to finish")
    return result["session_id"]


def apply(head, sha, session, descriptor, checkpoint=lambda: None):
    return rs.apply_restore_batch(URL, head, sha, session, *descriptor, checkpoint=checkpoint)


def state(engine):
    with engine.connect() as db:
        return dict(
            rows=db.execute(text("SELECT count(*) FROM ingest_log")).scalar_one(),
            sessions=[
                dict(row)
                for row in db.execute(text("SELECT * FROM greenmind_restore_session")).mappings()
            ],
            batches=db.execute(text("SELECT count(*) FROM greenmind_restore_batch")).scalar_one(),
            pages=db.execute(text("SELECT count(*) FROM greenmind_restore_page")).scalar_one(),
        )


def test_multiple_calls_same_atomic_target_and_full_typed_replay(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")], [log("three")]]])
    session = begin(head, sha)
    first = apply(head, sha, session, descriptors[0])
    assert first["committed_rows"] == 1 and not first["chain_loaded"]
    assert apply(head, sha, session, descriptors[0])["replayed"]
    final = apply(head, sha, session, descriptors[1])
    assert final["chain_loaded"] and final["committed_rows"] == 3 and final["committed_pages"] == 3
    assert apply(head, sha, session, descriptors[1])["replayed"]
    assert apply(head, sha, session, descriptors[0])["committed_rows"] == 3
    assert state(target)["batches"] == 2 and state(target)["pages"] == 3
    with target.connect() as db:
        assert (
            db.execute(text("SELECT payload FROM ingest_log")).scalars().all() == [b"\x00\xff"] * 3
        )
        assert db.execute(text("SELECT count(*) FROM zone")).scalar_one() == 1


def test_more_than_32_pages_across_bounded_calls(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log(str(i))]] for i in range(33)])
    session = begin(head, sha)
    for descriptor in descriptors:
        result = apply(head, sha, session, descriptor)
    assert result["chain_loaded"] and result["committed_pages"] == 33
    assert state(target)["rows"] == 33


def test_out_of_order_link_keeps_prefix(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")]]])
    session = begin(head, sha)
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="chain"):
        apply(head, sha, session, descriptors[1])
    assert state(target) == before
    apply(head, sha, session, descriptors[0])
    assert state(target)["rows"] == 1


@pytest.mark.parametrize("payload", [b"same", b"different"])
def test_overlapping_primary_key_aborts_new_batch(target, tmp_path, payload):
    head, sha, descriptors = plan(
        tmp_path, [[[log("existing", b"same")]], [[log("fresh")], [log("existing", payload)]]]
    )
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    before = state(target)
    with pytest.raises((IntegrityError, ArchiveBlocked)):
        apply(head, sha, session, descriptors[1])
    assert state(target) == before
    with target.connect() as db:
        assert (
            db.execute(
                text("SELECT payload FROM ingest_log WHERE measurement_id='existing'")
            ).scalar_one()
            == b"same"
        )


def test_later_corruption_atomic_rollback_and_resume(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")], [log("three")]]])
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    before = state(target)
    path = tmp_path / "page-2/0000.parquet"
    original = path.read_bytes()
    path.write_bytes(original + b"bad")
    with pytest.raises(ArchiveBlocked):
        apply(head, sha, session, descriptors[1])
    assert state(target) == before
    path.write_bytes(original)
    assert apply(head, sha, session, descriptors[1])["chain_loaded"]


def test_interruption_rolls_back_rows_and_journal(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")], [log("two")]]])
    session = begin(head, sha)
    before = state(target)

    @event.listens_for(target, "after_cursor_execute")
    def interrupt(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO ingest_log"):
            raise KeyboardInterrupt("synthetic interruption")

    with pytest.raises(KeyboardInterrupt):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_changed_master_tenant_blocks_without_target_change(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    with target.begin() as db:
        db.execute(text("UPDATE zone SET organization_id='foreign'"))
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="mutation|master|epoch"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_foreign_tenant_at_begin_rejected_by_real_target_join(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    with target.begin() as db:
        db.execute(text("UPDATE zone SET organization_id='foreign'"))
    session = begin(head, sha)
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="tenant links"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


@pytest.mark.parametrize(
    "field,value", [("organization", ZONE), ("source", "direct"), ("environment", "staging")]
)
def test_changed_head_scope_rejected_before_connection(target, tmp_path, monkeypatch, field, value):
    head, sha, _ = plan(tmp_path, [[[log("one")]]])
    head[field] = value
    monkeypatch.setattr(rs, "connection", lambda *a: pytest.fail("Must not connect"))
    with pytest.raises(ArchiveBlocked):
        begin(head, sha)


def test_wrong_target_oid_rejected(target, tmp_path, monkeypatch):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    before = state(target)
    monkeypatch.setattr(rs, "target_identity", lambda db: digest({"oid": 78}))
    with pytest.raises(ArchiveBlocked, match="target binding"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_changed_row_not_idempotent_replay(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    with target.begin() as db:
        db.execute(text("UPDATE ingest_log SET payload=x'00'"))
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="Unexpected restore database mutation"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_changed_artifact_not_idempotent_replay(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    path = tmp_path / "page-0/0000.parquet"
    path.write_bytes(path.read_bytes() + b"changed")
    before = state(target)
    with pytest.raises(ArchiveBlocked):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


@pytest.mark.parametrize("changes", [dict(expected_pages=2), dict(expected_rows=2)])
def test_truncated_chain_cannot_claim_complete(target, tmp_path, changes):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], changes=changes)
    session = begin(head, sha)
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="truncated"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_nonempty_initial_table_not_adopted(target, tmp_path):
    with target.begin() as db:
        db.execute(text("INSERT INTO ingest_log VALUES('original',:g,x'00')"), dict(g=GATEWAY))
    head, sha, _ = plan(tmp_path, [[[log("one")]]])
    with pytest.raises(ArchiveBlocked, match="empty"):
        begin(head, sha)
    with target.connect() as db:
        assert db.execute(text("SELECT measurement_id FROM ingest_log")).scalar_one() == "original"


def test_same_begin_recovers_committed_session_without_reset(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    before = state(target)
    assert begin(head, sha) == session
    assert state(target) == before
    different = dict(head, schema_master_sha256="b" * 64)
    with pytest.raises(ArchiveBlocked, match="Restore application universe changed"):
        begin(different, digest(different))
    assert state(target) == before


def test_unjournaled_insert_not_adopted(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    with target.begin() as db:
        db.execute(text("INSERT INTO ingest_log VALUES('outsider',:g,x'00')"), dict(g=GATEWAY))
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="mutation|Unjournaled"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_33_pages_one_call_rejected_before_connection(target, tmp_path, monkeypatch):
    head, sha, descriptors = plan(tmp_path, [[[log(str(i))] for i in range(33)]])
    session = begin(head, sha)
    monkeypatch.setattr(rs, "connection", lambda *a: pytest.fail("Must not connect"))
    with pytest.raises(ArchiveBlocked, match="Bounded nonempty"):
        apply(head, sha, session, descriptors[0])


def test_changed_target_schema_blocks_batch(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    with target.begin() as db:
        db.execute(text("ALTER TABLE ingest_log ADD COLUMN additional TEXT"))
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="catalog|relation|schema"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def test_later_page_trigger_cannot_change_earlier_page(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")], [log("two")]]])
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER change_earlier AFTER INSERT ON ingest_log WHEN NEW.measurement_id='two' BEGIN UPDATE ingest_log SET payload=x'00' WHERE measurement_id='one'; END"
            )
        )
    session = begin(head, sha)
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="Stored SQL values"):
        apply(head, sha, session, descriptors[0])
    assert state(target) == before


def direct_target(target):
    statements = [
        "CREATE TABLE direct_device(id TEXT PRIMARY KEY,organization_id TEXT)",
        "CREATE TABLE direct_session(device_id TEXT REFERENCES direct_device(id),id TEXT,config JSON,mode TEXT,PRIMARY KEY(device_id,id))",
        "CREATE TABLE direct_segment(id TEXT PRIMARY KEY,device_id TEXT REFERENCES direct_device(id))",
        "CREATE TABLE direct_chunk(id TEXT PRIMARY KEY,device_id TEXT REFERENCES direct_device(id),payload BLOB)",
        "CREATE TABLE direct_piece(segment_id TEXT REFERENCES direct_segment(id),chunk_id TEXT REFERENCES direct_chunk(id),payload_offset INTEGER,frame_count INTEGER,PRIMARY KEY(segment_id,chunk_id))",
        "CREATE TABLE direct_chunk_payload_ref(chunk_id TEXT PRIMARY KEY REFERENCES direct_chunk(id),version INTEGER,parts JSON,archived_at REAL)",
    ]
    with target.begin() as db:
        for statement in statements:
            db.execute(text(statement))
        db.execute(text("INSERT INTO direct_device VALUES(:id,:org)"), dict(id=SENSOR, org=ORG))
        db.execute(
            text("INSERT INTO direct_session VALUES(:id,:session,:config,'DIRECT')"),
            dict(
                id=SENSOR,
                session="00000000-0000-0000-0000-000000000099",
                config=json.dumps(
                    {
                        "protocol_version": 1,
                        "device_id": SENSOR,
                        "session_id": "00000000-0000-0000-0000-000000000099",
                        "session_start_us": epoch_us(AT),
                        "sample_rate": 380,
                        "channels": 1,
                        "sample_bits": 16,
                        "channel_labels": ["leaf0"],
                        "calibration_version": "cal-v1",
                        "firmware_version": "fw-v1",
                    }
                ),
            ),
        )
        db.execute(text("INSERT INTO direct_segment VALUES('segment',:id)"), dict(id=SENSOR))


def direct_body(identity):
    return dict(
        row=dict(id=identity, device_id=SENSOR, payload=b"\x00\xff"),
        context=dict(
            pieces=[dict(segment_id="segment", chunk_id=identity, payload_offset=0, frame_count=1)],
            payload_reference=dict(chunk_id=identity, version=1, parts=[], archived_at=1.0),
        ),
    )


def test_direct_children_across_batches_real_fk_and_replay(target, tmp_path):
    direct_target(target)
    head, sha, descriptors = plan(
        tmp_path,
        [[[direct_body("one")]], [[direct_body("two")]]],
        source="direct",
        dataset="direct_chunk",
    )
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    final = apply(head, sha, session, descriptors[1])
    assert final["chain_loaded"] and final["committed_rows"] == 2
    assert apply(head, sha, session, descriptors[0])["replayed"]
    with target.connect() as db:
        for table in ("direct_chunk", "direct_piece", "direct_chunk_payload_ref"):
            assert db.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 2
        assert db.execute(text("SELECT count(*) FROM direct_segment")).scalar_one() == 1


def test_direct_child_conflict_rolls_back_current_batch_only(target, tmp_path):
    direct_target(target)
    invalid = direct_body("two")
    invalid["context"]["pieces"][0]["segment_id"] = "missing"
    head, sha, descriptors = plan(
        tmp_path, [[[direct_body("one")]], [[invalid]]], source="direct", dataset="direct_chunk"
    )
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    with pytest.raises(IntegrityError):
        apply(head, sha, session, descriptors[1])
    with target.connect() as db:
        for table in ("direct_chunk", "direct_piece", "direct_chunk_payload_ref"):
            assert db.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 1
        assert db.execute(text("SELECT rows FROM greenmind_restore_session")).scalar_one() == 1


def test_different_source_domains_cannot_share_one_isolated_restore_database(target, tmp_path):
    direct_target(target)
    gateway, direct = tmp_path / "gatewayplan", tmp_path / "directplan"
    gateway.mkdir()
    direct.mkdir()
    first, first_sha, _ = plan(gateway, [[[log("one")]]])
    second, second_sha, _ = plan(
        direct, [[[direct_body("one")]]], source="direct", dataset="direct_chunk"
    )
    begin(first, first_sha)
    with pytest.raises(ArchiveBlocked, match="Restore application universe changed"):
        begin(second, second_sha)


def test_current_batch_byte_budget_is_checked_before_any_target_access(
    target, tmp_path, monkeypatch
):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    monkeypatch.setattr(parquet, "MAX_FILE_BYTES", 1)
    monkeypatch.setattr(rs, "connection", lambda *a: pytest.fail("Must not connect"))
    with pytest.raises(ArchiveBlocked, match="budget"):
        apply(head, sha, session, descriptors[0])


def test_private_master_values_never_enter_journals_or_results(target, tmp_path):
    secret = "SYNTHETIC-PRIVATE-KEYHASH-MUST-STAY-OUT"
    with target.begin() as db:
        db.execute(text("ALTER TABLE gateway ADD COLUMN key_hash TEXT"))
        db.execute(text("UPDATE gateway SET key_hash=:private"), dict(private=secret))
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    result = apply(head, sha, session, descriptors[0])
    assert secret not in json.dumps(result)
    with target.connect() as db:
        for table in (
            "greenmind_restore_session",
            "greenmind_restore_batch",
            "greenmind_restore_page",
        ):
            assert secret not in str(db.execute(text("SELECT * FROM " + table)).fetchall())


def test_later_batch_trigger_mutation_is_now_blocked_atomically(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("A")]], [[log("B")]]])
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER change_committed AFTER INSERT ON ingest_log WHEN NEW.measurement_id='B' BEGIN UPDATE ingest_log SET payload=x'00' WHERE measurement_id='A'; END"
            )
        )
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    before = state(target)
    with pytest.raises(ArchiveBlocked, match="unexpected mutations"):
        apply(head, sha, session, descriptors[1])
    assert state(target) == before
    assert apply(head, sha, session, descriptors[0])["replayed"]
