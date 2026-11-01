"""Real local row-trigger accounting; PostgreSQL DDL is not claimed executed."""

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import psycopg2
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import TextClause

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_fence as fence
from tests.restore_fence_sqlite import install_sqlite_adapter, target_sha256

DOMAIN = {"environment": "production", "source": "gateway", "schema_master_sha256": "a" * 64}


@pytest.fixture
def target(monkeypatch):
    install_sqlite_adapter(monkeypatch)
    engine = create_engine("sqlite://")
    metadata = MetaData()
    data = Table(
        "samples",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("organization", String, nullable=False),
        Column("value", Integer),
    )
    other = Table(
        "other_samples", metadata, Column("id", Integer, primary_key=True), Column("value", Integer)
    )
    master = Table(
        "devices", metadata, Column("id", Integer, primary_key=True), Column("name", String)
    )
    with engine.begin() as db:
        metadata.create_all(db)
        db.execute(master.insert(), {"id": 1, "name": "original"})
    try:
        with engine.begin() as db:
            yield db, {"samples": data, "other_samples": other, "devices": master}
    finally:
        engine.dispose()


def begin(db, tables):
    return fence.ensure(db, tables, {"devices"}, target_sha256(db), DOMAIN)


def test_real_insert_accept_and_freeze_preserve_master_and_second_organization(target):
    db, tables = target
    before = begin(db, tables)
    assert before == {
        "epoch": 0,
        "master_epoch": 0,
        "phase": "loading",
        "audit_epoch": None,
        "rows": {"other_samples": 0, "samples": 0},
    }
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
    first = fence.accept(db, before, {"samples": 1})
    assert first["epoch"] == 1 and first["rows"]["samples"] == 1
    assert begin(db, tables) == first
    db.execute(tables["samples"].insert(), {"id": 2, "organization": "second", "value": 20})
    second = fence.accept(db, first, {"samples": 1})
    assert second["epoch"] == 2 and second["rows"]["samples"] == 2
    frozen = fence.freeze(db)
    assert frozen["audit_epoch"] == 2 and frozen["phase"] == "auditing"
    assert fence.check_audit(db, 2) == frozen
    assert fence.freeze(db) == frozen
    assert db.execute(select(tables["devices"].c.name)).scalar_one() == "original"


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_unexpected_update_delete_and_master_mutation_block_accept(target, operation):
    db, tables = target
    before = begin(db, tables)
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
    accepted = fence.accept(db, before, {"samples": 1})
    if operation == "UPDATE":
        db.execute(tables["samples"].update().values(value=11))
    else:
        db.execute(tables["samples"].delete())
    with pytest.raises(ArchiveBlocked, match="unexpected mutations"):
        fence.accept(db, accepted, {})
    with pytest.raises(ArchiveBlocked, match="Unexpected restore database mutation"):
        fence.check(db)


def test_master_update_is_detected_without_scanning_master_rows(target):
    db, tables = target
    before = begin(db, tables)
    db.execute(tables["devices"].update().values(name="changed"))
    with pytest.raises(ArchiveBlocked):
        fence.accept(db, before, {})


def test_external_insert_between_batches_cannot_be_adopted(target):
    db, tables = target
    before = begin(db, tables)
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "external", "value": 10})
    with pytest.raises(ArchiveBlocked):
        fence.check(db)
    with pytest.raises(ArchiveBlocked):
        fence.accept(db, before, {})


def test_counter_and_accept_roll_back_with_the_same_batch_transaction(target):
    db, tables = target
    before = begin(db, tables)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        with db.begin_nested():
            db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
            assert fence.accept(db, before, {"samples": 1})["epoch"] == 1
            raise RuntimeError("synthetic interruption")
    assert fence.check(db) == before
    assert db.execute(select(tables["samples"].c.id)).first() is None


def test_actual_row_trigger_rejects_non_origin_without_changing_epoch(target):
    db, tables = target
    before = begin(db, tables)
    db.info["fence_origin"] = "replica"
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
    db.info["fence_origin"] = "origin"
    assert fence.check(db) == before


