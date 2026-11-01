"""Evidence-bound SQL pilot scope creation and conservative restore reconciliation.

Only a real ProductionGuard, existing Archive->Source lease, live source fence,
immutable Source audit, exact catalog and separate restore/snapshot proofs can
move a scope. No operation here deletes user data or performs DDL.
"""

import json
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import text

from app.raw_archive.coordination import bound_job_lock
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import require_bound_source_lease

from . import codec, registry_v5
from . import sql_deletion as deletion
from . import sql_deletion_audit as audit
from .ledger import Ledger
from .source import Source


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _table(db, name):
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        is not None
    )


def inventory(ledger):
    """Digest real SQL pilot budget and relevant durable deletion intents."""
    db = ledger.db
    pilots = []
    if _table(db, "sql_deletion_pilot"):
        registry_v5.schema(db, "sql_deletion_pilot")
        pilots = [
            list(row)
            for row in db.execute(
                "SELECT pilot_id,candidate,authorization_sha256 FROM sql_deletion_pilot ORDER BY pilot_id LIMIT 10002"
            )
        ]
    need(len(pilots) <= 10000, "SQL pilot inventory exceeds bound")
    receipts = []
    rows = db.execute("SELECT id,dataset,receipt FROM bundle ORDER BY id LIMIT 100001")
    for number, (identity, dataset, raw) in enumerate(rows, 1):
        need(number <= 100000, "SQL scope ledger exceeds bounded inventory")
        try:
            item = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ArchiveBlocked("Invalid SQL scope ledger receipt") from exc
        need(
            type(item) is dict and item.get("candidate") == identity,
            "SQL scope receipt identity differs",
        )
        state = item.get("state")
        source = item.get("source")
        kind = source.get("kind") if type(source) is dict else None
        if state in {"deleting", "evicted"} and kind != "wav":
            receipts.append([identity, dataset, state, item.get("deletion_intent")])
        elif item.get("deletion_intent") is not None and kind != "wav":
            receipts.append([identity, dataset, state, item.get("deletion_intent")])
    return {"pilots": pilots, "receipts": receipts}


_REGISTRY_TOKEN = object()


class RegistryEvidence:
    def __init__(self, token, ledger, inventory_sha, reference_sha):
        need(token is _REGISTRY_TOKEN, "Use independently restored registry evidence")
        self.ledger, self.inventory_sha, self.reference_sha = ledger, inventory_sha, reference_sha


def verify_registry_snapshot(ledger, verifier, reference, *, environment, checkpoint):
    """Restore and compare the complete current catalog using separate read-only credentials."""
    from app.raw_archive.policy import checksum
    from app.raw_archive.read_only_box import CatalogReadOnlyBox

    from . import registry_backup
    from .storage import Store

    need(
        type(ledger) is Ledger
        and not ledger.read_only
        and type(verifier) is Store
        and type(verifier.transport) is CatalogReadOnlyBox
        and callable(checkpoint),
        "Independent SQL registry verifier required",
    )
    need(
        type(reference) is dict
        and set(reference) == {"key", "sha256", "bytes"}
        and type(reference["bytes"]) is int
        and 0 < reference["bytes"] <= 1024**2
        and registry_v5.sha(reference["sha256"]),
        "Pinned SQL registry reference required",
    )
    need(
        _table(ledger.db, "sql_deletion_pilot"), "Explicit persisted SQL budget inventory required"
    )
    before = inventory(ledger)
    before_epoch = (
        ledger.db.execute("SELECT id,epoch,genesis FROM sql_deletion_epoch ORDER BY id").fetchall()
        if _table(ledger.db, "sql_deletion_epoch")
        else []
    )
    with tempfile.TemporaryDirectory(prefix="sql-registry-proof-", dir=ledger.root) as folder:
        root = Path(folder)
        index_file = root / "index.json"
        checkpoint()
        verifier.download(reference["key"], index_file, reference["bytes"])
        need(
            index_file.stat().st_size == reference["bytes"]
            and checksum(index_file) == reference["sha256"],
            "Independent SQL registry root differs",
        )
        index = json.loads(index_file.read_bytes())
        need(
            index.get("schema") == 5
            and index.get("environment") == environment
            and index.get("journal_v5", {})
            .get("tables", {})
            .get("sql_deletion_pilot", {})
            .get("present")
            is True,
            "SQL budget not covered by authenticated registry",
        )
        restored = Ledger(root / "restored")
        try:
            registry_backup.restore(
                reference,
                restored,
                verifier,
                checkpoint=checkpoint,
                expected_environment=environment,
            )
            need(
                inventory(restored) == before,
                "Registry SQL budget or intent differs from current ledger",
            )
            restored_epoch = (
                restored.db.execute(
                    "SELECT id,epoch,genesis FROM sql_deletion_epoch ORDER BY id"
                ).fetchall()
                if _table(restored.db, "sql_deletion_epoch")
                else []
            )
            need(restored_epoch == before_epoch, "Registry SQL scope genesis differs")
        finally:
            restored.close()
    checkpoint()
    need(inventory(ledger) == before, "SQL ledger changed during independent registry restore")
    return RegistryEvidence(_REGISTRY_TOKEN, ledger, codec.digest(before), codec.digest(reference))


