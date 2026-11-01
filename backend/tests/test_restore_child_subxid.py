"""Product CASE in local adapted SQL; no native PostgreSQL visibility claim."""

from types import SimpleNamespace

import duckdb
import pytest
from sqlalchemy.dialects import postgresql

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_fence as fence


def _sql_predicate(top, raw_xmin, status):
    # Only PG type/function spelling is adapted. MVCC needs native acceptance.
    expression = fence._OWN_INSERTION.replace(
        "pg_catalog.pg_xact_status(c.xmin::text::pg_catalog.xid8)",
        "probe_status(c.xmin::text)",
    ).replace("::numeric", "::DECIMAL(30,0)")
    calls = []
    with duckdb.connect(":memory:") as db:

        def status_function(value):
            calls.append(value)
            if isinstance(status, Exception):
                raise status
            return status

        db.create_function(
            "probe_status",
            status_function,
            ["VARCHAR"],
            "VARCHAR",
            null_handling="special",
            side_effects=True,
        )
        result = db.execute(
            "WITH authority AS (SELECT CAST(? AS DECIMAL(30,0)) AS top_id), "
            "c AS (SELECT CAST(? AS VARCHAR) AS xmin) SELECT "
            + expression
            + " FROM authority CROSS JOIN c",
            [str(top), str(raw_xmin)],
        ).fetchone()[0]
    return result, calls


@pytest.mark.parametrize(
    "top,xmin,status,expected,called",
    [
        (100, 100, "in progress", True, True),
        (100, 101, "in progress", True, True),
        (100, 106, "committed", False, True),
        (100, 106, "aborted", False, True),
        (100, 106, None, False, True),
        (100, 99, "in progress", False, False),
        (3, 2, "in progress", False, False),
        (0, 106, "in progress", False, False),
        (2**32 - 2, 2**32 - 1, "in progress", True, True),
        (2**32 - 2, 3, "in progress", False, False),
        (2**32 + 100, 101, "in progress", False, False),
        (2**63 + 100, 101, "in progress", False, False),
        (2**64 - 1, 2**32 - 1, "in progress", False, False),
    ],
)
def test_product_sql_parent_subxid_committed_and_wrap_admission(
    top, xmin, status, expected, called
):
    result, calls = _sql_predicate(top, xmin, status)
    assert result is expected
    assert bool(calls) is called


def test_unknown_or_future_status_error_never_becomes_owned():
    with pytest.raises(duckdb.InvalidInputException):
        _sql_predicate(100, 999, RuntimeError("synthetic_status_unavailable"))


def test_exact_postgresql_sql_path_remains_bounded_and_qualified():
    statements = []

    class DB:
        def execute(self, statement, params):
            statements.append((statement, params))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    assert fence._descendants(DB(), "synthetic_parent") == []
    statement, params = statements[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert params == {"name": "synthetic_parent"}
    assert fence._OWN_INSERTION in str(statement)
    assert "pg_catalog.pg_current_xact_id()" in compiled
    assert "pg_catalog.pg_xact_status" in compiled
    assert "AS MATERIALIZED" in compiled and "LIMIT 257" in compiled
    assert "c.depth<8" in compiled
    assert "mod(txid_current()" not in compiled
    assert "pg_visible_in_snapshot" not in compiled


@pytest.mark.parametrize("status", ["committed", "aborted", None])
def test_unbound_non_live_child_never_reaches_trigger_install(monkeypatch, status):
    owned, _ = _sql_predicate(100, 101, status)
    child = {"oid": 7, "name": "own_child", "schema": "public", "own_transaction": owned}
    monkeypatch.setattr(fence, "_descendants", lambda *_: [child])
    monkeypatch.setattr(
        fence, "_install_triggers", lambda *_a, **_k: pytest.fail("unproven child install")
    )
    with pytest.raises(ArchiveBlocked, match="Unbound external restore child"):
        fence._coverage(object(), "synthetic_parent", {}, prepare_new=True)
