"""Transaction-local safeguards for explicitly enabled private read pools."""

import os

from sqlalchemy import event

READ_ROLES = {"greenmind_archive_reader_33fe395", "greenmind_wav_metadata_b4ae52d674aa"}


def attach_read_guards(engine, *, statement_ms, lock_ms, work_mem_kib=None, idle_ms=None):
    enabled = os.getenv("GREENMIND_TRANSACTION_POOL_READS") == "true"
    pooled = engine.url.port == 6432 and engine.url.username in READ_ROLES
    if not enabled:
        if pooled:
            raise ValueError("Private pooling requires explicit transaction safeguards")
        return engine
    if (
        not pooled
        or engine.url.host != "127.0.0.1"
        or engine.url.database not in {"plantdb", "greenmind_direct_production"}
    ):
        raise ValueError("Only named private read database pools are permitted")
    if not 1 <= statement_ms <= 8000 or not 1 <= lock_ms <= 500:
        raise ValueError("Read transaction budgets must remain bounded")
    if work_mem_kib is not None and not 1 <= work_mem_kib <= 8192:
        raise ValueError("Read work memory must remain bounded")
    if idle_ms is not None and not 1 <= idle_ms <= 5000:
        raise ValueError("Idle transaction budget must remain bounded")
    statements = [
        "SET LOCAL transaction_read_only=on",
        f"SET LOCAL statement_timeout={statement_ms}",
        f"SET LOCAL lock_timeout={lock_ms}",
        "SET LOCAL jit=off",
    ]
    if work_mem_kib is not None:
        statements.append(f"SET LOCAL work_mem={work_mem_kib}")
    if idle_ms is not None:
        statements.append(f"SET LOCAL idle_in_transaction_session_timeout={idle_ms}")

    @event.listens_for(engine, "begin")
    def configure_transaction(connection):
        # Custom startup GUCs are not reliably reported by every PostgreSQL
        # version. SET LOCAL precedes the first query and resets at transaction end.
        for statement in statements:
            connection.exec_driver_sql(statement)

    return engine