def test_new_data_must_be_empty_master_may_be_populated(target):
    db, tables = target
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "prior", "value": 10})
    with pytest.raises(ArchiveBlocked, match="must be empty"):
        begin(db, tables)


@pytest.mark.parametrize(
    "tamper", ["drop_trigger", "change_function", "registry", "table_column", "origin"]
)
def test_catalog_or_origin_tampering_never_passes(target, tamper):
    db, tables = target
    begin(db, tables)
    if tamper == "drop_trigger":
        db.exec_driver_sql("DROP TRIGGER greenmind_restore_row_v1_samples_update")
    elif tamper == "change_function":
        db.exec_driver_sql("DROP VIEW greenmind_restore_track_v1")
        db.exec_driver_sql("CREATE VIEW greenmind_restore_track_v1 AS SELECT 'changed' AS body")
    elif tamper == "registry":
        db.execute(text("DELETE FROM greenmind_restore_fence_table WHERE table_name='samples'"))
    elif tamper == "table_column":
        db.exec_driver_sql("ALTER TABLE samples ADD COLUMN injected TEXT")
    else:
        db.info["fence_origin"] = "replica"
    with pytest.raises(ArchiveBlocked):
        fence.check(db)


@pytest.mark.parametrize("mutation", ["insert", "update", "delete"])
def test_frozen_fence_aborts_actual_table_mutation(target, mutation):
    db, tables = target
    before = begin(db, tables)
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
    fence.accept(db, before, {"samples": 1})
    frozen = fence.freeze(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            if mutation == "insert":
                db.execute(
                    tables["samples"].insert(), {"id": 2, "organization": "second", "value": 20}
                )
            elif mutation == "update":
                db.execute(tables["samples"].update().values(value=99))
            else:
                db.execute(tables["samples"].delete())
    assert fence.check_audit(db, frozen["epoch"]) == frozen
    with pytest.raises(ArchiveBlocked):
        begin(db, tables)
    with pytest.raises(ArchiveBlocked):
        fence.accept(db, frozen, {})


def test_wrong_target_domain_or_master_role_cannot_reuse_fence(target):
    db, tables = target
    begin(db, tables)
    with pytest.raises(ArchiveBlocked):
        fence.ensure(db, tables, {"devices"}, "0" * 64, DOMAIN)
    with pytest.raises(ArchiveBlocked):
        fence.ensure(db, tables, {"devices"}, target_sha256(db), {**DOMAIN, "source": "direct"})
    with pytest.raises(ArchiveBlocked):
        fence.ensure(db, tables, set(), target_sha256(db), DOMAIN)


def test_source_trigger_extra_update_is_counted_and_rejected(target):
    db, tables = target
    db.exec_driver_sql("""CREATE TRIGGER original_side_effect AFTER INSERT ON samples
      BEGIN UPDATE devices SET name='side-effect' WHERE id=1; END""")
    before = begin(db, tables)
    db.execute(tables["samples"].insert(), {"id": 1, "organization": "first", "value": 10})
    with pytest.raises(ArchiveBlocked):
        fence.accept(db, before, {"samples": 1})


def test_product_path_has_no_sqlite_fallback(monkeypatch):
    # Read the real production guard rather than the explicit fixture adapter.
    from importlib import util

    path = fence.__file__
    spec = util.spec_from_file_location("app.tiered_archive._fresh_fence_guard_test", path)
    fresh = util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as db:
            with pytest.raises(ArchiveBlocked, match="requires PostgreSQL"):
                fresh.check(db)
    finally:
        engine.dispose()


def _trigger(logical="samples", kind=29, enabled="A"):
    return {
        "enabled": enabled,
        "kind": kind,
        "function": True,
        "nargs": 1,
        "args": logical.encode() + b"\x00",
        "unconditional": True,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", "D"),
        ("enabled", "O"),
        ("function", False),
        ("nargs", 0),
        ("args", b"other\x00"),
        ("kind", 23),
        ("unconditional", False),
    ],
)
def test_physical_trigger_cannot_claim_coverage_with_changed_definition(field, value):
    row = _trigger()
    assert fence._valid_trigger(row, "samples", 29)
    row[field] = value
    assert not fence._valid_trigger(row, "samples", 29)