def _registry_evidence(ledger, evidence):
    need(
        type(evidence) is RegistryEvidence
        and evidence.ledger is ledger
        and evidence.inventory_sha == codec.digest(inventory(ledger))
        and registry_v5.sha(evidence.reference_sha),
        "Fresh actual SQL registry restore evidence required",
    )


def _preflight(
    candidate,
    adapter,
    ledger,
    *,
    authorization_path,
    authorization_sha256,
    runtime_sha256,
    bindings_sha256,
    checkpoint,
    clock,
    proof_guard,
):
    bound_job_lock()
    require_bound_source_lease()
    need(
        type(ledger) is Ledger
        and not ledger.read_only
        and not ledger.db.in_transaction
        and type(adapter) is Source
        and deletion._production_guard(proof_guard)
        and callable(checkpoint),
        "Native SQL scope authority required",
    )
    deletion._scope(candidate, adapter, checkpoint)
    now = deletion.utc(clock())
    permit = deletion.activation(authorization_path, authorization_sha256, candidate, now)
    need(
        permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256,
        "SQL scope runtime bindings differ",
    )
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict, "SQL scope candidate receipt required")
    deletion.eligibility(candidate, receipt, now)
    deletion._prepare_guard(proof_guard, candidate, receipt, now, permit)
    return permit, receipt


