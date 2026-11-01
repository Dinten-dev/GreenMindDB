"""Offline SQL scope/audit tests; DB connections are explicit test doubles."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import codec, registry_backup
from app.tiered_archive import sql_deletion as deletion
from app.tiered_archive import sql_deletion_audit as audit
from app.tiered_archive import sql_deletion_scope as scope
from tests.test_sql_deletion import NOW, setup_job
from tests.test_tiered_archive import bundle  # noqa: F401


def source_identity():
    return {
        "database": "source-test",
        "database_oid": 42,
        "role": "writer",
        "role_oid": 43,
        "server_address": "127.0.0.1/32",
        "server_port": 5432,
        "version": 150010,
        "session_role": "writer",
        "restricted_role": True,
        "replication_role": "origin",
        "read_only": "off",
    }


def test_review_only_ddl_is_additive_and_role_safe():
    plan = audit.ddl_plan("greenmind_sql_writer")
    assert plan["executed"] is False and plan["status"] == "REVIEW_ONLY"
    assert "ENABLE ALWAYS TRIGGER" in plan["ddl"]
    assert "REVOKE ALL" in plan["ddl"] and "GRANT SELECT,INSERT" in plan["ddl"]
    assert f"pg_advisory_xact_lock({audit.GLOBAL_LOCK}::bigint)" in audit.ROW_BODY
    with pytest.raises(ArchiveBlocked):
        audit.ddl_plan("role; DROP TABLE ingest_log;--")


def genesis():
    return {
        "format": "greenmind.sql-scope-genesis.v1",
        "epoch": "1" * 32,
        "source_identity_sha256": "2" * 64,
        "audit_schema_sha256": "3" * 64,
        "origin_inventory_sha256": "4" * 64,
        "registry_reference_sha256": "5" * 64,
        "runtime_sha256": "6" * 64,
        "bindings_sha256": "7" * 64,
        "proof_sha256": "8" * 64,
    }


def test_history_rejects_lost_budget_and_unmatched_commits(tmp_path):
    candidate, _, ledger, _, _, _ = setup_job(tmp_path)
    body = genesis()
    base = (audit.event_id(body), body["epoch"], "genesis", audit.canonical(body), "100")

    class DB:
        rows = [base]
        row_events = []

        def execute(self, statement, *_args, **_kwargs):
            return SimpleNamespace(
                all=lambda: self.row_events
                if "greenmind_sql_deletion_row_audit_v1" in str(statement)
                else self.rows
            )

    db = DB()
    assert scope.verify_history(db, ledger, body, strict=True)["commits"] == 0
    db.row_events = [("0" * 64, "102", "a" * 64)]
    with pytest.raises(ArchiveBlocked, match="deleted-row audit"):
        scope.verify_history(db, ledger, body, strict=True)
    db.row_events = []
    permit = {
        "pilot_id": "a" * 32,
        "runtime_sha256": "6" * 64,
        "bindings_sha256": "7" * 64,
        "proof_contract_sha256": "8" * 64,
        "fence_contract_sha256": "9" * 64,
    }
    commit = scope.commit_body(body, candidate, permit, "b" * 64, ["c" * 64, "d" * 64])
    db.rows = [
        base,
        (audit.event_id(commit), body["epoch"], "commit", audit.canonical(commit), "101"),
    ]
    with pytest.raises(ArchiveBlocked, match="budget"):
        scope.verify_history(db, ledger, body, strict=True)
    db.rows = []
    with pytest.raises(ArchiveBlocked, match="genesis"):
        scope.verify_history(db, ledger, body, strict=True)
    ledger.close()


def test_bootstrap_resumes_after_source_genesis_before_ledger_commit(tmp_path, monkeypatch):
    candidate, adapter, ledger, kwargs, _, _ = setup_job(tmp_path)
    ledger.db.execute("DROP TABLE sql_deletion_scope")
    ledger.db.commit()
    permit = deletion.activation(
        kwargs["authorization_path"], kwargs["authorization_sha256"], candidate, NOW
    )
    proof = SimpleNamespace(
        config={"source_identity": source_identity(), "audit_schema_sha256": "f" * 64}
    )
    monkeypatch.setattr(
        scope, "_preflight", lambda *_a, **_k: (permit, ledger.get(candidate.identity))
    )
    monkeypatch.setattr(deletion, "_begin_guard", lambda *_a: None)
    monkeypatch.setattr(deletion, "_guard", lambda *_a, **_k: None)
    monkeypatch.setattr(deletion, "_guard_deadline", lambda *_a: None)
    monkeypatch.setattr(
        audit, "inspect", lambda *_a: audit.identity_anchor(proof.config["source_identity"])
    )
    rows = []

    class DB:
        def execute(self, statement, *_args):
            if "count(*)" in str(statement):
                return SimpleNamespace(
                    scalar_one=lambda: 0 if "row_audit" in str(statement) else len(rows)
                )
            raise AssertionError(str(statement))

    db = DB()
    inside = [False]

    @contextmanager
    def exclusive(_):
        assert not inside[0]
        inside[0] = True
        adapter.connection = db
        try:
            yield
        finally:
            adapter.connection = None
            inside[0] = False

    adapter.exclusive = exclusive
    monkeypatch.setattr(
        audit,
        "insert",
        lambda _db, kind, body: rows.append((kind, body.copy())) or audit.event_id(body),
    )
    monkeypatch.setattr(
        audit,
        "read",
        lambda _db, kind, body: audit.event_id(body)
        if rows == [(kind, body)]
        else (_ for _ in ()).throw(ArchiveBlocked("audit differs")),
    )
    evidence = scope.RegistryEvidence(
        scope._REGISTRY_TOKEN, ledger, codec.digest(scope.inventory(ledger)), "d" * 64
    )
    calls = [0]

    def checkpoint():
        if rows and not inside[0]:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("after_source_commit")

    params = {
        **kwargs,
        "proof_guard": proof,
        "checkpoint": checkpoint,
        "registry_evidence": evidence,
    }
    with pytest.raises(RuntimeError, match="after_source_commit"):
        scope.bootstrap(candidate, adapter, ledger, **params)
    assert ledger.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "bootstrap_pending",
    )
    assert len(rows) == 1
    with pytest.raises(ArchiveBlocked, match="reconciliation"):
        registry_backup.backup(ledger, object(), checkpoint=lambda: None)
    result = scope.bootstrap(candidate, adapter, ledger, **params)
    assert result["status"] == "SQL_SCOPE_CURRENT" and len(rows) == 1
    assert ledger.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == ("current",)
    assert not scope._table(ledger.db, "sql_deletion_transition")
    ledger.close()


def test_empty_or_legacy_scope_never_implies_current(tmp_path):
    _, _, ledger, _, _, _ = setup_job(tmp_path)
    with pytest.raises(ArchiveBlocked, match="provenance"):
        scope.current_epoch(ledger, object(), "a" * 64, "b" * 64)
    ledger.db.execute("UPDATE sql_deletion_scope SET state='needs_reconcile'")
    ledger.db.commit()
    with pytest.raises(ArchiveBlocked, match="provenance"):
        scope.current_epoch(ledger, object(), "a" * 64, "b" * 64)
    ledger.close()


@pytest.mark.parametrize("fault", [None, "owner", "trigger", "fence", "schema"])
def test_native_audit_catalog_inspection_requires_immutable_relation(monkeypatch, fault):
    from app.tiered_archive import sql_deletion_guard

    identity = source_identity()
    monkeypatch.setattr(sql_deletion_guard, "_identity", lambda _db: identity)
    monkeypatch.setattr(audit, "inspect_row_capture", lambda *_args: None)
    monkeypatch.setattr(
        audit, "schema_sha256", lambda *_args: "a" * 64 if fault != "schema" else "b" * 64
    )
    columns = [
        ("event_id", "text", True),
        ("epoch", "text", True),
        ("kind", "text", True),
        ("body", "text", True),
        ("transaction_id", "bigint", True),
        ("created_at", "timestamp with time zone", True),
    ]
    properties = {
        "relkind": "r",
        "relpersistence": "p",
        "relrowsecurity": False,
        "relforcerowsecurity": False,
        "owner_member": fault == "owner",
        "can_select": True,
        "can_insert": True,
        "can_mutate": False,
        "inherited": False,
        "exact_primary_key": True,
    }
    function = {
        "body": audit.BODY,
        "security_definer": False,
        "volatility": "v",
        "config": ["search_path=pg_catalog"],
        "language": "plpgsql",
        "owner_member": False,
    }
    triggers = [
        {
            "tgname": "gm_sql_audit_immutable_row",
            "tgtype": 27,
            "tgenabled": "O" if fault == "trigger" else "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
        {
            "tgname": "gm_sql_audit_immutable_truncate",
            "tgtype": 34,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
    ]

    class Rows:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def all(self):
            return self.value

        def one(self):
            return self.value

        def scalar_one(self):
            return self.value

    class DB:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, _params=None):
            sql = str(statement)
            if "FROM pg_catalog.pg_attribute" in sql:
                return Rows(columns)
            if "FROM pg_catalog.pg_class c" in sql:
                return Rows(properties)
            if "FROM pg_catalog.pg_proc p" in sql:
                return Rows(function)
            if "FROM pg_catalog.pg_trigger t" in sql:
                return Rows(triggers)
            if "pg_try_advisory_xact_lock" in sql:
                return Rows(fault != "fence")
            raise AssertionError(sql)

    if fault is None:
        assert audit.inspect(DB(), "a" * 64, identity) == audit.identity_anchor(identity)
    else:
        with pytest.raises(ArchiveBlocked):
            audit.inspect(DB(), "a" * 64, identity)


def test_audit_insert_binds_actual_source_transaction():
    body = genesis()

    class DB:
        values = []

        def execute(self, statement, params=None):
            if str(statement).startswith("INSERT"):
                self.values.append(params)
                return SimpleNamespace(scalar_one=lambda: "123")
            return SimpleNamespace(scalar_one=lambda: "123")

    db = DB()
    assert audit.insert(db, "genesis", body) == audit.event_id(body)
    assert db.values[0]["body"] == audit.canonical(body)

    class WrongTx(DB):
        def execute(self, statement, params=None):
            return SimpleNamespace(
                scalar_one=lambda: "123" if str(statement).startswith("INSERT") else "124"
            )

    with pytest.raises(ArchiveBlocked, match="transaction"):
        audit.insert(WrongTx(), "genesis", body)


def test_registry_evidence_restores_exact_budget_and_rejects_drift(bundle, tmp_path, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from app.raw_archive.read_only_box import CatalogReadOnlyBox
    from app.tiered_archive import registry_v5
    from app.tiered_archive.storage import Store
    from tests.test_tiered_archive import AT, verified

    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    with ledger.db:
        ledger.db.execute(registry_v5.DDL["sql_deletion_pilot"])
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    readonly = object.__new__(CatalogReadOnlyBox)
    verifier = Store(readonly)
    monkeypatch.setattr(
        Store, "download", lambda self, key, path, size, **kw: box.download(key, path, size)
    )
    evidence = scope.verify_registry_snapshot(
        ledger, verifier, reference, environment="staging", checkpoint=lambda: None
    )
    assert evidence.ledger is ledger and evidence.reference_sha == codec.digest(reference)
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO sql_deletion_pilot VALUES(?,?,?)", ("d" * 32, candidate.identity, "e" * 64)
        )
    with pytest.raises(ArchiveBlocked, match="budget or intent"):
        scope.verify_registry_snapshot(
            ledger, verifier, reference, environment="staging", checkpoint=lambda: None
        )
    with pytest.raises(ArchiveBlocked, match="evidence required"):
        scope._registry_evidence(ledger, evidence)


def test_restored_scope_requires_exact_source_genesis_and_no_unresolved_budget(
    tmp_path, monkeypatch
):
    from app.tiered_archive import registry_v5

    candidate, adapter, ledger, kwargs, live, _ = setup_job(tmp_path)
    permit = deletion.activation(
        kwargs["authorization_path"], kwargs["authorization_sha256"], candidate, NOW
    )
    identity = source_identity()
    body = {
        **genesis(),
        "source_identity_sha256": audit.identity_anchor(identity),
        "runtime_sha256": kwargs["runtime_sha256"],
        "bindings_sha256": kwargs["bindings_sha256"],
    }
    ledger.db.execute(registry_v5.DDL["sql_deletion_epoch"])
    ledger.db.execute(
        "INSERT INTO sql_deletion_epoch VALUES(1,?,?)", (body["epoch"], audit.canonical(body))
    )
    ledger.db.execute("UPDATE sql_deletion_scope SET state='needs_reconcile'")
    ledger.db.commit()
    proof = SimpleNamespace(
        config={"source_identity": identity, "audit_schema_sha256": body["audit_schema_sha256"]}
    )
    monkeypatch.setattr(
        scope, "_preflight", lambda *_a, **_k: (permit, ledger.get(candidate.identity))
    )
    monkeypatch.setattr(deletion, "_begin_guard", lambda *_a: None)
    monkeypatch.setattr(deletion, "_guard", lambda *_a, **_k: None)
    monkeypatch.setattr(deletion, "_guard_deadline", lambda *_a: None)
    monkeypatch.setattr(audit, "inspect", lambda *_a: audit.identity_anchor(identity))
    record = (audit.event_id(body), body["epoch"], "genesis", audit.canonical(body), "111")
    source_rows = []

    class DB:
        def execute(self, statement, *_a, **_k):
            return SimpleNamespace(
                all=lambda: []
                if "greenmind_sql_deletion_row_audit_v1" in str(statement)
                else list(source_rows)
            )

    @contextmanager
    def exclusive(_candidate):
        adapter.connection = DB()
        try:
            yield
        finally:
            adapter.connection = None

    adapter.exclusive = exclusive
    evidence = scope.RegistryEvidence(
        scope._REGISTRY_TOKEN, ledger, codec.digest(scope.inventory(ledger)), "d" * 64
    )
    params = {**kwargs, "proof_guard": proof, "registry_evidence": evidence}
    with pytest.raises(ArchiveBlocked, match="genesis"):
        scope.reconcile(candidate, adapter, ledger, **params)
    assert ledger.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    source_rows.append(record)
    assert scope.reconcile(candidate, adapter, ledger, **params)["status"] == "SQL_SCOPE_CURRENT"
    assert len(live) == candidate.rows
    assert ledger.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == ("current",)
    ledger.close()


@pytest.mark.parametrize("fault", [None, "grant", "definer", "capture", "immutable"])
def test_row_audit_requires_owner_only_trigger_and_no_writer_insert(fault):
    columns = [
        ("event_id", "text", True),
        ("transaction_id", "bigint", True),
        ("row_sha256", "text", True),
    ]
    props = {
        "relkind": "r",
        "relpersistence": "p",
        "relrowsecurity": False,
        "relforcerowsecurity": False,
        "owner_member": False,
        "can_select": True,
        "can_mutate": fault == "grant",
        "inherited": False,
        "exact_primary_key": True,
    }
    function = {
        "body": audit.ROW_BODY,
        "security_definer": fault != "definer",
        "volatility": "v",
        "config": ["search_path=pg_catalog"],
        "language": "plpgsql",
        "table_owner": True,
        "owner_member": False,
    }
    capture = {
        "tgtype": 9,
        "tgenabled": "O" if fault == "capture" else "A",
        "tgnargs": 0,
        "unconditional": True,
        "unconstrained": True,
        "attributes": "",
        "exact_function": True,
    }
    triggers = [
        {
            "tgname": "gm_sql_audit_immutable_row",
            "tgtype": 27,
            "tgenabled": "O" if fault == "immutable" else "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
        {
            "tgname": "gm_sql_audit_immutable_truncate",
            "tgtype": 34,
            "tgenabled": "A",
            "tgnargs": 0,
            "unconditional": True,
            "exact_function": True,
        },
    ]

    class Rows:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def one(self):
            return self.value

        def all(self):
            return self.value

    class DB:
        def execute(self, statement, _params=None):
            sql = str(statement)
            if "FROM pg_catalog.pg_attribute" in sql:
                return Rows(columns)
            if "FROM pg_catalog.pg_class c" in sql:
                return Rows(props)
            if "FROM pg_catalog.pg_proc p" in sql:
                return Rows(function)
            if "tgname='gm_sql_capture_deleted_row'" in sql:
                return Rows(capture)
            if "FROM pg_catalog.pg_trigger t" in sql:
                return Rows(triggers)
            raise AssertionError(sql)

    if fault is None:
        audit.inspect_row_capture(DB())
    else:
        with pytest.raises(ArchiveBlocked):
            audit.inspect_row_capture(DB())


def test_row_trigger_proof_binds_transaction_and_exact_hashes():
    body = {"epoch": "a" * 32, "source_row_sha256": ["b" * 64, "c" * 64]}

    class DB:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _statement, params):
            assert params["txid"] == 123 and params["event"] == audit.event_id(body)
            return SimpleNamespace(all=lambda: self.rows)

    audit.verify_row_trigger(DB([("b" * 64,), ("c" * 64,)]), body, "123")
    with pytest.raises(ArchiveBlocked, match="row audit"):
        audit.verify_row_trigger(DB([("b" * 64,)]), body, "123")


def test_identity_anchor_survives_postmaster_restart_but_not_role_change():
    before = {**source_identity(), "postmaster_start": "first", "isolation": "read committed"}
    after = {**before, "postmaster_start": "second"}
    assert audit.identity_anchor(before) == audit.identity_anchor(after)
    assert audit.identity_anchor(after) != audit.identity_anchor({**after, "role_oid": 44})
