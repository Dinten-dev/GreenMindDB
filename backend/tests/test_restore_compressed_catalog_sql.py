"""Execute catalog joins against native b227 metadata; SQLite SQL adapter only.

Native evidence: outputs/greenmind-staging-compressed-target-diagnostic-20261101/
Native.stdout, Parent actual b973f7/own wait0, target63761. No source payloads.
"""

import pytest
from sqlalchemy import create_engine, text

from app.tiered_archive.restore_compressed import CATALOG


@pytest.fixture
def catalog():
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.exec_driver_sql("ATTACH DATABASE ':memory:' AS pg_catalog")
        db.exec_driver_sql("ATTACH DATABASE ':memory:' AS _timescaledb_catalog")
        for sql in [
            "CREATE TABLE pg_catalog.pg_namespace(oid INTEGER,nspname TEXT)",
            "CREATE TABLE pg_catalog.pg_class(oid INTEGER,relnamespace INTEGER,relname TEXT,relkind TEXT,relpersistence TEXT,relrowsecurity BOOL,relforcerowsecurity BOOL)",
            "CREATE TABLE pg_catalog.pg_inherits(inhrelid INTEGER,inhparent INTEGER)",
            "CREATE TABLE _timescaledb_catalog.hypertable(id INTEGER,schema_name TEXT,table_name TEXT,compressed_hypertable_id INTEGER)",
            "CREATE TABLE _timescaledb_catalog.chunk(id INTEGER,hypertable_id INTEGER,schema_name TEXT,table_name TEXT,compressed_chunk_id INTEGER,dropped BOOL)",
            "INSERT INTO pg_catalog.pg_namespace VALUES(11,'public'),(12,'_timescaledb_internal')",
            "INSERT INTO pg_catalog.pg_class VALUES(64575,11,'sensor_reading','r','p',0,0),(64572,12,'_compressed_hypertable_4','r','p',0,0),(64578,12,'_hyper_3_5_chunk','r','p',0,0),(64586,12,'compress_hyper_4_7_chunk','r','p',0,0),(64582,12,'_hyper_3_8_chunk','r','p',0,0)",
            "INSERT INTO _timescaledb_catalog.hypertable VALUES(3,'public','sensor_reading',4),(4,'_timescaledb_internal','_compressed_hypertable_4',NULL)",
            "INSERT INTO _timescaledb_catalog.chunk VALUES(5,3,'_timescaledb_internal','_hyper_3_5_chunk',7,0),(7,4,'_timescaledb_internal','compress_hyper_4_7_chunk',NULL,0),(8,3,'_timescaledb_internal','_hyper_3_8_chunk',NULL,0)",
            "INSERT INTO pg_catalog.pg_inherits VALUES(64578,64575),(64582,64575)",
        ]:
            db.exec_driver_sql(sql)
        yield db
    engine.dispose()


def rows(db):
    # Only PG's bigint/text/integer cast syntax is removed; actual product joins/predicates
    # run unchanged over these strictly typed small synthetic metadata fields.
    return [
        dict(r)
        for r in db.execute(
            text(CATALOG.replace("::bigint", "").replace("::text", "").replace("::integer", "")),
            {"limit": 321},
        ).mappings()
    ]


def test_native_standalone_shape_is_complete_without_inheritance(catalog):
    result = rows(catalog)
    assert [r["oid"] for r in result] == [64572, 64586]
    root, chunk = result
    assert root["kind"] == "root" and chunk["kind"] == "chunk"
    assert chunk["public_table"] == "sensor_reading" and chunk["public_oid"] == 64575
    assert chunk["hypertable_id"] == 3 and chunk["compressed_hypertable_id"] == 4
    assert chunk["original_chunk_id"] == 5 and chunk["chunk_id"] == 7 and chunk["root_oid"] == 64572
    assert (
        catalog.exec_driver_sql(
            "SELECT count(*) FROM pg_catalog.pg_inherits WHERE inhrelid=64586"
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE _timescaledb_catalog.chunk SET hypertable_id=9 WHERE id=7",
        "UPDATE _timescaledb_catalog.chunk SET compressed_chunk_id=8 WHERE id=5",
        "UPDATE _timescaledb_catalog.chunk SET dropped=1 WHERE id=7",
        "UPDATE pg_catalog.pg_class SET relrowsecurity=1 WHERE oid=64586",
        "UPDATE pg_catalog.pg_class SET relforcerowsecurity=1 WHERE oid=64586",
        "INSERT INTO pg_catalog.pg_inherits VALUES(64586,64575)",
        "INSERT INTO pg_catalog.pg_inherits VALUES(64586,64572)",
    ],
)
def test_wrong_link_rls_or_added_physical_parent_cannot_enter_plan(catalog, mutation):
    catalog.exec_driver_sql(mutation)
    assert [r["kind"] for r in rows(catalog)] == ["root"]


def test_changed_physical_oid_is_not_hidden_by_unchanged_names(catalog):
    before = rows(catalog)
    catalog.exec_driver_sql("UPDATE pg_catalog.pg_class SET oid=70000 WHERE oid=64586")
    after = rows(catalog)
    assert [r["name"] for r in before] == [r["name"] for r in after]
    assert before != after and after[1]["oid"] == 70000
