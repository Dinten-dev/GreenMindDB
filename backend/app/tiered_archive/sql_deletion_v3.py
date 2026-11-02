"""Separate, fail-closed V3 SQL pilot authorization and native Source checks.

This module does not schedule deletion. The full V3 runtime gate remains closed
until a native Source audit schema/grants restore verifier is implemented.
"""

import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
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


def require_native_restore_evidence(guard=None, candidate=None, *, checkpoint=None):
    """Re-read actual V3 backup and inspect a separate marked PostgreSQL restore."""
    if guard is None or candidate is None or not callable(checkpoint):
        raise ArchiveBlocked("V3 native Source audit schema/grants restore provenance unavailable")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from app.raw_archive.read_only_box import CatalogReadOnlyBox, configured_catalog_read_only_box

    from . import backup, registry_v5, runtime, sql_audit_v3_backup
    from .recovery import restore_url
    from .scientific_registry_import import _provider_proof, _transport
    from .sql_deletion_guard import ProductionGuardV3, _private_json, _snapshot_reference
    from .sql_runtime import _private_file
    from .storage import Store

    need(type(guard) is ProductionGuardV3 and guard.candidate == candidate and os.geteuid() == 0,
         "Real root V3 restore guard required")
    config = guard.config
    need(type(config.get("audit_v3_restore_evidence_path")) is str
         and Path(config["audit_v3_restore_evidence_path"]).is_absolute()
         and type(config.get("audit_v3_restore_evidence_sha256")) is str
         and sql_audit_v3.HEX64.fullmatch(config["audit_v3_restore_evidence_sha256"]),
         "V3 native Source audit schema/grants restore provenance unavailable")
    evidence = _private_json(config["audit_v3_restore_evidence_path"], config["audit_v3_restore_evidence_sha256"])
    fields = {"format", "backup_candidate", "backup_manifest_sha256", "key_file", "key_sha256",
              "target_profile_file", "target_profile_sha256", "target", "target_writer_role",
              "provider_proof_file", "provider_proof_sha256", "provider_terminal_file", "provider_terminal_sha256"}
    need(type(evidence) is dict and set(evidence) == fields and evidence["format"] == "greenmind.sql-audit-v3-native-restore-input.v1"
         and evidence["backup_candidate"] == config["audit_v3_backup_candidate"]
         and evidence["backup_manifest_sha256"] == config["audit_v3_backup_manifest_sha256"],
         "Pinned V3 native restore inputs required")
    for field in ("key_sha256", "target_profile_sha256", "provider_proof_sha256", "provider_terminal_sha256"):
        need(type(evidence[field]) is str and sql_audit_v3.HEX64.fullmatch(evidence[field]), "Exact V3 restore input SHA required")
    raw_key = _private_file(evidence["key_file"], evidence["key_sha256"], 32)
    key = backup.key_from_file(Path(evidence["key_file"]))
    need(raw_key == key, "V3 restore key generation changed")
    target_url = restore_url(_private_file(evidence["target_profile_file"], evidence["target_profile_sha256"], 8192).decode().strip())
    target = evidence["target"]
    need(target_url.database != config["source_identity"]["database"], "Isolated V3 restore database required")
    receipt = guard.ledger.get(evidence["backup_candidate"])
    now = datetime.now(UTC)
    need(type(receipt) is dict and receipt.get("state") == "verified" and receipt.get("destination") == guard.store.identity
         and receipt.get("manifest", {}).get("sha256") == evidence["backup_manifest_sha256"]
         and now - timedelta(hours=24) <= utc(receipt.get("verified_at")) <= now,
         "Current verified V3 Gateway backup required")
    backup_candidate = runtime.candidate_from_json(receipt["source"])
    need(backup_candidate.identity == evidence["backup_candidate"] and backup_candidate.environment == candidate.environment,
         "V3 Gateway backup identity differs")
    table = guard.ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_backup'").fetchone()
    need(table is not None, "V3 independent backup provenance unavailable")
    registry_v5.schema(guard.ledger.db, "sql_audit_v3_backup")
    refs = receipt["files"]
    need(len(refs) == 6, "Full V3 backup inventory required")
    row = guard.ledger.db.execute("SELECT epoch,candidate,gateway_manifest_sha256,audit_sha256,plaintext_sha256 FROM sql_audit_v3_backup WHERE epoch=?", (config["audit_v3_epoch"],)).fetchone()
    need(row is not None and tuple(row) == (config["audit_v3_epoch"], backup_candidate.identity, evidence["backup_manifest_sha256"], refs[5]["sha256"], refs[5]["plaintext_sha256"]),
         "V3 independent backup journal differs")
    readonly = configured_catalog_read_only_box()
    need(type(readonly) is CatalogReadOnlyBox, "Independent V3 read-only verifier required")
    try:
        verifier = Store(readonly)
        _transport(Store(guard.store.transport), verifier)
        _provider_proof(evidence["provider_proof_file"], evidence["provider_proof_sha256"], evidence["provider_terminal_file"], evidence["provider_terminal_sha256"])
        # Snapshot paths use the existing separately authorized operator reader;
        # immutable audit objects below still use the independent RO account.
        guard._snapshot_lock(receipt["snapshot"])
        _snapshot_reference(guard.store, guard.provider, guard.ledger, receipt, checkpoint)
        readback, payload = sql_audit_v3_backup.verify_gateway_bundle(backup_candidate, guard.ledger, verifier, key, checkpoint=checkpoint, _include_payload=True)
        need(not readback["unknown"] and payload["data"]["generation"][0]["epoch"] == config["audit_v3_epoch"]
             and payload["data"]["generation"][0]["genesis_sha"] == config["audit_v3_genesis_sha256"]
             and payload["schema_sha256"] == config["audit_schema_sha256"], "V3 audit generation or schema differs")
        source = config["source_identity"]
        need(all(payload["source_identity"].get(k) == source.get(k) and source.get(k) is not None
                 for k in ("database", "database_oid", "server_address", "server_port", "postmaster_start")),
             "V3 backup physical Source identity differs")
        engine = create_engine(target_url, poolclass=NullPool, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as db:
                physical = db.execute(text("SELECT inet_server_addr()::text, pg_postmaster_start_time()::text")).one()
                need(tuple(physical) != (source["server_address"], source["postmaster_start"]), "V3 restore cannot share Source PostgreSQL")
        finally:
            engine.dispose()
        result = sql_audit_v3_backup.verify_native_rows(target_url.render_as_string(hide_password=False), target, payload,
                                                        checkpoint=checkpoint, target_writer_role=evidence["target_writer_role"])
        need(result.get("native_rows_match") is True and result.get("target_schema_grants_match") is True,
             "Full native V3 audit restore differs")
        checkpoint()
        guard._snapshot_lock(receipt["snapshot"])
        need(guard.ledger.get(backup_candidate.identity) == receipt, "V3 backup receipt changed")
        return {"candidate": backup_candidate.identity, "gateway_manifest_sha256": evidence["backup_manifest_sha256"],
                "audit_plaintext_sha256": readback["audit_plaintext_sha256"], "snapshot_sha256": payload["snapshot_sha256"],
                "history": sql_audit_v3_backup.history_binding(payload), "native_schema_grants_rows_verified": True}
    finally:
        readonly.close()


def make_source_permit(candidate, permit, authorization_sha256, source_dba_identity, writer_identity, session, *, producer_sha256, seconds):
    """Bind an already-validated V3 approval to this live lease, never grant DML."""
    audit = permit["audit"]
    need(type(session) is str and sql_audit_v3.HEX32.fullmatch(session), "Live lease session required")
    need(type(source_dba_identity) is dict and source_dba_identity and type(producer_sha256) is str and sql_audit_v3.HEX64.fullmatch(producer_sha256), "Pinned Source producer and DBA identity required")
    need(type(seconds) is int and 3 <= seconds <= 35, "Bounded Source permit lifetime required")
    identity = writer_identity
    need(type(identity) is dict and type(identity.get("role")) is str and sql_audit_v3.NAME.fullmatch(identity["role"]) and type(identity.get("role_oid")) is int and identity["role_oid"] > 0, "Pinned Source writer identity required")
    now = datetime.now(UTC)
    return {"format": "greenmind.sql-audit-v3-source-permit.v1", "lease_session": session,
            "producer_sha256": producer_sha256, "source_identity": source_dba_identity,
            "schema_sha256": audit["schema_sha256"], "writer_role": identity["role"], "writer_oid": identity["role_oid"],
            "epoch": audit["epoch"], "slot": audit["slot"], "candidate_sha": candidate.identity,
            "tenant_sha": hashlib.sha256(candidate.organization.encode()).hexdigest(),
            "pk_sha": audit["pk_sha256"], "permit_sha": authorization_sha256,
            "body": approval_body(permit, authorization_sha256), "expected": audit["source_row_sha256"],
            "start_at": (now - timedelta(seconds=1)).isoformat(), "expires_at": (now + timedelta(seconds=seconds)).isoformat()}


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
        operation = sql_audit_v3.validate_operation_body(body, candidate, auth)
        need(operation["pilot_id"] == pilot, "V3 Source operation pilot differs")
        receipt = ledger.get(candidate)
        need(type(receipt) is dict, "V3 pilot receipt absent")
        intent = receipt.get("deletion_intent")
        if type(intent) is dict:
            need(intent.get("pilot_id") == pilot and intent.get("authorization_sha256") == auth and intent.get("audit_v3_slot") == slot and intent.get("audit_v3_epoch") == epoch and intent.get("audit_v3_body") == body and intent.get("source_row_sha256") == sorted(expected)
                 and all(intent.get(k) == operation[k] for k in ("runtime_sha256", "bindings_sha256", "proof_contract_sha256", "fence_contract_sha256")), "V3 durable intent differs")
        else:
            need(type(active_permit) is dict and active_permit["pilot_id"] == pilot and active_permit["candidate"] == candidate and active_permit["audit"]["slot"] == slot and active_permit["audit"]["epoch"] == epoch and active_permit["audit"]["source_row_sha256"] == sorted(expected) and authorization_sha256 == auth and approval_body(active_permit, auth) == body and slot not in by_slot, "V3 pending approval has no durable intent")
        if slot in by_slot:
            need(type(intent) is dict and receipt.get("state") in {"deleting", "evicted"} and row_sets.get(slot, []) == sorted(expected), "V3 committed Source audit differs")
        else:
            need(receipt.get("state") in {"verified", "deleting"}, "V3 event absent for settled pilot")
    need(set(by_slot) <= approval_slots and all(slot in approval_slots for slot in row_sets), "V3 orphan audit event")
    need(not pilots or len(approvals) == 1 or (type(active_permit) is dict and len(approvals) == 0), "V3 pilot history lost")
    return {"format": "greenmind.sql-audit-history.v3", "approvals": len(approvals), "commits": len(events), "history_sha256": codec.digest({"approvals": [list(x) for x in approvals], "events": [list(x) for x in events], "rows": [list(x) for x in rows]})}


def _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256, runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key, *, require_enabled=True):
    """V3-native permit checks plus the real production proof/fence guard."""
    from .policy import eligibility
    from .sql_deletion_guard import ProductionGuardV3

    need(type(proof_guard) is ProductionGuardV3, "Exact V3 production guard required")
    checkpoint()
    if require_enabled:
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
    need(ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_scope_proof'").fetchone() is not None,
         "V3 audit scope proof provenance unavailable")
    registry_v5.schema(ledger.db, "sql_audit_v3_scope_proof")
    proof = ledger.db.execute("SELECT id,epoch,body,body_sha256 FROM sql_audit_v3_scope_proof WHERE id=1").fetchone()
    need(proof is not None, "V3 audit scope proof provenance unavailable")
    registry_v5.validate("sql_audit_v3_scope_proof", list(proof), candidate.environment, ledger)
    need(proof[1] == permit["audit"]["epoch"], "V3 audit scope proof epoch differs")
    body = json.loads(proof[2])
    anchor = ledger.db.execute("SELECT candidate,gateway_manifest_sha256,plaintext_sha256 FROM sql_audit_v3_backup WHERE epoch=?", (proof[1],)).fetchone()
    need(anchor is not None and tuple(anchor) == (body["backup_candidate"], body["backup_manifest_sha256"], body["audit_plaintext_sha256"])
         and body["runtime_sha256"] == permit["runtime_sha256"] and body["bindings_sha256"] == permit["bindings_sha256"],
         "V3 scope proof is stale for current backup or runtime")
    with ledger.db:
        ledger.db.execute("CREATE TABLE IF NOT EXISTS sql_deletion_pilot (pilot_id TEXT PRIMARY KEY, candidate TEXT NOT NULL, authorization_sha256 TEXT NOT NULL)")
        existing = ledger.db.execute("SELECT pilot_id,candidate,authorization_sha256 FROM sql_deletion_pilot ORDER BY pilot_id LIMIT 2").fetchall()
        expected = (permit["pilot_id"], candidate.identity, authorization_sha256)
        need(not existing or existing == [expected], "V3 pilot budget already consumed")
        if not existing:
            ledger.db.execute("INSERT INTO sql_deletion_pilot VALUES (?,?,?)", expected)


