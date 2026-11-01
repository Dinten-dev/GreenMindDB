"""Separate, fail-closed V3 SQL pilot authorization and native Source checks.

This module does not schedule deletion. The full V3 runtime gate remains closed
until a native Source audit schema/grants restore verifier is implemented.
"""

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from app.raw_archive.coordination import bound_job_lock
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_lease import require_bound_source_lease

from . import codec, sql_audit_v3, sql_deletion
from .ledger import _private_read
from .policy import POLICY_SHA256, utc
from .source import Source


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def activation(path, expected_sha256, candidate, now):
    """Read a distinct V3 permit without invoking V1/V2 authorization."""
    need(type(expected_sha256) is str and sql_audit_v3.HEX64.fullmatch(expected_sha256), "Pinned V3 permit required")
    raw = _private_read(Path(path), 65536)
    need(hashlib.sha256(raw).hexdigest() == expected_sha256, "V3 permit changed")
    try:
        permit = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ArchiveBlocked("Invalid V3 permit") from exc
    keys = {"format", "policy", "sql_policy", "mode", "pilot_id", "environment", "candidate", "runtime_sha256", "bindings_sha256", "proof_contract_sha256", "fence_contract_sha256", "authorized_at", "expires_at", "acceptance", "audit"}
    need(type(permit) is dict and set(permit) == keys and permit["format"] == "greenmind.sql-deletion.v3" and permit["policy"] == POLICY_SHA256 and permit["sql_policy"] == sql_deletion.SQL_POLICY_SHA256 and permit["mode"] == "pilot" and permit["environment"] == candidate.environment and permit["candidate"] == candidate.identity and type(permit["pilot_id"]) is str and re.fullmatch(r"[a-f0-9]{32}", permit["pilot_id"]) and all(type(permit[k]) is str and sql_audit_v3.HEX64.fullmatch(permit[k]) for k in ("runtime_sha256", "bindings_sha256", "proof_contract_sha256", "fence_contract_sha256")) and utc(permit["authorized_at"]) <= now < utc(permit["expires_at"]), "Exact current V3 pilot permit required")
    accepted = permit["acceptance"]
    fields = {"sql_policy", "candidate", "runtime_sha256", "bindings_sha256", "proof_contract_sha256", "fence_contract_sha256", "deployed_at", "observation"}
    need(type(accepted) is dict and set(accepted) == fields and all(accepted[k] == permit[k] for k in fields - {"deployed_at", "observation"}), "Exact V3 acceptance binding required")
    obs = accepted["observation"]
    need(type(obs) is dict and set(obs) == {"started_at", "finished_at", "maximum_gap_seconds", "samples", "errors", "progress"} and utc(accepted["deployed_at"]) <= utc(obs["started_at"]) and timedelta(hours=24) <= utc(obs["finished_at"]) - utc(obs["started_at"]) and utc(obs["finished_at"]) <= now and type(obs["maximum_gap_seconds"]) is int and 0 <= obs["maximum_gap_seconds"] <= 600 and type(obs["samples"]) is int and obs["samples"] >= 280 and type(obs["errors"]) is int and obs["errors"] == 0 and type(obs["progress"]) is dict and obs["progress"] == {"gateway": True, "direct": True}, "V3 24-hour observation required")
    audit = permit["audit"]
    need(type(audit) is dict and set(audit) == {"epoch", "genesis_sha256", "schema_sha256", "slot", "pk_sha256", "source_row_sha256"} and type(audit["epoch"]) is str and sql_audit_v3.HEX32.fullmatch(audit["epoch"]) and all(type(audit[k]) is str and sql_audit_v3.HEX64.fullmatch(audit[k]) for k in ("genesis_sha256", "schema_sha256", "pk_sha256")) and type(audit["slot"]) is int and 1 <= audit["slot"] <= 128, "Exact V3 generation and slot required")
    hashes = audit["source_row_sha256"]
    need(type(hashes) is list and len(hashes) == candidate.rows and 0 < len(hashes) <= 32 and all(type(h) is str and sql_audit_v3.HEX64.fullmatch(h) for h in hashes) and hashes == sorted(set(hashes)), "Exact V3 native Source row hashes required")
    return permit