def test_new_child_requires_same_transaction_and_inherited_row_fence(monkeypatch):
    child = {
        "oid": 7,
        "name": "_hyper_chunk",
        "schema": "_timescaledb_internal",
        "own_transaction": False,
    }
    monkeypatch.setattr(fence, "_descendants", lambda *_: [child])
    effects = []
    monkeypatch.setattr(fence, "_install_triggers", lambda *args, **kw: effects.append((args, kw)))
    monkeypatch.setattr(
        fence,
        "_trigger",
        lambda _db, _name, _schema, which: _trigger(kind=29 if which == fence.ROW_TRIGGER else 32),
    )
    monkeypatch.setattr(fence, "_relation_pin", lambda *_: "a" * 64)
    with pytest.raises(ArchiveBlocked, match="external restore child"):
        fence._coverage(None, "samples", {}, prepare_new=True)
    assert effects == []
    child["own_transaction"] = True
    assert fence._coverage(None, "samples", {}, prepare_new=True) == {"7": "a" * 64}
    assert effects == [
        ((None, "_hyper_chunk", "samples", "_timescaledb_internal"), {"new_chunk": True})
    ]
    effects.clear()
    assert fence._coverage(None, "samples", {"7": "a" * 64}, prepare_new=True) == {"7": "a" * 64}
    assert effects == []  # Existing children's missing/changed fences are never repaired.


def test_new_child_with_missing_row_trigger_never_gets_silent_repair(monkeypatch):
    statements = []
    db = type("DB", (), {"exec_driver_sql": lambda _self, sql: statements.append(sql)})()
    monkeypatch.setattr(fence, "_trigger", lambda *_: None)
    with pytest.raises(ArchiveBlocked, match="lacks inherited row fence"):
        fence._install_triggers(
            db, "_hyper_chunk", "samples", "_timescaledb_internal", new_chunk=True
        )
    assert statements == []


@pytest.mark.parametrize("after", [{}, {"10": "d" * 64}, {"10": "c" * 64, "11": "e" * 64}])
def test_changed_or_unaccepted_child_identity_blocks_regular_check(target, monkeypatch, after):
    db, tables = target
    monkeypatch.setattr(fence, "_coverage", lambda *_args, **_kw: {"10": "c" * 64})
    begin(db, tables)
    monkeypatch.setattr(fence, "_coverage", lambda *_args, **_kw: after)
    with pytest.raises(ArchiveBlocked, match="physical child identity changed"):
        fence.check(db)


def test_pg_trigger_source_binds_logical_name_origin_and_truncate_without_row_payload():
    statements = []
    db = type("DB", (), {"execute": lambda _self, sql: statements.append(str(sql))})()
    fence._install_function(db)
    body = statements[0]
    assert "logical_name := TG_ARGV[0]" in body
    assert "pg_catalog.pg_inherits" in body
    assert "TG_OP='TRUNCATE'" in body
    assert "session_replication_role" in body
    assert "NEW." not in body and "OLD." not in body


def test_actual_pg_ddl_dialect_rendering_escapes_literal_percent_for_psycopg2():
    statements = []

    class DriverBoundary:
        def execute(self, statement):
            assert isinstance(statement, TextClause)
            compiled = statement.compile(dialect=psycopg2.dialect())
            assert compiled.params == {}  # PL/pgSQL := is not an application bind.
            wire = str(compiled)
            statements.append((str(statement), wire, wire % compiled.params))

    fence._install_function(DriverBoundary())
    original, wire, driver_rendered = statements[0]
    assert "format('public.%I'" in original
    assert "format('public.%%I'" in wire
    assert driver_rendered == original
    with pytest.raises((TypeError, ValueError)):
        original % {}  # The removed raw-driver path bypassed dialect escaping.
    notice = text("DO $$ BEGIN RAISE NOTICE 'progress %',1; END $$")
    compiled = notice.compile(dialect=psycopg2.dialect())
    assert "progress %%" in str(compiled)
    assert str(compiled) % compiled.params == str(notice)