def scope_transition(candidate, adapter, ledger, *, authorization_path, authorization_sha256,
                     runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard,
                     registry_evidence, bootstrap=False):
    """CAS one evidence-bound V3 scope while the real Source audit fence is held."""
    from . import registry_v5, sql_deletion_scope
    from .ledger import Ledger
    from .policy import eligibility
    from .sql_deletion_guard import ProductionGuardV3, _identity

    need(os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SQL_V3_SCOPE_ENABLED") == "true"
         and type(bootstrap) is bool and type(ledger) is Ledger and not ledger.read_only
         and not ledger.db.in_transaction and type(adapter) is Source and type(proof_guard) is ProductionGuardV3
         and callable(checkpoint) and callable(clock), "Explicit root V3 scope controller required")
    bound_job_lock()
    require_bound_source_lease()
    sql_deletion_scope.v3_registry_evidence(ledger, registry_evidence)
    db = ledger.db
    need(sql_deletion_scope._table(db, "sql_deletion_pilot"), "Persisted SQL pilot budget required")
    registry_v5.schema(db, "sql_deletion_pilot")
    need(not sql_deletion_scope._table(db, "sql_deletion_transition"), "Unresolved SQL scope transition")
    before = sql_deletion_scope.inventory(ledger)
    v3_before = sql_deletion_scope.v3_inventory(ledger)
    scope_info = v3_before["sql_audit_v3_scope"]
    proof_info = v3_before["sql_audit_v3_scope_proof"]
    if bootstrap:
        need(not scope_info["present"] and not proof_info["present"] and not before["pilots"] and not before["receipts"],
             "V3 bootstrap requires a known empty origin")
        previous_scope = previous_proof = None
    else:
        need(scope_info["present"] and proof_info["present"] and len(scope_info["rows"]) == len(proof_info["rows"]) == 1,
             "Restored V3 scope proof missing")
        previous_scope = db.execute("SELECT id,epoch,genesis_sha256,state FROM sql_audit_v3_scope WHERE id=1").fetchone()
        previous_proof = db.execute("SELECT id,epoch,body,body_sha256 FROM sql_audit_v3_scope_proof WHERE id=1").fetchone()
        need(previous_scope is not None and previous_scope[3] in {"current", "needs_reconcile"}, "V3 scope state is ambiguous")
        registry_v5.validate("sql_audit_v3_scope_proof", list(previous_proof), candidate.environment, ledger)
    sql_deletion._scope(candidate, adapter, checkpoint)
    now = utc(clock())
    permit = activation(authorization_path, authorization_sha256, candidate, now)
    pins = permit["audit"]
    need(permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256
         and pins["schema_sha256"] == proof_guard.config["audit_schema_sha256"]
         and pins["epoch"] == proof_guard.config["audit_v3_epoch"]
         and pins["genesis_sha256"] == proof_guard.config["audit_v3_genesis_sha256"], "V3 scope authorization differs")
    if previous_scope is not None:
        need(tuple(previous_scope[1:3]) == (pins["epoch"], pins["genesis_sha256"]), "V3 scope generation changed")
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict, "V3 scope candidate receipt required")
    eligibility(candidate, receipt, now)
    proof_guard.prepare(candidate, receipt, now, permit)
    restore = proof_guard._v3_restore
    need(type(restore) is dict and restore.get("native_schema_grants_rows_verified") is True
         and restore.get("candidate") == proof_guard.config["audit_v3_backup_candidate"]
         and restore.get("gateway_manifest_sha256") == proof_guard.config["audit_v3_backup_manifest_sha256"],
         "Actual independent V3 audit restore required")
    sql_deletion_scope.v3_registry_evidence(ledger, registry_evidence)
    lock_key = sql_deletion._lock_key(candidate)
    proof_guard.begin_source()
    committed = False
    try:
        with adapter.exclusive(candidate):
            _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256,
                   runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key, require_enabled=False)
            source_db = adapter.connection
            identity = _identity(source_db)
            need(identity == proof_guard.config["source_identity"] and sql_audit_v3.schema_sha256(source_db) == pins["schema_sha256"],
                 "Live V3 Source identity or schema changed")
            sql_audit_v3.inspect_writer(source_db, identity["role"], identity["role_oid"])
            generation = source_db.execute(text("SELECT epoch,genesis_sha,writer_oid::bigint FROM public.gm_sql_v3_generation WHERE singleton")).one_or_none()
            need(generation is not None and tuple(generation) == (pins["epoch"], pins["genesis_sha256"], identity["role_oid"]),
                 "Live V3 Source generation differs")
            history = verify_history(source_db, ledger, pins["epoch"])
            expected_history = {k: history[k] for k in ("approvals", "commits", "history_sha256")}
            need(restore.get("history") == expected_history, "Current Source history differs from restored V3 snapshot")
            need(len(before["pilots"]) <= 1 and history["approvals"] == history["commits"] == len(before["pilots"]),
                 "V3 pilot approval or commit is unresolved")
            if before["pilots"]:
                need(len(before["receipts"]) == 1 and before["receipts"][0][0] == before["pilots"][0][1]
                     and before["receipts"][0][2] == "evicted", "Consumed V3 pilot receipt requires reconciliation")
            else:
                need(not before["receipts"], "Unexplained SQL deletion history")
            if previous_proof is not None:
                previous = json.loads(previous_proof[2])
                need(previous["approvals"] <= history["approvals"] and previous["commits"] <= history["commits"]
                     and (previous["commits"] == 0 or previous["history_sha256"] == history["history_sha256"]),
                     "V3 Source history was reset or rewritten")
            body = {"format": "greenmind.sql-audit-v3-scope-proof.v1", "epoch": pins["epoch"],
                    "genesis_sha256": pins["genesis_sha256"], "source_identity_sha256": codec.digest(identity),
                    "audit_schema_sha256": pins["schema_sha256"], "backup_candidate": restore["candidate"],
                    "backup_manifest_sha256": restore["gateway_manifest_sha256"], "audit_plaintext_sha256": restore["audit_plaintext_sha256"],
                    "snapshot_sha256": restore["snapshot_sha256"], "restore_evidence_sha256": proof_guard.config["audit_v3_restore_evidence_sha256"],
                    "registry_reference_sha256": registry_evidence.reference_sha, "history_sha256": history["history_sha256"],
                    "inventory_sha256": codec.digest(before), "runtime_sha256": runtime_sha256, "bindings_sha256": bindings_sha256,
                    "approvals": history["approvals"], "commits": history["commits"]}
            raw = registry_v5.canonical(body)
            proof_row = [1, pins["epoch"], raw, hashlib.sha256(raw.encode()).hexdigest()]
            _guard(candidate, adapter, receipt, permit, authorization_path, authorization_sha256,
                   runtime_sha256, bindings_sha256, checkpoint, clock, proof_guard, lock_key, require_enabled=False)
            sql_deletion_scope.v3_registry_evidence(ledger, registry_evidence)
            need(sql_deletion_scope.inventory(ledger) == before and not db.in_transaction, "V3 SQL journal changed")
            try:
                db.execute("BEGIN IMMEDIATE")
                if bootstrap:
                    need(not sql_deletion_scope._table(db, "sql_audit_v3_scope") and not sql_deletion_scope._table(db, "sql_audit_v3_scope_proof"), "V3 scope appeared")
                    db.execute(registry_v5.DDL["sql_audit_v3_scope"])
                    db.execute(registry_v5.DDL["sql_audit_v3_scope_proof"])
                    db.execute("INSERT INTO sql_audit_v3_scope VALUES(1,?,?,?)", (pins["epoch"], pins["genesis_sha256"], "needs_reconcile"))
                    db.execute("INSERT INTO sql_audit_v3_scope_proof VALUES(?,?,?,?)", proof_row)
                else:
                    changed = db.execute("UPDATE sql_audit_v3_scope_proof SET epoch=?,body=?,body_sha256=? WHERE id=? AND epoch=? AND body=? AND body_sha256=?",
                                         (*proof_row[1:], *previous_proof)).rowcount
                    need(changed == 1, "V3 scope proof CAS failed")
                registry_v5.validate("sql_audit_v3_scope_proof", proof_row, candidate.environment, ledger)
                old_state = "needs_reconcile" if bootstrap else previous_scope[3]
                changed = db.execute("UPDATE sql_audit_v3_scope SET state='current' WHERE id=1 AND epoch=? AND genesis_sha256=? AND state=?",
                                     (pins["epoch"], pins["genesis_sha256"], old_state)).rowcount
                need(changed == 1 and sql_deletion_scope.inventory(ledger) == before, "V3 scope CAS or budget changed")
                checkpoint()
                require_bound_source_lease()
                proof_guard.check_deadline()
                db.commit()
                committed = True
            except BaseException:
                db.rollback()
                raise
    except BaseException:
        # An uncertain Source transaction after a local CAS must not leave current.
        if committed and not db.in_transaction and sql_deletion_scope._table(db, "sql_audit_v3_scope"):
            with db:
                db.execute("UPDATE sql_audit_v3_scope SET state='needs_reconcile' WHERE id=1 AND epoch=? AND genesis_sha256=? AND state='current'",
                           (pins["epoch"], pins["genesis_sha256"]))
        raise
    ledger.publish_read_view()
    return {"status": "SQL_AUDIT_V3_SCOPE_CURRENT", "epoch": pins["epoch"], "proof_sha256": proof_row[3],
            "pilot_budget_consumed": bool(before["pilots"]), "deletion_performed": False}


