import copy
import hashlib
from pathlib import Path

import pytest

from app.raw_archive import feature_identity_upgrade as m


class Journal:
    def __init__(self):
        self.events = []

    def emit(self, event, value):
        self.events.append((event, value))

    def has_build_intent(self, plan_sha256):
        return any(
            e == "index_build_intent" and v["plan_sha256"] == plan_sha256 for e, v in self.events
        )


def fixture():
    columns = [
        dict(
            name=n,
            attnum=i,
            type=2950 if i == 1 else 1043,
            typmod=-1 if i == 1 else 54 if i in (2, 3) else 68,
            collation=0 if i == 1 else 100,
            notnull=True,
        )
        for i, n in enumerate(m.NEW, 1)
    ]
    table = dict(
        oid=10,
        owner=20,
        namespace=2200,
        relkind="r",
        relpersistence="p",
        owns=True,
        estimated_rows=222144,
        total_bytes=518397952,
    )
    constraint = dict(
        oid=30,
        name=m.CONSTRAINT,
        kind="u",
        keys=[1, 2, 3, 4],
        index_oid=31,
        deferrable=False,
        deferred=False,
        validated=True,
        full_catalog={"oid": 30, "conkey": [1, 2, 3, 4]},
    )
    index = dict(
        oid=31,
        name=m.CONSTRAINT,
        owner=20,
        namespace=2200,
        table_oid=10,
        method="btree",
        unique=True,
        valid=True,
        ready=True,
        live=True,
        primary=False,
        exclusion=False,
        immediate=True,
        nulls_not_distinct=False,
        attributes=4,
        key_attributes=4,
        keys=[1, 2, 3, 4],
        options=[0] * 4,
        collations=[0, 100, 100, 100],
        no_expressions=True,
        no_predicate=True,
        default_ops=[True] * 4,
        dependencies=[30],
    )
    return dict(
        database=dict(
            name="synthetic",
            oid=1,
            actor="plantuser",
            backend_pid=123,
            version=150014,
            recovery=False,
            read_only="off",
        ),
        table=table,
        columns=columns,
        constraints=[constraint],
        indexes=[index],
        auth=[],
        heads=[{"version_num": "0023"}],
    )


class SQL:
    autocommit = True
    backend_pid = 123

    def __init__(self):
        self.state = fixture()
        self.statements = []
        self.idle = True
        self.backup = None
        self.fail_build = False
        self.block_switch = False
        self.corrupt_after_switch = False

    def close(self):
        self.idle = True

    def __call__(self, statement, parameters=()):
        self.statements.append(statement)
        for key in ("database", "table", "columns", "constraints", "indexes", "auth", "heads"):
            if "feature_identity:" + key in statement:
                value = self.state[key]
                return copy.deepcopy([value] if key in ("database", "table") else value)
        if statement.startswith("SELECT pg_try_advisory_lock"):
            return [{"held": True}]
        if statement.startswith("SELECT pg_advisory_unlock"):
            return [{"released": True}]
        if statement.startswith("SET "):
            return []
        if statement == m.CREATE_INDEX:
            assert self.idle
            index = copy.deepcopy(self.state["indexes"][0])
            index.update(
                oid=32,
                name=m.INDEX,
                attributes=6,
                key_attributes=6,
                keys=[1, 2, 3, 4, 5, 6],
                options=[0] * 6,
                collations=[0, 100, 100, 100, 100, 100],
                default_ops=[True] * 6,
                dependencies=[],
                valid=not self.fail_build,
                ready=not self.fail_build,
            )
            self.state["indexes"].append(index)
            if self.fail_build:
                self.fail_build = False
                raise TimeoutError("synthetic CIC wait")
        elif statement.startswith("DROP INDEX CONCURRENTLY"):
            assert self.idle and m.INDEX in statement
            self.state["indexes"] = [i for i in self.state["indexes"] if i["name"] != m.INDEX]
        elif statement == "BEGIN":
            self.backup = copy.deepcopy(self.state)
            self.idle = False
        elif statement.startswith("LOCK TABLE"):
            if self.block_switch:
                raise TimeoutError("synthetic lock conflict")
        elif statement == m.SWITCH:
            assert not self.idle
            self.state["indexes"] = [i for i in self.state["indexes"] if i["oid"] != 31]
            self.state["indexes"][0].update(name=m.CONSTRAINT, dependencies=[33])
            self.state["constraints"][0].update(
                oid=33,
                keys=list(range(1, 7)),
                index_oid=32,
                full_catalog={"oid": 33, "conkey": list(range(1, 7))},
            )
            if self.corrupt_after_switch:
                self.state["auth"].append({"name": "unexpected"})
        elif statement == "COMMIT":
            self.idle = True
        elif statement == "ROLLBACK":
            self.state = self.backup
            self.idle = True
        else:
            raise AssertionError(statement)
        return []


def reviewed(sql):
    return m.review(sql, expected_database="synthetic")


def test_prepare_concurrently_preserves_old_protection_and_head():
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    result = m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    assert result["columns"] == list(m.OLD)
    assert sql.state["heads"] == [{"version_num": "0023"}] and sql.state["auth"] == []
    assert sql.state["constraints"][0]["index_oid"] == 31
    assert sum(s == m.CREATE_INDEX for s in sql.statements) == 1
    assert not any(s.startswith(("UPDATE ", "DELETE ", "INSERT ")) for s in sql.statements)


def test_completed_build_is_reused_after_controller_failure():
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    assert sql.statements.count(m.CREATE_INDEX) == 1


