"""Static query/DDL contract checks; these are not PostgreSQL planner proofs."""

import re
from pathlib import Path

from app.tiered_archive.source import DATASETS

DEPLOY = Path(__file__).resolve().parents[2] / "deploy/tiered-archive"


def definitions():
    values = []
    for line in (DEPLOY / "direct-indexes.sql").read_text().splitlines():
        if line.startswith("CREATE INDEX"):
            match = re.fullmatch(
                r"CREATE INDEX CONCURRENTLY IF NOT EXISTS (\w+) ON public\.(\w+) \((.*)\);",
                line,
            )
            assert match
            values.append((match[1], match[2], match[3].split(", ")))
    return values


def test_expression_keys_match_unchanged_source_time_and_entire_keyset():
    items = definitions()
    assert len(items) == 3
    for name, table, keys in items:
        spec = DATASETS[table]
        assert spec.source == "direct" and spec.epoch
        field = spec.time.removeprefix("t.")
        cast = "::double precision" if table == "direct_visual_point" else ""
        assert keys == [f"pg_catalog.to_timestamp({field}{cast})", *spec.keys]
        assert name == "tiered_" + table + "_scan"


def test_preflight_and_postflight_surround_concurrent_creation():
    sql = (DEPLOY / "direct-indexes.sql").read_text()
    before = sql.index("\\set direct_index_require_all false")
    first = sql.index("CREATE INDEX CONCURRENTLY")
    last = sql.rindex("CREATE INDEX CONCURRENTLY")
    after = sql.index("\\set direct_index_require_all true")
    assert before < first <= last < after
    assert sql.count("\\ir direct-indexes-check.sql") == 2
    assert "\\set ON_ERROR_STOP on" in sql
    assert all("WHERE" not in line for line in sql.splitlines() if line.startswith("CREATE"))


def test_guard_rejects_same_name_numeric_partial_invalid_and_wrong_order():
    sql = (DEPLOY / "direct-indexes-check.sql").read_text()
    for guard in (
        "provolatile = 'i'",
        "actual.indrelid <> table_oid",
        "NOT actual.indisvalid",
        "NOT actual.indisready",
        "NOT actual.indislive",
        "actual.indpred IS NOT NULL",
        "actual.indkey[0] <> 0",
        "actual.indnatts <> cardinality(expected.keys)",
        "key_definitions IS DISTINCT FROM expected.keys",
    ):
        assert guard in sql
    assert "RAISE EXCEPTION" in sql
    statements = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert not re.search(r"\b(DROP|REINDEX|ALTER)\b", statements)


def test_visual_key_matches_actual_postgresql15_deparse():
    # Native PG15.10 pg_get_indexdef(index_oid, 1, true), not full DDL rendering.
    actual_key = "to_timestamp(bucket::double precision)"
    sql = (DEPLOY / "direct-indexes-check.sql").read_text()
    assert f"ARRAY['{actual_key}', 'segment_id', 'channel', 'bucket']" in sql
    assert "to_timestamp((bucket)::double precision)" not in sql