def stage_source_approval(candidate, adapter, ledger, *, authorization_path, authorization_sha256,
                          runtime_sha256, bindings_sha256, proof_guard, checkpoint, clock,
                          binding_path, binding_sha256, dba_identity_path, dba_identity_sha256,
                          producer_sha256, challenge_file, frame_file):
    """Issue a one-shot Source approval challenge after the complete live V3 guard.

    No DML is authorized. Any uncertain exchange leaves a durable intent and
    cannot be retried without independent Source/ledger reconciliation.
    """
    import base64

    from app.raw_archive import remote_lease
    from app.raw_archive.scientific_scope_control import current_source_session

    from .policy import eligibility
    from .restore_inputs import frozen
    from .sql_deletion_guard import ProductionGuardV3
    from .sql_runtime import _await_source_frame

    need(os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SQL_V3_SOURCE_APPROVAL_ENABLED") == "true"
         and type(proof_guard) is ProductionGuardV3 and type(adapter) is Source and callable(checkpoint),
         "Explicit root V3 source approval controller required")
    bound_job_lock()
    require_bound_source_lease()
    sql_deletion._scope(candidate, adapter, checkpoint)
    now = utc(clock())
    permit = activation(authorization_path, authorization_sha256, candidate, now)
    need(permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256
         and permit["audit"]["schema_sha256"] == proof_guard.config["audit_schema_sha256"]
         and permit["audit"]["epoch"] == proof_guard.config["audit_v3_epoch"]
         and permit["audit"]["genesis_sha256"] == proof_guard.config["audit_v3_genesis_sha256"],
         "V3 approval proof scope differs")
    spec = sql_deletion.DATASETS[candidate.dataset]
    pk = [{key: item["body"]["row"][key] for key in spec.keys} for item in adapter.expected]
    need(codec.digest(pk) == permit["audit"]["pk_sha256"], "V3 approval primary-key scope differs")
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict, "V3 verified candidate receipt required")
    eligibility(candidate, receipt, now)
    proof_guard.prepare(candidate, receipt, now, permit)
    need(activation(authorization_path, authorization_sha256, candidate, utc(clock())) == permit,
         "V3 authorization expired after preflight")
    for path, digest in ((binding_path, binding_sha256), (dba_identity_path, dba_identity_sha256)):
        need(type(digest) is str and sql_audit_v3.HEX64.fullmatch(digest), "Pinned Source input required")
        info = Path(path).lstat()
        need(info.st_uid == 0 and info.st_mode & 0o777 == 0o400 and info.st_nlink == 1, "Root immutable Source input required")
    before_binding, before_dba = frozen(Path(binding_path)), frozen(Path(dba_identity_path))
    binding = _private_read(Path(binding_path), 16384)
    dba_raw = _private_read(Path(dba_identity_path), 16384)
    need(hashlib.sha256(binding).hexdigest() == binding_sha256 and hashlib.sha256(dba_raw).hexdigest() == dba_identity_sha256
         and frozen(Path(binding_path)) == before_binding and frozen(Path(dba_identity_path)) == before_dba,
         "Source input changed")
    try:
        dba_identity = json.loads(dba_raw)
        need(type(json.loads(binding)) is dict, "Invalid Source binding")
    except (ValueError, UnicodeError) as exc:
        raise ArchiveBlocked("Invalid Source input") from exc
    need(hashlib.sha256(Path(sql_audit_v3.__file__).read_bytes()).hexdigest() == producer_sha256,
         "Pinned Source producer differs")
    session = current_source_session()
    authority = remote_lease._BOUND_SOURCE_LEASE.get()
    remote = getattr(authority[0], "__self__", None) if authority else None
    need(type(remote) is remote_lease.RemoteLease and remote.session == session, "Actual Source lease required")
    seconds = min(35, int(remote.deadline - time.monotonic()) - 10)
    source_permit = make_source_permit(candidate, permit, authorization_sha256, dba_identity,
                                       proof_guard.config["source_identity"], session,
                                       producer_sha256=producer_sha256, seconds=seconds)
    raw_permit = json.dumps(source_permit, sort_keys=True, separators=(",", ":")).encode()
    permit_sha = hashlib.sha256(raw_permit).hexdigest()
    request = {"format": "greenmind.sql-v3-source-transport.v1", "permit_base64": base64.b64encode(raw_permit).decode(),
               "permit_sha256": permit_sha, "binding_base64": base64.b64encode(binding).decode(),
               "binding_sha256": binding_sha256, "lease_session": session}
    _budget(ledger, candidate, permit, authorization_sha256)
    intent = ledger.root / ("sql-v3-source-approval-" + permit["pilot_id"] + ".intent")
    try:
        fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    except FileExistsError as exc:
        raise ArchiveBlocked("V3 Source approval outcome requires reconciliation") from exc
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        stream.write(json.dumps({"format": "greenmind.sql-v3-source-approval-intent.v1", "candidate": candidate.identity,
                                 "pilot_id": permit["pilot_id"], "authorization_sha256": authorization_sha256,
                                 "permit_sha256": permit_sha, "binding_sha256": binding_sha256}, sort_keys=True, separators=(",", ":")).encode())
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(ledger.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    frame_sha = _await_source_frame(challenge_file, frame_file, "sql-v3-approval", request, checkpoint, seconds=seconds)
    raw = _private_read(Path(frame_file), 16384)
    need(hashlib.sha256(raw).hexdigest() == frame_sha, "V3 Source frame changed")
    try:
        frame = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ArchiveBlocked("Invalid V3 Source frame") from exc
    need(frame == {"format": "greenmind.sql-v3-source-result.v1", "lease_session": session,
                   "permit_sha256": permit_sha, "slot": permit["audit"]["slot"], "epoch": permit["audit"]["epoch"],
                   "status": "source_approval_recorded", "production_acceptance": False}, "V3 Source approval frame differs")
    checkpoint()
    need(current_source_session() == session, "Source lease changed after approval")
    return {"status": "source_approval_recorded", "permit_sha256": permit_sha, "frame_sha256": frame_sha,
            "candidate": candidate.identity, "production_acceptance": False, "deletion_authorized": False}


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