def test_invalid_index_requires_same_journal_and_explicit_repair():
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    sql.fail_build = True
    with pytest.raises(TimeoutError):
        m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    for proof, repair in ((Journal(), True), (journal, False)):
        with pytest.raises(m.MigrationBlocked, match="owned_explicit_repair"):
            m.prepare(sql, plan, checkpoint=lambda: None, journal=proof, repair_invalid=repair)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal, repair_invalid=True)
    assert sql.state["indexes"][0]["oid"] == 31
    assert sum(s.startswith("DROP INDEX CONCURRENTLY") for s in sql.statements) == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(keys=[2, 1, 3, 4, 5, 6]),
        dict(no_predicate=False),
        dict(nulls_not_distinct=True),
        dict(default_ops=[False] * 6),
        dict(dependencies=[99]),
        dict(attributes=7),
    ],
)
def test_unreviewed_index_shape_is_not_adopted(change):
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    sql.state["indexes"][1].update(change)
    with pytest.raises(m.MigrationBlocked):
        m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)


def test_nowait_rollback_preserves_old_then_resumes_exactly():
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    sql.block_switch = True
    with pytest.raises(TimeoutError):
        m.switch(sql, plan, checkpoint=lambda: None, journal=journal)
    assert sql.idle and sql.state["constraints"][0]["index_oid"] == 31
    sql.block_switch = False
    assert m.switch(sql, plan, checkpoint=lambda: None, journal=journal)["columns"] == list(m.NEW)
    assert (
        m.switch(sql, plan, checkpoint=lambda: None, journal=journal)["phase"] == "already_switched"
    )
    assert sql.statements.count(m.SWITCH) == 1


def test_postalter_catalog_drift_rolls_back_entire_switch():
    sql, journal = SQL(), Journal()
    plan = reviewed(sql)
    m.prepare(sql, plan, checkpoint=lambda: None, journal=journal)
    sql.corrupt_after_switch = True
    with pytest.raises(m.MigrationBlocked, match="identity_changed"):
        m.switch(sql, plan, checkpoint=lambda: None, journal=journal)
    assert sql.state["constraints"][0]["index_oid"] == 31 and sql.state["auth"] == []


@pytest.mark.parametrize("key,value", [("typmod", 67), ("notnull", False)])
def test_bad_column_contract_blocks_before_ddl(key, value):
    sql = SQL()
    sql.state["columns"][-1][key] = value
    with pytest.raises(m.MigrationBlocked, match="column_type_or_nullability"):
        reviewed(sql)
    assert not any(s.startswith("CREATE ") for s in sql.statements)


def test_database_head_backend_drift_block_before_ddl():
    for mutate in (
        lambda s: s["heads"].append({"version_num": "0024"}),
        lambda s: s["database"].update(name="production"),
        lambda s: s["database"].update(backend_pid=999),
    ):
        sql = SQL()
        mutate(sql.state)
        with pytest.raises(m.MigrationBlocked):
            reviewed(sql)
        assert not any(s.startswith("CREATE ") for s in sql.statements)


def test_alembic_acknowledges_only_and_keeps_auth_dependency():
    import ast

    file = Path(__file__).resolve().parents[2] / "alembic/versions/0025_feature_result_identity.py"
    tree = ast.parse(file.read_text())
    namespace = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                namespace[target.id] = ast.literal_eval(node.value)
    assert namespace["down_revision"] == "0024"
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    assert not any(
        isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and any(word in n.value for word in ("ALTER TABLE", "CREATE INDEX", "UPDATE "))
        for n in ast.walk(upgrade)
    )
    with pytest.raises(m.MigrationBlocked, match="prepare_and_switch"):
        m.completed(SQL())


def test_durable_private_intent_reopens(tmp_path):
    plan = reviewed(SQL())
    journal = m.DirectoryJournal(tmp_path / "journal")
    journal.emit(
        "index_build_intent",
        dict(
            plan_sha256=m.digest(plan),
            index=m.INDEX,
            statement_sha256=hashlib.sha256(m.CREATE_INDEX.encode()).hexdigest(),
        ),
    )
    journal.close()
    journal = m.DirectoryJournal(tmp_path / "journal")
    assert journal.has_build_intent(m.digest(plan))
    assert (tmp_path / "journal/journal.jsonl").stat().st_mode & 0o777 == 0o600
    journal.close()


def test_async_adapter_cancels_on_guard_failure_but_allows_rollback():
    import time

    class Cursor:
        description = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters):
            calls.append(statement)

    class Connection:
        cancelled = False

        def cursor(self):
            return Cursor()

        def cancel(self):
            self.cancelled = True

        def poll(self):
            return 0

        def close(self):
            pytest.fail("successful cancellation should retain rollback connection")

    calls = []
    checks = []

    def checkpoint():
        checks.append(True)
        if len(checks) > 1:
            raise m.MigrationBlocked("synthetic health pause")

    sql = m.PostgreSQL.__new__(m.PostgreSQL)
    sql.checkpoint = checkpoint
    sql.deadline = time.monotonic() + 10
    sql.last_check = 0
    sql.connection = Connection()
    with pytest.raises(m.MigrationBlocked, match="health pause"):
        sql(m.CREATE_INDEX)
    assert sql.connection.cancelled
    sql("ROLLBACK")
    assert calls == [m.CREATE_INDEX, "ROLLBACK"]


def test_async_adapter_expired_scope_never_sends_new_query():
    import time

    sql = m.PostgreSQL.__new__(m.PostgreSQL)
    sql.deadline = time.monotonic() - 1
    sql.checkpoint = lambda: pytest.fail("expired operation admitted")
    with pytest.raises(m.MigrationBlocked, match="migration_deadline"):
        sql(m.CREATE_INDEX)