def approval_body(permit, authorization_sha256):
    """Exact public binding installed by Source DBA for this one pilot."""
    return json.dumps({
        "format": "greenmind.sql-audit-operation.v3",
        "pilot_id": permit["pilot_id"],
        "candidate": permit["candidate"],
        "authorization_sha256": authorization_sha256,
        "runtime_sha256": permit["runtime_sha256"],
        "bindings_sha256": permit["bindings_sha256"],
        "proof_contract_sha256": permit["proof_contract_sha256"],
        "fence_contract_sha256": permit["fence_contract_sha256"],
    }, sort_keys=True, separators=(",", ":"))

def native_approval(db, candidate, adapter, permit, authorization_sha256, expected_identity, *, checkpoint, arm):
    """Check the real Source generation, rows, locks and DBA slot, then optionally arm.

    Caller must already hold the actual complete proof guard and Source fence.
    This helper alone never authorizes a DELETE.
    """
    from .sql_deletion_audit import native_row_hashes
    from .sql_deletion_guard import _identity

    bound_job_lock()
    require_bound_source_lease()
    need(type(adapter) is Source and db is adapter.connection and db.dialect.name == "postgresql" and candidate.source == "gateway" and candidate.dataset == "ingest_log" and candidate.kind == "logs", "Exact live V3 Source transaction required")
    actual = _identity(db)
    need(actual == expected_identity and actual["restricted_role"] is True and actual["replication_role"] == "origin" and actual["read_only"] == "off" and actual["role"] == actual["session_role"], "V3 Source writer identity differs")
    pins = permit["audit"]
    need(sql_audit_v3.schema_sha256(db) == pins["schema_sha256"], "V3 Source audit schema differs")
    sql_audit_v3.inspect_writer(db, actual["role"], actual["role_oid"])
    need(db.execute(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": 0x474D53514C415544}).scalar_one() is True, "V3 Source audit fence unavailable")
    generation = db.execute(text("SELECT epoch,genesis_sha,writer_oid::bigint FROM public.gm_sql_v3_generation WHERE singleton")).one_or_none()
    need(generation is not None and tuple(generation) == (pins["epoch"], pins["genesis_sha256"], actual["role_oid"]), "V3 Source generation differs")
    need(db.execute(text("SELECT count(*) FROM public.gm_sql_v3_unknown")).scalar_one() == 0, "Unknown Source DELETE holds V3 scope")
    rows = list(adapter.expected)
    need(len(rows) == candidate.rows, "V3 Source expected rows differ")
    spec = sql_deletion.DATASETS[candidate.dataset]
    pk = [{k: item["body"]["row"][k] for k in spec.keys} for item in rows]
    need(codec.digest(pk) == pins["pk_sha256"], "V3 primary-key scope differs")
    hashes = native_row_hashes(db, candidate, rows, checkpoint)
    need(hashes == pins["source_row_sha256"], "V3 native Source rows differ")
    approval = db.execute(text("""SELECT epoch,writer_oid::bigint,candidate_sha,tenant_sha,pk_sha,permit_sha,body,expected,
        clock_timestamp() >= start_at AND clock_timestamp() < expires_at FROM public.gm_sql_v3_approval WHERE slot=:slot"""), {"slot": pins["slot"]}).one_or_none()
    tenant = hashlib.sha256(candidate.organization.encode()).hexdigest()
    need(approval is not None and tuple(approval) == (pins["epoch"], actual["role_oid"], candidate.identity, tenant, pins["pk_sha256"], authorization_sha256, approval_body(permit, authorization_sha256), hashes, True), "V3 DBA approval differs")
    existing = db.execute(text("SELECT count(*) FROM public.gm_sql_v3_event WHERE slot=:slot"), {"slot": pins["slot"]}).scalar_one()
    need(existing == 0, "V3 slot already committed or unresolved")
    if arm:
        checkpoint()
        db.execute(text("SELECT public.gm_sql_v3_arm(CAST(:slot AS smallint),:permit)"), {"slot": pins["slot"], "permit": authorization_sha256})
    return hashes


def verify_committed_rows(db, permit):
    pins = permit["audit"]
    actual = db.execute(text("SELECT hash FROM public.gm_sql_v3_row WHERE slot=:slot ORDER BY hash LIMIT 33"), {"slot": pins["slot"]}).scalars().all()
    need(actual == pins["source_row_sha256"], "V3 transaction audit rows differ")
    return actual


def require_native_restore_evidence():
    """No fabricated readiness while full schema/grant restore is unavailable."""
    raise ArchiveBlocked("V3 native Source audit schema/grants restore provenance unavailable")


def verify_history(db, ledger, epoch, *, active_permit=None, authorization_sha256=None):
    """Reconcile bounded real Source events against the durable one-pilot journal."""
    need(type(epoch) is str and sql_audit_v3.HEX32.fullmatch(epoch), "Exact V3 epoch required")
    need(db.execute(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": 0x474D53514C415544}).scalar_one() is True, "V3 audit fence busy")
    need(db.execute(text("SELECT count(*) FROM public.gm_sql_v3_unknown")).scalar_one() == 0, "Unknown Source DELETE holds V3 history")
    pilots = ledger.db.execute("SELECT pilot_id,candidate,authorization_sha256 FROM sql_deletion_pilot ORDER BY pilot_id LIMIT 2").fetchall()
    need(len(pilots) <= 1, "V3 pilot budget ambiguous")
    approvals = db.execute(text("SELECT slot,epoch,candidate_sha,permit_sha,body,expected FROM public.gm_sql_v3_approval ORDER BY slot LIMIT 129")).all()
    events = db.execute(text("SELECT slot,txid::text FROM public.gm_sql_v3_event ORDER BY slot LIMIT 129")).all()
    rows = db.execute(text("SELECT slot,hash FROM public.gm_sql_v3_row ORDER BY slot,hash LIMIT 4097")).all()
    need(len(approvals) <= 128 and len(events) <= 128 and len(rows) <= 4096, "V3 audit history exceeds capacity")
    by_slot = {int(slot): txid for slot, txid in events}
    need(len(by_slot) == len(events) and all(str(txid).isdecimal() and int(txid) > 0 for txid in by_slot.values()), "V3 event transaction differs")
    row_sets = {}
    for slot, digest in rows:
        need(slot in by_slot and type(digest) is str and sql_audit_v3.HEX64.fullmatch(digest), "V3 orphan Source row audit")
        row_sets.setdefault(slot, []).append(digest)
    approval_slots = set()
    for slot, item_epoch, candidate, auth, body, expected in approvals:
        need(slot not in approval_slots and item_epoch == epoch and len(pilots) == 1, "V3 Source approval provenance differs")
        approval_slots.add(slot)
        pilot, stored_candidate, stored_auth = pilots[0]
        need(candidate == stored_candidate and auth == stored_auth, "V3 Source pilot budget differs")
        receipt = ledger.get(candidate)
        need(type(receipt) is dict, "V3 pilot receipt absent")
        intent = receipt.get("deletion_intent")
        if type(intent) is dict:
            need(intent.get("pilot_id") == pilot and intent.get("authorization_sha256") == auth and intent.get("audit_v3_slot") == slot and intent.get("audit_v3_epoch") == epoch and intent.get("audit_v3_body") == body and intent.get("source_row_sha256") == sorted(expected), "V3 durable intent differs")
        else:
            need(type(active_permit) is dict and active_permit["pilot_id"] == pilot and active_permit["candidate"] == candidate and active_permit["audit"]["slot"] == slot and active_permit["audit"]["epoch"] == epoch and active_permit["audit"]["source_row_sha256"] == sorted(expected) and authorization_sha256 == auth and approval_body(active_permit, auth) == body and slot not in by_slot, "V3 pending approval has no durable intent")
        if slot in by_slot:
            need(type(intent) is dict and receipt.get("state") in {"deleting", "evicted"} and row_sets.get(slot, []) == sorted(expected), "V3 committed Source audit differs")
        else:
            need(receipt.get("state") in {"verified", "deleting"}, "V3 event absent for settled pilot")
    need(set(by_slot) <= approval_slots and all(slot in approval_slots for slot in row_sets), "V3 orphan audit event")
    need(not pilots or len(approvals) == 1 or (type(active_permit) is dict and len(approvals) == 0), "V3 pilot history lost")
    return {"format": "greenmind.sql-audit-history.v3", "approvals": len(approvals), "commits": len(events), "history_sha256": codec.digest({"approvals": [list(x) for x in approvals], "events": [list(x) for x in events], "rows": [list(x) for x in rows]})}


def _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key):
    """V3-native permit checks plus the real production proof/fence guard."""
    from .policy import eligibility
    from .sql_deletion_guard import ProductionGuardV3

    need(type(proof_guard) is ProductionGuardV3, "Exact V3 production guard required")
    checkpoint()
    sql_deletion._enabled()
    now = utc(clock())
    need(activation(authorization_path, authorization_sha256, candidate, now) == permit and permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256, "V3 authorization changed")
    eligibility(candidate, receipt, now)
    db = adapter.connection
    need(db is not None and db.dialect.name == "postgresql" and db.execute(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": lock_key}).scalar_one() is True, "V3 full partition fence unavailable")
    txid = db.execute(text("SELECT pg_catalog.txid_current()::text")).scalar_one()
    evidence = proof_guard(candidate, receipt, now, db, lock_key, permit)
    need(type(evidence) is dict and set(evidence) == {"candidate", "transaction_id", "fence_key", "proof_contract_sha256", "fence_contract_sha256", "checks"} and evidence["candidate"] == candidate.identity and evidence["transaction_id"] == txid and evidence["fence_key"] == lock_key and evidence["proof_contract_sha256"] == permit["proof_contract_sha256"] and evidence["fence_contract_sha256"] == permit["fence_contract_sha256"] and type(evidence["checks"]) is dict and set(evidence["checks"]) == sql_deletion.REQUIRED_PROOFS and all(value is True for value in evidence["checks"].values()), "V3 live production proof guard rejected")
    if adapter.expected:
        need(adapter.inspect(candidate) == candidate, "V3 Source rows changed")
    checkpoint()
    need(activation(authorization_path, authorization_sha256, candidate, utc(clock())) == permit, "V3 authorization expired")
    proof_guard.check_deadline()


def _budget(ledger, candidate, permit, authorization_sha256):
    """Never bootstrap or reset V3 scope or the permanent one-pilot budget."""
    need(not ledger.read_only and not ledger.db.in_transaction, "Writable idle V3 journal required")
    table = ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_scope'").fetchone()
    need(table is not None, "V3 audit scope provenance unavailable")
    from . import registry_v5

    registry_v5.schema(ledger.db, "sql_audit_v3_scope")
    scope = ledger.db.execute("SELECT epoch,genesis_sha256,state FROM sql_audit_v3_scope WHERE id=1").fetchone()
    need(scope == (permit["audit"]["epoch"], permit["audit"]["genesis_sha256"], "current"), "V3 audit scope requires native restore reconciliation")
    registry_v5.validate("sql_audit_v3_scope", [1, *scope], candidate.environment, ledger)
    with ledger.db:
        ledger.db.execute("CREATE TABLE IF NOT EXISTS sql_deletion_pilot (pilot_id TEXT PRIMARY KEY, candidate TEXT NOT NULL, authorization_sha256 TEXT NOT NULL)")
        existing = ledger.db.execute("SELECT pilot_id,candidate,authorization_sha256 FROM sql_deletion_pilot ORDER BY pilot_id LIMIT 2").fetchall()
        expected = (permit["pilot_id"], candidate.identity, authorization_sha256)
        need(not existing or existing == [expected], "V3 pilot budget already consumed")
        if not existing:
            ledger.db.execute("INSERT INTO sql_deletion_pilot VALUES (?,?,?)", expected)


def evict(candidate, adapter, ledger, *, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard):
    """One V3 transaction; currently held before DML by missing native restore proof."""
    from sqlalchemy import and_, delete

    from .policy import eligibility
    from .sql_deletion_guard import ProductionGuardV3

    need(type(proof_guard) is ProductionGuardV3 and type(adapter) is Source and callable(checkpoint) and callable(clock), "Real V3 production adapters required")
    bound_job_lock()
    require_bound_source_lease()
    sql_deletion._enabled()
    spec = sql_deletion._scope(candidate, adapter, checkpoint)
    need(candidate.source == "gateway" and candidate.dataset == "ingest_log" and candidate.kind == "logs", "V3 pilot Source scope differs")
    permit = activation(authorization_path, authorization_sha256, candidate, utc(clock()))
    need(permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256 and permit["audit"]["schema_sha256"] == proof_guard.config["audit_schema_sha256"] and permit["audit"]["epoch"] == proof_guard.config["audit_v3_epoch"] and permit["audit"]["genesis_sha256"] == proof_guard.config["audit_v3_genesis_sha256"], "V3 proof contract differs")
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict, "V3 verified candidate receipt required")
    eligibility(candidate, receipt, utc(clock()))
    # V3 guard never treats missing audit restore evidence as a positive proof.
    proof_guard.prepare(candidate, receipt, utc(clock()), permit)
    _budget(ledger, candidate, permit, authorization_sha256)
    lock_key = sql_deletion._lock_key(candidate)
    originals = list(adapter.expected)
    try:
        proof_guard.begin_source()
        with adapter.exclusive(candidate):
            receipt = ledger.get(candidate.identity)
            need(type(receipt) is dict, "V3 receipt disappeared")
            absent = adapter.absent(candidate)
            if absent:
                need(receipt.get("state") in {"deleting", "evicted"}, "V3 source absent without intent")
                adapter.expected = []
            _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key)
            db = adapter.connection
            need(sql_audit_v3.schema_sha256(db) == permit["audit"]["schema_sha256"], "V3 Source schema pin changed")
            history = verify_history(db, ledger, permit["audit"]["epoch"], active_permit=permit, authorization_sha256=authorization_sha256)
            if absent:
                need(history["commits"] == 1 and receipt.get("state") in {"deleting", "evicted"}, "V3 absent source lacks exact committed audit")
            else:
                adapter.expected = originals
                native_approval(db, candidate, adapter, permit, authorization_sha256, proof_guard.config["source_identity"], checkpoint=checkpoint, arm=False)
                if receipt["state"] == "verified":
                    intent = {"sql_policy": sql_deletion.SQL_POLICY_SHA256, "authorization_sha256": authorization_sha256, "candidate": candidate.identity, "pilot_id": permit["pilot_id"], "runtime_sha256": runtime_sha256, "bindings_sha256": bindings_sha256, "proof_contract_sha256": permit["proof_contract_sha256"], "fence_contract_sha256": permit["fence_contract_sha256"], "created_at": utc(clock()).isoformat(), "source_row_sha256": permit["audit"]["source_row_sha256"], "audit_v3_slot": permit["audit"]["slot"], "audit_v3_epoch": permit["audit"]["epoch"], "audit_v3_body": approval_body(permit, authorization_sha256)}
                    receipt = ledger.save(candidate, "deleting", {**receipt, "deletion_intent": intent}, utc(clock()))
                else:
                    need(receipt["state"] == "deleting", "V3 deletion intent state differs")
                verify_history(db, ledger, permit["audit"]["epoch"], active_permit=permit, authorization_sha256=authorization_sha256)
                native_approval(db, candidate, adapter, permit, authorization_sha256, proof_guard.config["source_identity"], checkpoint=checkpoint, arm=True)
                for item in originals:
                    _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key)
                    table = adapter.table(spec.table)
                    row = item["body"]["row"]
                    condition = and_(*(table.c[key] == row[key] for key in spec.keys))
                    need(db.execute(delete(table).where(condition)).rowcount == 1, "V3 exact row deletion differs")
                    adapter.expected = adapter.expected[1:]
                need(adapter.absent(candidate), "V3 Source partition remains")
                verify_committed_rows(db, permit)
                history = verify_history(db, ledger, permit["audit"]["epoch"], active_permit=permit, authorization_sha256=authorization_sha256)
                need(history["commits"] == 1, "V3 transaction audit incomplete")
                _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key)
                checkpoint()
        return ledger.save(candidate, "evicted", receipt, utc(clock()))
    finally:
        adapter.expected = originals
