"""Synthetic catalog and real SQLite transaction tests, not native TS acceptance."""

from copy import deepcopy

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_compressed as compressed
from app.tiered_archive import restore_universe as universe
from tests.test_tiered_restore_session import apply, begin, log, plan, state
from tests.test_tiered_restore_session import target as restore_target

target = restore_target


def storage():
    root = dict(
        schema="_timescaledb_internal",
        name="_compressed_hypertable_4",
        oid=40,
        kind="root",
        public_table="ingest_log",
        public_oid=10,
        hypertable_id=1,
        compressed_hypertable_id=4,
        root_oid=40,
        chunk_id=None,
        original_chunk_id=None,
        relation_sha256="a" * 64,
    )
    return [
        root,
        dict(
            root,
            name="compress_hyper_4_7_chunk",
            oid=70,
            kind="chunk",
            chunk_id=7,
            original_chunk_id=5,
        ),
    ]


def head():
    return dict(
        source="gateway",
        dataset="ingest_log",
        universe=dict(
            version=2,
            tables={"ingest_log": "data", "gateway": "master", "zone": "master"},
            empty_compressed_storage=storage(),
        ),
    )


@pytest.mark.parametrize("change", ["master", "unlinked", "dup", "oid", "sha", "extra", "unsorted"])
def test_exact_identity_contract_rejects_unknown_or_unlinked_storage(change):
    value = head()
    entries = value["universe"]["empty_compressed_storage"]
    if change == "master":
        entries[0]["public_table"] = "gateway"
    elif change == "unlinked":
        entries[1]["root_oid"] = 99
    elif change == "dup":
        entries[1]["oid"] = 40
    elif change == "oid":
        entries[0]["oid"] = True
    elif change == "sha":
        entries[0]["relation_sha256"] = "no"
    elif change == "extra":
        entries[0]["unknown"] = True
    else:
        entries.reverse()
    with pytest.raises(ArchiveBlocked):
        universe.validate(value)


def test_v1_meaning_unchanged_and_v2_explicit():
    value = head()
    universe.validate(value)
    value["universe"]["version"] = 1
    with pytest.raises(ArchiveBlocked):
        universe.validate(value)
    del value["universe"]["empty_compressed_storage"]
    universe.validate(value)
    assert compressed.check(None, value) == ()


class Result:
    def __init__(self, value=None):
        self.value = value

    def first(self):
        return self.value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return iter(self.value)


class Catalog:
    class Dialect:
        name = "postgresql"

    dialect = Dialect()

    def __init__(self, rows, *, isolation="read committed", payload=False):
        self.rows, self.isolation, self.payload, self.queries = rows, isolation, payload, []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        if sql.startswith("SHOW"):
            return Result(self.isolation)
        if sql.startswith("WITH roots"):
            return Result(self.rows)
        return Result((1,) if self.payload and sql.startswith("SELECT 1 FROM ONLY") else None)


def test_native_catalog_sql_uses_exact_links_and_provenance(monkeypatch):
    rows = [{k: v for k, v in row.items() if k != "relation_sha256"} for row in storage()]
    db = Catalog(rows)
    monkeypatch.setattr(universe, "chunk_exemption", lambda db: "PROVED")
    monkeypatch.setattr("app.tiered_archive.restore_fence._relation_pin", lambda *args: "a" * 64)
    assert compressed.inventory(db) == storage()
    sql, params = db.queries[-1]
    assert params == {"limit": 321}
    assert "cc.id=ch.compressed_chunk_id" in sql and "z.id=h.compressed_hypertable_id" in sql
    assert "i.inhrelid=c.oid)" in sql and "NOT c.relrowsecurity" in sql
    assert "JOIN pg_catalog.pg_inherits i" not in sql
    assert "NOT r.relforcerowsecurity" in sql and "NOT ch.dropped AND NOT cc.dropped" in sql
    compiled = text(compressed.CATALOG).compile(dialect=postgresql.dialect())
    assert compiled.params == {"limit": None}
    db.isolation = "repeatable read"
    with pytest.raises(ArchiveBlocked, match="READ COMMITTED"):
        compressed.inventory(db)


def test_metadata_overflow_blocks_before_relation_pins(monkeypatch):
    monkeypatch.setattr(universe, "chunk_exemption", lambda db: "PROVED")
    db = Catalog([{}] * 321)
    monkeypatch.setattr(
        "app.tiered_archive.restore_fence._relation_pin",
        lambda *args: pytest.fail("must not read pins"),
    )
    with pytest.raises(ArchiveBlocked, match="budget"):
        compressed.inventory(db)


