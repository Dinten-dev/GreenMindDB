"""Explicit online feature capability, independent of authentication/Alembic heads.

Operators supply a direct primary SQL adapter and the existing health checkpoint.
No connection, DDL, rows, migrations or head stamps occur at import time.
"""

import fcntl
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

OLD = ("wav_file_id", "extractor_version", "calibration_version", "parameter_hash")
NEW = (*OLD, "source_sha256", "feature_checksum")
CONSTRAINT = "uq_wav_feature_version_identity"
INDEX = "uq_wav_feature_result_online_v1"
VERSION = "greenmind.feature-identity-online.v1"
LOCK = (1196246601, 1)

CREATE_INDEX = (
    'CREATE UNIQUE INDEX CONCURRENTLY "' + INDEX + '" ON public.wav_feature_version '
    "USING btree (" + ",".join('"' + name + '"' for name in NEW) + ")"
)
SWITCH = (
    'ALTER TABLE public.wav_feature_version DROP CONSTRAINT "' + CONSTRAINT + '", '
    'ADD CONSTRAINT "' + CONSTRAINT + '" UNIQUE USING INDEX "' + INDEX + '"'
)


class MigrationBlocked(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise MigrationBlocked(reason)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class DirectoryJournal:
    """Durable private proof; never store DSNs, credentials or application rows."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = self.root.lstat()
        need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            "private_migration_journal_required",
        )
        fd = os.open(self.root / "journal.jsonl", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.file = os.fdopen(fd, "r+b", buffering=0)
        info = os.fstat(fd)
        need(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600,
            "private_journal_file_required",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def emit(self, event, value):
        raw = encoded({"event": event, "value": value}) + b"\n"
        need(len(raw) <= 32768, "migration_proof_record_too_large")
        self.file.seek(0, os.SEEK_END)
        need(self.file.tell() + len(raw) <= 2 * 1024**2, "migration_journal_budget")
        need(self.file.write(raw) == len(raw), "incomplete_migration_proof_write")
        os.fsync(self.file.fileno())

    def has_build_intent(self, plan_sha256):
        self.file.seek(0)
        raw = self.file.read(2 * 1024**2 + 1)
        need(len(raw) <= 2 * 1024**2, "migration_journal_budget")
        for line in raw.splitlines():
            item = json.loads(line)
            if item == {
                "event": "index_build_intent",
                "value": {
                    "plan_sha256": plan_sha256,
                    "index": INDEX,
                    "statement_sha256": hashlib.sha256(CREATE_INDEX.encode()).hexdigest(),
                },
            }:
                return True
        return False

    def close(self):
        self.file.close()


DB = """/* feature_identity:database */
SELECT current_database() AS name,d.oid::bigint AS oid,current_user AS actor,
       pg_backend_pid() AS backend_pid,current_setting('server_version_num')::int AS version,
       pg_is_in_recovery() AS recovery,current_setting('transaction_read_only') AS read_only
FROM pg_catalog.pg_database d WHERE d.datname=current_database()"""
TABLE = """/* feature_identity:table */
SELECT c.oid::bigint AS oid,c.relowner::bigint AS owner,c.relnamespace::bigint AS namespace,
       c.relkind,c.relpersistence,pg_has_role(c.relowner,'USAGE') AS owns,
       c.reltuples::bigint AS estimated_rows,pg_total_relation_size(c.oid) AS total_bytes
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname='wav_feature_version'"""
COLUMNS = """/* feature_identity:columns */
SELECT a.attname AS name,a.attnum,a.atttypid::bigint AS type,a.atttypmod AS typmod,
       a.attcollation::bigint AS collation,a.attnotnull AS notnull
FROM pg_catalog.pg_attribute a WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped
ORDER BY a.attnum"""
CONSTRAINTS = """/* feature_identity:constraints */
SELECT x.oid::bigint AS oid,x.conname AS name,x.contype AS kind,
       x.conkey::smallint[] AS keys,x.conindid::bigint AS index_oid,
       x.condeferrable AS deferrable,x.condeferred AS deferred,x.convalidated AS validated,
       to_jsonb(x) AS full_catalog
FROM pg_catalog.pg_constraint x WHERE x.conrelid=%s ORDER BY x.oid"""
INDEXES = """/* feature_identity:indexes */
SELECT c.oid::bigint AS oid,c.relname AS name,c.relowner::bigint AS owner,
       c.relnamespace::bigint AS namespace,i.indrelid::bigint AS table_oid,am.amname AS method,
       i.indisunique AS unique,i.indisvalid AS valid,i.indisready AS ready,i.indislive AS live,
       i.indisprimary AS primary,i.indisexclusion AS exclusion,i.indimmediate AS immediate,
       i.indnullsnotdistinct AS nulls_not_distinct,i.indnatts AS attributes,i.indnkeyatts AS key_attributes,
       i.indkey::smallint[] AS keys,i.indoption::smallint[] AS options,
       i.indcollation::oid[] AS collations,i.indexprs IS NULL AS no_expressions,
       i.indpred IS NULL AS no_predicate,
       ARRAY(SELECT o.opcdefault AND ns.nspname='pg_catalog'
         FROM unnest(i.indclass::oid[]) WITH ORDINALITY q(oid,pos)
         JOIN pg_catalog.pg_opclass o ON o.oid=q.oid
         JOIN pg_catalog.pg_namespace ns ON ns.oid=o.opcnamespace ORDER BY q.pos) AS default_ops,
       ARRAY(SELECT x.oid::bigint FROM pg_catalog.pg_constraint x WHERE x.conindid=c.oid
         ORDER BY x.oid) AS dependencies
FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
JOIN pg_catalog.pg_am am ON am.oid=c.relam WHERE i.indrelid=%s ORDER BY c.oid"""
AUTH = """/* feature_identity:auth */
SELECT c.relname AS name,to_jsonb(c)-'reltuples'-'relpages'-'relallvisible'-'relfrozenxid'-'relminmxid'
       AS relation,
       (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a
         WHERE a.attrelid=c.oid AND a.attnum>0) AS columns,
       (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.oid) FROM pg_catalog.pg_constraint x
         WHERE x.conrelid=c.oid) AS constraints,
       (SELECT jsonb_agg(to_jsonb(i) ORDER BY i.indexrelid) FROM pg_catalog.pg_index i
         WHERE i.indrelid=c.oid) AS indexes
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN ('auth_session','access_invitation') ORDER BY c.relname"""
HEADS = "/* feature_identity:heads */ SELECT version_num FROM public.alembic_version ORDER BY version_num"


def snapshot(sql):
    database = sql(DB)[0]
    need(database["backend_pid"] == sql.backend_pid, "direct_stable_postgres_backend_required")
    tables = sql(TABLE)
    need(len(tables) == 1, "exact_feature_table_required")
    table = tables[0]
    need(
        table["relkind"] == "r" and table["relpersistence"] == "p",
        "ordinary_permanent_table_required",
    )
    return {
        "database": database,
        "table": table,
        "columns": sql(COLUMNS, (table["oid"],)),
        "constraints": sql(CONSTRAINTS, (table["oid"],)),
        "indexes": sql(INDEXES, (table["oid"],)),
        "auth_sha256": digest(sql(AUTH)),
        "heads": [r["version_num"] for r in sql(HEADS)],
    }


def feature_columns(state):
    attrs = {v["name"]: v for v in state["columns"]}
    expected = {
        "wav_file_id": (2950, -1),
        "extractor_version": (1043, 54),
        "calibration_version": (1043, 54),
        "parameter_hash": (1043, 68),
        "source_sha256": (1043, 68),
        "feature_checksum": (1043, 68),
    }
    need(
        all(
            name in attrs
            and attrs[name]["notnull"]
            and (attrs[name]["type"], attrs[name]["typmod"]) == shape
            for name, shape in expected.items()
        ),
        "feature_column_type_or_nullability_mismatch",
    )
    return attrs


def index_shape(index, state, columns, *, valid=True, dependencies=None):
    attrs = feature_columns(state)
    need(all(name in NEW for name in columns), "unreviewed_feature_index_columns")
    table = state["table"]
    need(
        index["owner"] == table["owner"]
        and index["namespace"] == table["namespace"]
        and index["table_oid"] == table["oid"]
        and index["method"] == "btree"
        and index["unique"] is True
        and index["immediate"] is True
        and not index["primary"]
        and not index["exclusion"]
        and not index["nulls_not_distinct"]
        and index["attributes"] == index["key_attributes"] == len(columns)
        and index["keys"] == [attrs[name]["attnum"] for name in columns]
        and index["collations"] == [attrs[name]["collation"] for name in columns]
        and index["options"] == [0] * len(columns)
        and index["default_ops"] == [True] * len(columns)
        and index["no_expressions"]
        and index["no_predicate"],
        "feature_index_shape_mismatch",
    )
    if valid:
        need(index["valid"] and index["ready"] and index["live"], "feature_index_invalid")
    else:
        need(index["live"] and not index["valid"], "only_live_invalid_index_repairable")
    if dependencies is not None:
        need(index["dependencies"] == dependencies, "feature_index_has_unreviewed_dependencies")


def feature_constraint(state):
    rows = [x for x in state["constraints"] if x["name"] == CONSTRAINT]
    need(len(rows) == 1, "exact_unique_feature_constraint_required")
    row = rows[0]
    need(
        row["kind"] == "u" and row["validated"] and not row["deferrable"] and not row["deferred"],
        "ordinary_validated_feature_unique_required",
    )
    by_number = {x["attnum"]: x["name"] for x in state["columns"]}
    columns = tuple(by_number.get(k) for k in row["keys"])
    indexes = [i for i in state["indexes"] if i["oid"] == row["index_oid"]]
    need(len(indexes) == 1, "constraint_backing_index_missing")
    index_shape(indexes[0], state, columns, dependencies=[row["oid"]])
    need(indexes[0]["name"] == CONSTRAINT, "constraint_index_name_changed")
    return row, columns


def base_identity(state):
    return {
        "database": {k: state["database"][k] for k in ("name", "oid", "actor", "version")},
        "table": {
            k: state["table"][k] for k in ("oid", "owner", "namespace", "relkind", "relpersistence")
        },
        "columns": state["columns"],
        "auth_sha256": state["auth_sha256"],
        "heads": state["heads"],
        "other_constraints": [
            x["full_catalog"] for x in state["constraints"] if x["name"] != CONSTRAINT
        ],
    }


def review(sql, *, expected_database, expected_heads=("0023",)):
    need(sql.autocommit and sql.idle, "autocommit_idle_adapter_required")
    state = snapshot(sql)
    need(
        state["database"]["name"] == expected_database
        and not state["database"]["recovery"]
        and state["database"]["version"] // 10000 == 15,
        "reviewed_primary_PostgreSQL15_required",
    )
    need(state["heads"] == list(expected_heads), "unreviewed_alembic_head")
    constraint, columns = feature_constraint(state)
    need(columns in (OLD, NEW), "unexpected_feature_constraint")
    return {
        "schema": VERSION,
        "base": base_identity(state),
        "old_constraint_oid": constraint["oid"],
        "old_index_oid": constraint["index_oid"],
        "initial_columns": list(columns),
        "estimated_rows": state["table"]["estimated_rows"],
        "table_total_bytes": state["table"]["total_bytes"],
    }


def compatible(state, plan):
    need(
        plan["schema"] == VERSION and base_identity(state) == plan["base"],
        "reviewed_schema_or_identity_changed",
    )
    need(
        state["table"]["owns"]
        and not state["database"]["recovery"]
        and state["database"]["read_only"] == "off",
        "existing_table_owner_write_session_required",
    )
    constraint, columns = feature_constraint(state)
    need(columns in (OLD, NEW), "unexpected_feature_constraint")
    if columns == OLD:
        need(
            constraint["oid"] == plan["old_constraint_oid"]
            and constraint["index_oid"] == plan["old_index_oid"],
            "old_protection_changed",
        )
    return constraint, columns


@contextmanager
def authority(sql):
    need(sql.autocommit and sql.idle, "autocommit_idle_adapter_required")
    held = sql("SELECT pg_try_advisory_lock(%s,%s) AS held", LOCK)[0]["held"]
    need(held is True, "another_feature_migrator_active")
    try:
        yield
    finally:
        try:
            need(
                sql("SELECT pg_advisory_unlock(%s,%s) AS released", LOCK)[0]["released"] is True,
                "migration_authority_lost",
            )
        except BaseException:
            sql.close()  # Never retain a session lock or unknown transaction.
            raise


def _proof(sql, plan, journal, phase):
    state = snapshot(sql)
    constraint, columns = compatible(state, plan)
    value = {
        "plan_sha256": digest(plan),
        "phase": phase,
        "columns": list(columns),
        "auth_sha256": state["auth_sha256"],
        "alembic_heads": state["heads"],
        "other_constraints_sha256": digest(base_identity(state)["other_constraints"]),
        "constraint_oid": constraint["oid"],
        "constraint_index_oid": constraint["index_oid"],
        "indexes_sha256": digest(state["indexes"]),
    }
    journal.emit(phase, value)
    return value


def prepare(sql, plan, *, checkpoint, journal, repair_invalid=False):
    """No transaction block; only a new, exact six-column index can be repaired."""
    with authority(sql):
        checkpoint()
        state = snapshot(sql)
        _, columns = compatible(state, plan)
        if columns == NEW:
            return _proof(sql, plan, journal, "already_switched")
        for command in (
            "SET lock_timeout='150ms'",
            "SET statement_timeout='120s'",
            "SET maintenance_work_mem='16MB'",
            "SET max_parallel_maintenance_workers=0",
        ):
            sql(command)
        proposed = [x for x in state["indexes"] if x["name"] == INDEX]
        need(len(proposed) <= 1, "ambiguous_prepare_index")
        if proposed:
            current = proposed[0]
            if current["valid"]:
                index_shape(current, state, NEW, dependencies=[])
                return _proof(sql, plan, journal, "index_ready")
            index_shape(current, state, NEW, valid=False, dependencies=[])
            need(
                repair_invalid and journal.has_build_intent(digest(plan)),
                "invalid_index_needs_owned_explicit_repair",
            )
            checkpoint()
            journal.emit(
                "invalid_index_drop_intent",
                {"plan_sha256": digest(plan), "index_oid": current["oid"]},
            )
            sql('DROP INDEX CONCURRENTLY public."' + INDEX + '"')
        checkpoint()
        journal.emit(
            "index_build_intent",
            {
                "plan_sha256": digest(plan),
                "index": INDEX,
                "statement_sha256": hashlib.sha256(CREATE_INDEX.encode()).hexdigest(),
            },
        )
        try:
            sql(CREATE_INDEX)
        except BaseException as error:
            journal.emit(
                "index_build_failed",
                {
                    "plan_sha256": digest(plan),
                    "error_type": type(error).__name__,
                    "sqlstate": getattr(error, "pgcode", None),
                },
            )
            raise
        checkpoint()
        state = snapshot(sql)
        _, columns = compatible(state, plan)
        need(columns == OLD, "old_protection_must_survive_prepare")
        indexes = [x for x in state["indexes"] if x["name"] == INDEX]
        need(len(indexes) == 1, "prepared_index_missing")
        index_shape(indexes[0], state, NEW, dependencies=[])
        return _proof(sql, plan, journal, "index_ready")


def switch(sql, plan, *, checkpoint, journal):
    """Short NOWAIT metadata transaction; any failure rolls back the old unique."""
    with authority(sql):
        checkpoint()
        state = snapshot(sql)
        _, columns = compatible(state, plan)
        if columns == NEW:
            return _proof(sql, plan, journal, "already_switched")
        journal.emit("switch_intent", {"plan_sha256": digest(plan)})
        sql("BEGIN")
        try:
            sql("SET LOCAL lock_timeout='150ms'")
            sql("SET LOCAL statement_timeout='2s'")
            sql("SET LOCAL idle_in_transaction_session_timeout='2s'")
            sql("LOCK TABLE ONLY public.wav_feature_version IN ACCESS EXCLUSIVE MODE NOWAIT")
            state = snapshot(sql)
            _, columns = compatible(state, plan)
            need(columns == OLD, "constraint_changed_before_switch")
            indexes = [x for x in state["indexes"] if x["name"] == INDEX]
            need(len(indexes) == 1, "prepare_index_first")
            index_shape(indexes[0], state, NEW, dependencies=[])
            sql(SWITCH)
            need(compatible(snapshot(sql), plan)[1] == NEW, "six_column_switch_not_observed")
            sql("COMMIT")
        except BaseException as error:
            try:
                sql("ROLLBACK")
            except BaseException:
                sql.close()
            journal.emit(
                "switch_failed",
                {
                    "plan_sha256": digest(plan),
                    "error_type": type(error).__name__,
                    "sqlstate": getattr(error, "pgcode", None),
                },
            )
            raise
        checkpoint()
        return _proof(sql, plan, journal, "switched")


def completed(sql):
    """0025 acknowledges the capability only; never builds indexes or auth tables."""
    state = snapshot(sql)
    _, columns = feature_constraint(state)
    need(columns == NEW, "explicit_online_feature_prepare_and_switch_required_before_0025")
    return {"schema": VERSION, "columns": list(columns), "table_oid": state["table"]["oid"]}


class PostgreSQL:
    """Direct async PG session; guard polling cancels expensive backend work.

    Native production callers must also retain backend/actor scope and prove
    cancellation after external termination. Server statement timeout remains
    finite even if the Python controller disappears. No DSN is journaled.
    """

    def __init__(self, dsn, *, checkpoint, seconds=180):
        import time

        import psycopg2

        need(
            type(seconds) in (int, float) and 1 <= seconds <= 300,
            "bounded_online_migration_required",
        )
        checkpoint()
        self.checkpoint = checkpoint
        self.deadline = time.monotonic() + seconds
        self.last_check = 0.0
        self.connection = psycopg2.connect(
            dsn,
            async_=True,
            connect_timeout=3,
            options="-c application_name=greenmind-feature-identity-online-v1 "
            "-c lock_timeout=150 -c statement_timeout=120000 "
            "-c maintenance_work_mem=16384 -c max_parallel_maintenance_workers=0",
        )
        try:
            self._wait()
        except BaseException:
            self.connection.close()
            raise

    @property
    def backend_pid(self):
        return self.connection.get_backend_pid()

    @property
    def autocommit(self):
        return self.connection.autocommit

    @property
    def idle(self):
        from psycopg2.extensions import TRANSACTION_STATUS_IDLE

        return self.connection.get_transaction_status() == TRANSACTION_STATUS_IDLE

    def _wait(self, *, recovery=False):
        import select
        import time

        from psycopg2.extensions import POLL_OK, POLL_READ, POLL_WRITE

        end = time.monotonic() + 5 if recovery else self.deadline
        while True:
            now = time.monotonic()
            need(now < end, "migration_deadline")
            if not recovery and now - self.last_check >= 1:
                self.checkpoint()
                self.last_check = now
            state = self.connection.poll()
            if state == POLL_OK:
                return
            need(state in (POLL_READ, POLL_WRITE), "unexpected_postgres_poll_state")
            select.select(
                [self.connection] if state == POLL_READ else [],
                [self.connection] if state == POLL_WRITE else [],
                [],
                min(0.2, end - now),
            )

    def __call__(self, statement, parameters=()):
        import time

        recovery = statement == "ROLLBACK" or statement.startswith("SELECT pg_advisory_unlock(")
        if not recovery:
            need(time.monotonic() < self.deadline, "migration_deadline")
            self.checkpoint()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                self._wait(recovery=recovery)
                if cursor.description is None:
                    return []
                names = [column.name for column in cursor.description]
                return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        except BaseException:
            try:
                self.connection.cancel()
                self._wait(recovery=True)
            except BaseException:
                # Closing never commits a transaction. The server-side120s
                # statement deadline independently bounds a disconnected CIC.
                self.connection.close()
            raise

    def close(self):
        self.connection.close()