def bootstrap(
    candidate,
    adapter,
    ledger,
    *,
    authorization_path,
    authorization_sha256,
    runtime_sha256,
    bindings_sha256,
    checkpoint,
    clock,
    proof_guard,
    registry_evidence,
):
    """Create the first epoch only from real empty history plus a committed Source genesis."""
    scope_exists = _table(ledger.db, "sql_deletion_scope")
    state_before = (
        ledger.db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone()
        if scope_exists
        else None
    )
    need(not scope_exists or state_before is not None, "Existing SQL scope state is ambiguous")
    if state_before == ("current",):
        return reconcile(
            candidate,
            adapter,
            ledger,
            authorization_path=authorization_path,
            authorization_sha256=authorization_sha256,
            runtime_sha256=runtime_sha256,
            bindings_sha256=bindings_sha256,
            checkpoint=checkpoint,
            clock=clock,
            proof_guard=proof_guard,
            registry_evidence=registry_evidence,
        )
    permit, receipt = _preflight(
        candidate,
        adapter,
        ledger,
        authorization_path=authorization_path,
        authorization_sha256=authorization_sha256,
        runtime_sha256=runtime_sha256,
        bindings_sha256=bindings_sha256,
        checkpoint=checkpoint,
        clock=clock,
        proof_guard=proof_guard,
    )
    _registry_evidence(ledger, registry_evidence)
    db = ledger.db
    state = (
        db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone()
        if _table(db, "sql_deletion_scope")
        else None
    )
    need(
        state in {None, ("bootstrap_pending",)},
        "Existing or restored SQL scope requires reconciliation",
    )
    actual = inventory(ledger)
    need(
        not actual["pilots"] and not actual["receipts"],
        "Existing SQL pilot or deletion history prevents fresh genesis",
    )
    transition = (
        db.execute("SELECT body FROM sql_deletion_transition WHERE id=1").fetchone()
        if _table(db, "sql_deletion_transition")
        else None
    )
    identity = proof_guard.config["source_identity"]
    template = {
        "format": "greenmind.sql-scope-genesis.v1",
        "source_identity_sha256": audit.identity_anchor(identity),
        "audit_schema_sha256": proof_guard.config["audit_schema_sha256"],
        "origin_inventory_sha256": codec.digest(actual),
        "registry_reference_sha256": registry_evidence.reference_sha,
        "runtime_sha256": runtime_sha256,
        "bindings_sha256": bindings_sha256,
        "proof_sha256": permit["proof_contract_sha256"],
    }
    if transition:
        body = json.loads(transition[0])
        need(
            state == ("bootstrap_pending",)
            and type(body) is dict
            and {k: v for k, v in body.items() if k != "epoch"} == template
            and audit.EPOCH.fullmatch(body.get("epoch", "")),
            "SQL bootstrap transition differs",
        )
    else:
        need(
            state is None and not _table(db, "sql_deletion_epoch"),
            "SQL genesis provenance ambiguous",
        )
        body = {**template, "epoch": uuid.uuid4().hex}
        with db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sql_deletion_scope (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)"
            )
            db.execute("INSERT INTO sql_deletion_scope VALUES(1,'bootstrap_pending')")
            db.execute(
                "CREATE TABLE sql_deletion_transition (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)"
            )
            db.execute("INSERT INTO sql_deletion_transition VALUES(1,?)", (audit.canonical(body),))
    lock_key = deletion._lock_key(candidate)
    deletion._begin_guard(proof_guard)
    with adapter.exclusive(candidate):
        deletion._guard(
            candidate,
            adapter,
            receipt,
            permit,
            authorization_path,
            authorization_sha256,
            runtime_sha256,
            bindings_sha256,
            checkpoint,
            clock,
            proof_guard,
            lock_key,
            require_enabled=False,
        )
        source = adapter.connection
        need(
            audit.inspect(source, body["audit_schema_sha256"], identity)
            == body["source_identity_sha256"],
            "SQL genesis Source binding changed",
        )
        count = source.execute(
            text("SELECT count(*) FROM public.greenmind_sql_deletion_audit_v1")
        ).scalar_one()
        need(
            type(count) is int and count in (0, 1),
            "Existing Source audit history prevents bootstrap",
        )
        row_count = source.execute(
            text("SELECT count(*) FROM public.greenmind_sql_deletion_row_audit_v1")
        ).scalar_one()
        need(row_count == 0, "Existing Source deleted-row audit prevents bootstrap")
        if count == 0:
            audit.insert(source, "genesis", body)
        else:
            audit.read(source, "genesis", body)
        checkpoint()
        deletion._guard_deadline(proof_guard)
    checkpoint()
    need(inventory(ledger) == actual, "SQL pilot inventory changed during genesis")
    with db:
        db.execute(registry_v5.DDL["sql_deletion_epoch"])
        db.execute(
            "INSERT INTO sql_deletion_epoch VALUES(1,?,?)", (body["epoch"], audit.canonical(body))
        )
        db.execute(
            "UPDATE sql_deletion_scope SET state='current' WHERE id=1 AND state='bootstrap_pending'"
        )
        db.execute("DROP TABLE sql_deletion_transition")
    ledger.publish_read_view()
    return {
        "status": "SQL_SCOPE_CURRENT",
        "epoch": body["epoch"],
        "genesis_sha256": audit.event_id(body),
        "deletion_performed": False,
    }


def current_epoch(ledger, proof_guard, runtime_sha256, bindings_sha256):
    db = ledger.db
    need(
        _table(db, "sql_deletion_scope")
        and db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone() == ("current",)
        and _table(db, "sql_deletion_epoch")
        and not _table(db, "sql_deletion_transition"),
        "SQL scope provenance requires reconciliation",
    )
    registry_v5.schema(db, "sql_deletion_epoch")
    row = db.execute("SELECT id,epoch,genesis FROM sql_deletion_epoch WHERE id=1").fetchone()
    need(row is not None, "SQL scope genesis missing")
    registry_v5.validate("sql_deletion_epoch", list(row), "production", ledger)
    body = json.loads(row[2])
    need(
        body["source_identity_sha256"]
        == audit.identity_anchor(proof_guard.config["source_identity"])
        and body["audit_schema_sha256"] == proof_guard.config["audit_schema_sha256"],
        "SQL scope Source identity or schema changed",
    )
    return body