@pytest.mark.parametrize("when", ["before", "while_locking"])
def test_catalog_change_blocks_even_when_storage_empty(monkeypatch, when):
    count = [0]

    def current(db):
        count[0] += 1
        result = deepcopy(storage())
        if when == "before" or count[0] == 2:
            result[1]["oid"] += 1
        return result

    monkeypatch.setattr(compressed, "inventory", current)
    with pytest.raises(ArchiveBlocked, match="changed"):
        compressed.check(Catalog([]), head())


def test_locks_all_physical_relations_before_empty_read(monkeypatch):
    monkeypatch.setattr(compressed, "inventory", lambda db: storage())
    db = Catalog([])
    assert compressed.check(db, head()) == (40, 70)
    sql = [entry[0] for entry in db.queries]
    assert all(s.startswith("LOCK TABLE ONLY") and s.endswith("SHARE MODE NOWAIT") for s in sql[:2])
    assert all(s.startswith("SELECT 1 FROM ONLY") and s.endswith("LIMIT 1") for s in sql[2:])
    db.payload = True
    with pytest.raises(ArchiveBlocked, match="physically empty"):
        compressed.check(db, head())


@pytest.fixture
def private_storage(target, monkeypatch):
    # Explicit TEST adapter: real SQLite row effects/rollback, catalog and PG
    # lock syntax are simulated. This is not a PostgreSQL/Timescale proof.
    original_inventory = universe.relation_inventory
    monkeypatch.setattr(
        universe,
        "relation_inventory",
        lambda db, empty_oids=(): {
            pair
            for pair in original_inventory(db)
            if pair[1] not in {row["name"] for row in storage()}
        },
    )
    monkeypatch.setattr(compressed, "inventory", lambda db: storage())
    with target.begin() as db:
        for row in storage():
            db.execute(text(f'CREATE TABLE "{row["name"]}"(payload TEXT)'))

    @event.listens_for(target, "before_cursor_execute", retval=True)
    def physical_sql(conn, cursor, statement, params, context, many):
        return statement.replace("FROM ONLY ", "FROM ").replace(
            '"_timescaledb_internal".', ""
        ).replace("_timescaledb_internal.", ""), params

    return target


def make_plan(tmp_path, *, freeze=False):
    return plan(
        tmp_path, [[[log("one")]]], changes=dict(universe=head()["universe"]), freeze=freeze
    )


def test_empty_storage_load_and_idempotent_replay(private_storage, tmp_path):
    value, sha, batches = make_plan(tmp_path)
    sid = begin(value, sha)
    assert apply(value, sha, sid, batches[0])["chain_loaded"]
    assert apply(value, sha, sid, batches[0])["replayed"]
    assert state(private_storage)["rows"] == 1


def test_same_transaction_private_trigger_payload_rolls_back_before_commit(
    private_storage, tmp_path
):
    value, sha, batches = make_plan(tmp_path)
    with private_storage.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER private_effect AFTER INSERT ON ingest_log "
                "BEGIN INSERT INTO compress_hyper_4_7_chunk VALUES('unexpected'); END"
            )
        )
    sid = begin(value, sha)
    before = state(private_storage)
    with pytest.raises(ArchiveBlocked, match="physically empty"):
        apply(value, sha, sid, batches[0])
    assert state(private_storage) == before
    with private_storage.connect() as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM _timescaledb_internal.compress_hyper_4_7_chunk")
            ).scalar_one()
            == 0
        )


def test_between_calls_private_payload_blocks_final_audit(private_storage, tmp_path, monkeypatch):
    import os

    from app.tiered_archive import restore_inputs as inputs
    from tests.test_tiered_restore_final_audit import audit, start

    monkeypatch.setattr(inputs, "ROOT_UID", os.geteuid())
    value, sha, batches = make_plan(tmp_path, freeze=True)
    sid = begin(value, sha)
    apply(value, sha, sid, batches[0])
    start(value, sha, sid)
    with private_storage.begin() as db:
        db.execute(
            text("INSERT INTO _timescaledb_internal._compressed_hypertable_4 VALUES('unexpected')")
        )
    with pytest.raises(ArchiveBlocked, match="physically empty"):
        audit(value, sha, sid, batches[0])
    assert state(private_storage)["sessions"][0]["state"] == "auditing"


def test_empty_storage_completes_typed_final_audit(private_storage, tmp_path, monkeypatch):
    import os

    from app.tiered_archive import restore_inputs as inputs
    from tests.test_tiered_restore_final_audit import audit, start

    monkeypatch.setattr(inputs, "ROOT_UID", os.geteuid())
    value, sha, batches = make_plan(tmp_path, freeze=True)
    sid = begin(value, sha)
    apply(value, sha, sid, batches[0])
    start(value, sha, sid)
    result = audit(value, sha, sid, batches[0])
    assert result["phase"] == "verified"
    assert result["all_registered_sessions_verified"]
