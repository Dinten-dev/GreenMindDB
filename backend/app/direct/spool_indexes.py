"""Explicit concurrent index upgrade; never run from receiver/worker startup."""

from sqlalchemy import inspect, text

INDEXES = (
    (
        "ix_direct_chunk_inline_received",
        "direct_chunk",
        ["received_at", "id", "device_id"],
        "(payload IS NOT NULL)",
    ),
    ("ix_direct_piece_chunk_id", "direct_piece", ["chunk_id"], None),
)


def upgrade(engine):
    if engine.dialect.name != "postgresql":
        raise ValueError("Concurrent spool upgrade requires PostgreSQL")
    names = set(inspect(engine).get_table_names())
    if not {"direct_chunk", "direct_piece"} <= names or any(
        not name.startswith("direct_") for name in names
    ):
        raise ValueError("Refusing index upgrade outside the Direct database")
    # CONCURRENTLY keeps intake writable and cannot run inside a transaction.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        try:
            connection.execute(text("SET lock_timeout='100ms'"))
            connection.execute(text("SET statement_timeout='600000ms'"))
            connection.execute(text("SET maintenance_work_mem='32MB'"))
            connection.execute(text("SET max_parallel_maintenance_workers=0"))
            for name, table, columns, predicate in INDEXES:
                suffix = " WHERE " + predicate if predicate else ""
                connection.execute(
                    text(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                        f"ON public.{table} ({', '.join(columns)}){suffix}"
                    )
                )
                actual = connection.execute(
                    text("""SELECT t.relname, i.indisvalid, i.indisunique,
                    ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,false)
                          FROM generate_series(1,i.indnkeyatts) n),
                    pg_get_expr(i.indpred,i.indrelid), am.amname
                    FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid
                    JOIN pg_class t ON t.oid=i.indrelid
                    JOIN pg_am am ON am.oid=idx.relam
                    WHERE i.indexrelid=to_regclass(:name)"""),
                    {"name": "public." + name},
                ).one()
                if tuple(actual) != (table, True, False, columns, predicate, "btree"):
                    raise ValueError(
                        "Existing spool index is invalid or has a different definition"
                    )
        finally:
            # Do not return a pooled connection with maintenance settings.
            connection.invalidate()