def commit_body(genesis, candidate, permit, authorization_sha256, source_row_sha256):
    need(
        type(source_row_sha256) is list
        and len(source_row_sha256) == candidate.rows
        and source_row_sha256 == sorted(set(source_row_sha256))
        and all(type(value) is str and audit.HEX.fullmatch(value) for value in source_row_sha256),
        "Exact native Source row hashes required",
    )
    return {
        "format": "greenmind.sql-deletion-commit.v1",
        "epoch": genesis["epoch"],
        "pilot_id": permit["pilot_id"],
        "candidate": candidate.identity,
        "authorization_sha256": authorization_sha256,
        "source_content_sha256": candidate.content_sha256,
        "source_row_sha256": source_row_sha256,
        "rows": candidate.rows,
        "sql_policy_sha256": deletion.SQL_POLICY_SHA256,
        "runtime_sha256": permit["runtime_sha256"],
        "bindings_sha256": permit["bindings_sha256"],
        "proof_contract_sha256": permit["proof_contract_sha256"],
        "fence_contract_sha256": permit["fence_contract_sha256"],
    }


def verify_history(db, ledger, genesis, *, active_pilot=None, strict=False):
    """Compare every immutable Source event to actual persisted budget/intent."""
    state = inventory(ledger)
    pilots = {item[0]: item for item in state["pilots"]}
    need(
        len(pilots) == len(state["pilots"]) and len(pilots) <= 32,
        "SQL pilot history exceeds reconciliation bound",
    )
    events = db.execute(
        text(
            "SELECT event_id,epoch,kind,CASE WHEN pg_catalog.octet_length(body)<=8192 THEN body ELSE NULL END,transaction_id::text FROM public.greenmind_sql_deletion_audit_v1 ORDER BY event_id LIMIT 35"
        )
    ).all()
    need(0 < len(events) <= 33, "SQL Source audit history exceeds bound or lacks genesis")
    seen_genesis = False
    seen_pilots = set()
    seen_candidates = set()
    expected_row_events = set()
    txids = set()
    for event, epoch, kind, raw, txid in events:
        try:
            body = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ArchiveBlocked("SQL Source audit encoding differs") from exc
        need(
            type(body) is dict
            and audit.canonical(body) == raw
            and event == audit.event_id(body)
            and epoch == genesis["epoch"]
            and type(txid) is str
            and txid.isdecimal()
            and txid not in txids,
            "SQL Source audit event differs",
        )
        txids.add(txid)
        if kind == "genesis":
            need(not seen_genesis and body == genesis, "SQL Source genesis differs")
            seen_genesis = True
            continue
        need(
            kind == "commit" and body.get("format") == "greenmind.sql-deletion-commit.v1",
            "Unknown Source SQL audit event",
        )
        pilot = body.get("pilot_id")
        need(
            pilot in pilots
            and pilot not in seen_pilots
            and pilots[pilot][1:] == [body.get("candidate"), body.get("authorization_sha256")],
            "Source SQL audit lacks exact pilot budget",
        )
        receipt = ledger.get(body["candidate"])
        need(
            type(receipt) is dict
            and receipt.get("state") in ({"evicted"} if strict else {"deleting", "evicted"}),
            "SQL audit has unresolved ledger intent",
        )
        intent = receipt.get("deletion_intent")
        need(
            type(intent) is dict
            and intent.get("pilot_id") == pilot
            and intent.get("authorization_sha256") == pilots[pilot][2]
            and intent.get("candidate") == body["candidate"],
            "SQL Source audit intent differs",
        )
        from .runtime import candidate_from_json

        candidate = candidate_from_json(receipt["source"])
        permit = {
            key: intent[key]
            for key in (
                "pilot_id",
                "runtime_sha256",
                "bindings_sha256",
                "proof_contract_sha256",
                "fence_contract_sha256",
            )
        }
        need(
            body
            == commit_body(
                genesis, candidate, permit, pilots[pilot][2], intent.get("source_row_sha256")
            ),
            "SQL Source audit content differs",
        )
        expected_row_events.update((event, txid, row_sha) for row_sha in body["source_row_sha256"])
        seen_pilots.add(pilot)
        seen_candidates.add(body["candidate"])
    need(seen_genesis, "SQL Source genesis absent")
    row_events = db.execute(
        text(
            "SELECT event_id,transaction_id::text,row_sha256 FROM public.greenmind_sql_deletion_row_audit_v1 ORDER BY event_id,transaction_id,row_sha256 LIMIT 1025"
        )
    ).all()
    actual_row_events = [tuple(row) for row in row_events]
    need(
        len(actual_row_events) <= 1024
        and len(actual_row_events) == len(expected_row_events)
        and set(actual_row_events) == expected_row_events,
        "Source deleted-row audit history differs",
    )
    for pilot in pilots:
        if pilot not in seen_pilots:
            need(not strict and pilot == active_pilot, "Unresolved or lost SQL pilot history")
    for candidate_id, _dataset, status, intent in state["receipts"]:
        if candidate_id not in seen_candidates:
            need(
                not strict
                and status == "deleting"
                and type(intent) is dict
                and intent.get("pilot_id") == active_pilot
                and active_pilot in pilots
                and pilots[active_pilot][1] == candidate_id,
                "SQL deletion receipt lacks immutable Source audit",
            )
        elif strict:
            need(status == "evicted", "Unresolved SQL deletion intent")
    return {
        "pilots": len(pilots),
        "commits": len(seen_pilots),
        "source_history_sha256": codec.digest(
            {
                "events": [list(row) for row in events],
                "rows": [list(row) for row in actual_row_events],
            }
        ),
    }


