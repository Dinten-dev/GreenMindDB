"""Small synthetic bounds tests; no compressed JSON/native shared-server probe."""

import pytest
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects import postgresql

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_baseline as baseline
from app.tiered_archive import restore_session as rs
from app.tiered_archive import restore_sizes as sizes
from app.tiered_archive import restore_universe as universe
from tests.restore_bounds_sqlite import install_bounds_adapter


@pytest.fixture
def db_fixture(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "bounded.sqlite"))
    install_bounds_adapter(monkeypatch)
    yield engine
    engine.dispose()


def test_large_text_is_rejected_before_value_fetch_or_encode(db_fixture):
    table = Table(
        "bounded_master",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("message", Text),
    )
    table.create(db_fixture)
    with db_fixture.begin() as db:
        db.execute(table.insert().values(id=1, message="x" * (sizes.MAX_FIELD_BYTES + 1)))
        baseline.ensure(db, {table.name: table}, dict(master_epoch=0), rs.schema_fingerprint)

    @event.listens_for(db_fixture, "before_cursor_execute")
    def forbid_value(conn, cursor, statement, parameters, context, many):
        assert not statement.lstrip().startswith("SELECT bounded_master.id, bounded_master.message")

    with pytest.raises(ArchiveBlocked, match="raw byte size"), db_fixture.begin() as db:
        baseline.advance(db, {table.name: table}, dict(master_epoch=0), checkpoint=lambda: None)
    with db_fixture.connect() as db:
        assert db.execute(text("SELECT rows FROM greenmind_restore_baseline")).scalar_one() == 0


def test_nonnull_json_is_held_without_serializing_or_transferring_it(db_fixture):
    table = Table(
        "bounded_json", MetaData(), Column("id", Integer, primary_key=True), Column("payload", JSON)
    )
    table.create(db_fixture)
    with db_fixture.begin() as db:
        db.execute(table.insert().values(id=1, payload={"number": "unproved"}))
        baseline.ensure(db, {table.name: table}, dict(master_epoch=0), rs.schema_fingerprint)

    @event.listens_for(db_fixture, "before_cursor_execute")
    def forbid_value(conn, cursor, statement, parameters, context, many):
        assert not statement.lstrip().startswith("SELECT bounded_json.id, bounded_json.payload")
        assert "row_to_json" not in statement and "json_extract" not in statement

    with pytest.raises(ArchiveBlocked, match="type or raw"), db_fixture.begin() as db:
        baseline.advance(db, {table.name: table}, dict(master_epoch=0), checkpoint=lambda: None)


def test_admitted_unicode_text_fetches_one_row_and_resumes(db_fixture):
    table = Table(
        "unicode_master",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("message", Text),
    )
    table.create(db_fixture)
    with db_fixture.begin() as db:
        db.execute(table.insert(), [dict(id=i, message="é" * 100) for i in range(4)])
        baseline.ensure(db, {table.name: table}, dict(master_epoch=0), rs.schema_fingerprint)
    reads = []

    @event.listens_for(db_fixture, "before_cursor_execute")
    def one_value(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith("SELECT unicode_master.id, unicode_master.message"):
            reads.append(statement)
            assert "LIMIT" in statement
            assert parameters[-2] == 1

    rows = 0
    for _ in range(5):
        with db_fixture.begin() as db:
            result = baseline.advance(
                db,
                {table.name: table},
                dict(master_epoch=0),
                checkpoint=lambda: None,
                maximum_rows=1,
            )
            rows += result["rows"]
            if baseline.anchor(db, {table.name: table}, dict(master_epoch=0)) != "0" * 64:
                break
    assert rows == 4 and len(reads) == 4


def test_raw_contract_never_uses_pg_column_size_or_json_conversion():
    table = Table(
        "m",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("message", Text),
        Column("payload", postgresql.JSONB),
    )
    assert sizes.column_contract(table.c.payload) is None
    sql = str(sizes.column_contract(table.c.message).compile(dialect=postgresql.dialect()))
    assert "pg_catalog.octet_length" in sql
    assert "pg_column_size" not in sql and "::text" not in sql
    assert sizes.key_contract([Column("unknown", Text)]) is False


class Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def all(self):
        return self.value


class Inventory:
    class Dialect:
        name = "postgresql"

    dialect = Dialect()

    def __init__(self, metadata, rows):
        self.metadata, self.rows, self.sql = metadata, rows, []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        return Rows(self.metadata if "e.extversion" in sql else self.rows)


def test_internal_user_table_remains_visible_without_namespace_exemption():
    db = Inventory(
        dict(oid=1, extversion="2.17.2", catalogs=2),
        [
            dict(schema="public", name="ingest_log"),
            dict(schema="_timescaledb_internal", name="application_secret"),
        ],
    )
    result = universe.relation_inventory(db)
    assert ("_timescaledb_internal", "application_secret") in result
    query = db.sql[-1]
    assert "n.nspname IN ('_timescaledb_internal'" not in query
    assert "_timescaledb_catalog.chunk" in query and "pg_catalog.pg_inherits" in query
    assert "h.schema_name='public'" in query
    assert "d.refclassid='pg_catalog.pg_extension'" in query


def test_unproved_timescale_catalog_is_not_exempted():
    db = Inventory(dict(oid=1, extversion="2.17.2", catalogs=1), [])
    with pytest.raises(ArchiveBlocked, match="provenance"):
        universe.relation_inventory(db)
    assert len(db.sql) == 1


def test_unknown_encoding_fails_before_any_master_read(monkeypatch):
    class Result:
        def one(self):
            return ("UTF8", "LATIN1")

    class DB:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

        def exec_driver_sql(self, sql):
            return Result()

    with pytest.raises(ArchiveBlocked, match="encoding"):
        sizes.check_encoding(DB())