def reconcile(
    candidate,
    adapter,
    ledger,
    *,
    authorization_path,
    authorization_sha256,
    runtime_sha256,
    bindings_sha256,
    checkpoint,
    clock,
    proof_guard,
    registry_evidence,
):
    """Reconcile a restored epoch against the real immutable Source audit."""
    permit, receipt = _preflight(
        candidate,
        adapter,
        ledger,
        authorization_path=authorization_path,
        authorization_sha256=authorization_sha256,
        runtime_sha256=runtime_sha256,
        bindings_sha256=bindings_sha256,
        checkpoint=checkpoint,
        clock=clock,
        proof_guard=proof_guard,
    )
    _registry_evidence(ledger, registry_evidence)
    db = ledger.db
    need(
        _table(db, "sql_deletion_scope")
        and db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone()
        in {("needs_reconcile",), ("current",)}
        and _table(db, "sql_deletion_epoch")
        and not _table(db, "sql_deletion_transition"),
        "Restored SQL scope provenance absent or ambiguous",
    )
    registry_v5.schema(db, "sql_deletion_epoch")
    epoch = db.execute("SELECT id,epoch,genesis FROM sql_deletion_epoch WHERE id=1").fetchone()
    need(epoch is not None, "Restored SQL genesis missing")
    registry_v5.validate("sql_deletion_epoch", list(epoch), candidate.environment, ledger)
    genesis = json.loads(epoch[2])
    need(
        genesis["source_identity_sha256"]
        == audit.identity_anchor(proof_guard.config["source_identity"])
        and genesis["audit_schema_sha256"] == proof_guard.config["audit_schema_sha256"],
        "Restored SQL source identity or schema differs",
    )
    before = inventory(ledger)
    need(
        len(before["pilots"]) <= 1
        and (not before["pilots"] or before["pilots"][0][1] == candidate.identity),
        "Only one fully inspectable SQL pilot can be reconciled",
    )
    need(
        not any(item[2] == "deleting" for item in before["receipts"]),
        "Unresolved SQL deletion intent requires separate recovery",
    )
    originals = list(adapter.expected)
    deletion._begin_guard(proof_guard)
    try:
        with adapter.exclusive(candidate):
            absent = adapter.absent(candidate)
            if before["pilots"]:
                need(
                    absent and receipt.get("state") == "evicted",
                    "Audited SQL candidate reappeared or is unresolved",
                )
                adapter.expected = []
            else:
                need(not absent, "Current SQL candidate absent without pilot audit")
            deletion._guard(
                candidate,
                adapter,
                receipt,
                permit,
                authorization_path,
                authorization_sha256,
                runtime_sha256,
                bindings_sha256,
                checkpoint,
                clock,
                proof_guard,
                deletion._lock_key(candidate),
                require_enabled=False,
            )
            audit.inspect(
                adapter.connection,
                genesis["audit_schema_sha256"],
                proof_guard.config["source_identity"],
            )
            evidence = verify_history(adapter.connection, ledger, genesis, strict=True)
            checkpoint()
            deletion._guard_deadline(proof_guard)
    finally:
        adapter.expected = originals
    need(inventory(ledger) == before, "SQL pilot inventory changed during reconciliation")
    with db:
        db.execute(
            "UPDATE sql_deletion_scope SET state='current' WHERE id=1 AND state IN ('needs_reconcile','current')"
        )
    ledger.publish_read_view()
    return {
        "status": "SQL_SCOPE_CURRENT",
        "epoch": epoch[1],
        **evidence,
        "deletion_performed": False,
    }
