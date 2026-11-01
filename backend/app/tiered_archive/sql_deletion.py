"""Separately authorized exact SQL partition pilot; never schedules itself.

The runtime must supply a real proof_guard which validates the deployed writer
fence, full partition, source, schema, backups, restores and snapshot. A Python
callback is not such evidence by itself; no production proof_guard is installed.
"""

import hashlib
import json
import os
import re
import stat
from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, delete, text

from app.raw_archive.policy import ArchiveBlocked

from . import gateway_references
from .codec import encode
from .policy import POLICY_SHA256, eligibility, utc
from .source import DATASETS, Source, row_identity

SQL_RULES = {
    "version": "greenmind-sql-delete-v2",
    "kinds": ["dashboard", "features", "logs"],
    "hot_days": {"dashboard": 90, "features": 90, "logs": 30},
    "reserve_days": 7,
    "max_candidate_rows": 32,
    "pilot_partitions": 1,
    "scope": "exact_rows_not_drop_table",
    "datasets": [
        "direct_visual_point",
        "gateway_state_report",
        "ingest_log",
        "visual_reading",
        "visual_wave",
        "wav_feature",
        "wav_feature_version",
    ],
    "excluded": ["direct_payload", "direct_revision", "raw_readings", "wav"],
}
SQL_POLICY_SHA256 = hashlib.sha256(json.dumps(SQL_RULES, sort_keys=True).encode()).hexdigest()
ALLOWED = frozenset(SQL_RULES["datasets"])
REQUIRED_PROOFS = frozenset(
    {
        "full_partition",
        "source_identity",
        "schema_and_side_effects",
        "tenant_isolation",
        "dependencies_and_references",
        "archive_only_restore",
        "snapshot_live_readback",
        "database_backup_restore",
        "observation_24h",
        "runtime_and_binding",
        "writer_fence",
    }
)
_SHA = re.compile(r"[a-f0-9]{64}")


def need(value, message):
    if not value:
        raise ArchiveBlocked(message)


def _enabled():
    need(
        all(
            os.getenv(key) == "true"
            for key in (
                "TIERED_ARCHIVE_DELETE_ENABLED",
                "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
                "TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED",
            )
        ),
        "Explicit SQL retention and deletion enablement required",
    )


def _production_guard(guard):
    from .sql_deletion_guard import ProductionGuard

    return type(guard) is ProductionGuard


def _prepare_guard(guard, candidate, receipt, now, permit):
    guard.prepare(candidate, receipt, now, permit)


def _begin_guard(guard):
    guard.begin_source()


def _guard_deadline(guard):
    guard.check_deadline()


def activation(path, expected_sha256, candidate, now):
    """Read a private SHA-pinned SQL-only activation, distinct from WAV policy."""
    need(
        type(expected_sha256) is str and _SHA.fullmatch(expected_sha256),
        "Pinned SQL authorization required",
    )
    path = Path(path)
    need(path.is_absolute() and not path.is_symlink(), "Private SQL authorization required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        raw = f.read(65537)
        after = os.fstat(f.fileno())
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid in {0, os.geteuid()}
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and len(raw) <= 65536
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        and hashlib.sha256(raw).hexdigest() == expected_sha256,
        "SQL authorization changed",
    )
    try:
        permit = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArchiveBlocked("Invalid SQL authorization") from exc
    keys = {
        "format",
        "policy",
        "sql_policy",
        "mode",
        "pilot_id",
        "environment",
        "candidate",
        "runtime_sha256",
        "bindings_sha256",
        "proof_contract_sha256",
        "fence_contract_sha256",
        "authorized_at",
        "expires_at",
        "acceptance",
    }
    need(
        type(permit) is dict
        and set(permit) == keys
        and permit["format"] == "greenmind.sql-deletion.v2"
        and permit["policy"] == POLICY_SHA256
        and permit["sql_policy"] == SQL_POLICY_SHA256
        and permit["mode"] in {"pilot", "automatic"}
        and permit["environment"] == candidate.environment
        and permit["candidate"] == candidate.identity
        and isinstance(permit["pilot_id"], str)
        and re.fullmatch(r"[a-f0-9]{32}", permit["pilot_id"])
        and all(
            type(permit[k]) is str and _SHA.fullmatch(permit[k])
            for k in (
                "runtime_sha256",
                "bindings_sha256",
                "proof_contract_sha256",
                "fence_contract_sha256",
            )
        )
        and utc(permit["authorized_at"]) <= now < utc(permit["expires_at"]),
        "Exact current SQL authorization required",
    )
    accepted = permit["acceptance"]
    need(
        type(accepted) is dict
        and set(accepted)
        == {
            "sql_policy",
            "candidate",
            "runtime_sha256",
            "bindings_sha256",
            "proof_contract_sha256",
            "fence_contract_sha256",
            "deployed_at",
            "observation",
        }
        and all(
            accepted[k] == permit[k]
            for k in (
                "sql_policy",
                "candidate",
                "runtime_sha256",
                "bindings_sha256",
                "proof_contract_sha256",
                "fence_contract_sha256",
            )
        ),
        "Exact separately accepted SQL evidence required",
    )
    obs = accepted["observation"]
    need(
        type(obs) is dict
        and set(obs)
        == {"started_at", "finished_at", "maximum_gap_seconds", "samples", "errors", "progress"}
        and utc(accepted["deployed_at"]) <= utc(obs["started_at"])
        and timedelta(hours=24) <= utc(obs["finished_at"]) - utc(obs["started_at"])
        and utc(obs["finished_at"]) <= now
        and type(obs["maximum_gap_seconds"]) is int
        and 0 <= obs["maximum_gap_seconds"] <= 600
        and type(obs["samples"]) is int
        and obs["samples"] >= 280
        and type(obs["errors"]) is int
        and obs["errors"] == 0
        and type(obs["progress"]) is dict
        and set(obs["progress"]) == {"gateway", "direct"}
        and all(value is True for value in obs["progress"].values()),
        "24-hour observation required",
    )
    return permit


def _scope(candidate, adapter, checkpoint):
    candidate.validate()
    spec = DATASETS.get(candidate.dataset)
    need(
        candidate.dataset in ALLOWED
        and spec is not None
        and spec.kind == candidate.kind
        and spec.source == candidate.source
        and candidate.kind in SQL_RULES["kinds"]
        and type(candidate.rows) is int
        and 0 < candidate.rows <= SQL_RULES["max_candidate_rows"],
        "SQL candidate dataset or bounded row scope is excluded",
    )
    need(
        type(adapter) is Source
        and adapter.source == candidate.source
        and adapter.environment == candidate.environment
        and type(adapter.expected) is list
        and len(adapter.expected) == candidate.rows,
        "Exact restored SQL source rows required",
    )
    digest = hashlib.sha256()
    previous = None
    seen = set()
    for item in adapter.expected:
        checkpoint()
        need(
            type(item) is dict
            and set(item) == {"identity", "organization", "sensor", "time_us", "body"}
            and item["organization"] == candidate.organization
            and type(item["body"]) is dict
            and set(item["body"]) == {"row", "context"}
            and type(item["body"]["row"]) is dict
            and item["identity"] == row_identity(spec, item["body"]["row"]),
            "SQL source row scope differs",
        )
        order = (item["time_us"], item["identity"])
        need(previous is None or order > previous, "SQL source rows are not unique and ordered")
        previous = order
        keys = tuple(encode(item["body"]["row"][key]) for key in spec.keys)
        need(keys not in seen, "Duplicate source primary key")
        seen.add(keys)
        encoded = encode(item)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    need(digest.hexdigest() == candidate.content_sha256, "SQL restored row digest differs")
    return spec


def _budget(ledger, permit, candidate, authorization_sha256):
    """Claim exactly one durable candidate per pilot, including across crashes/ticks."""
    need(
        not ledger.read_only and not ledger.db.in_transaction, "Writable idle SQL journal required"
    )
    need(
        ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_deletion_scope'"
        ).fetchone()
        and ledger.db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone()
        == ("current",),
        "SQL pilot budget provenance needs reconciliation",
    )
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS sql_deletion_pilot (
            pilot_id TEXT PRIMARY KEY, candidate TEXT NOT NULL, authorization_sha256 TEXT NOT NULL)""")
        ledger.db.execute(
            "INSERT OR IGNORE INTO sql_deletion_pilot VALUES (?,?,?)",
            (permit["pilot_id"], candidate.identity, authorization_sha256),
        )
        row = ledger.db.execute(
            "SELECT candidate,authorization_sha256 FROM sql_deletion_pilot WHERE pilot_id=?",
            (permit["pilot_id"],),
        ).fetchone()
        need(
            row == (candidate.identity, authorization_sha256),
            "SQL pilot partition budget already consumed",
        )


def _lock_key(candidate):
    raw = encode(
        {
            "source": candidate.source,
            "dataset": candidate.dataset,
            "organization": candidate.organization,
            "start": utc(candidate.start),
            "end": utc(candidate.end),
        }
    )
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _guard(
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
    *,
    require_enabled=True,
):
    checkpoint()
    if require_enabled:
        _enabled()
    now = utc(clock())
    fresh = activation(authorization_path, authorization_sha256, candidate, now)
    need(
        fresh == permit
        and permit["runtime_sha256"] == runtime_sha256
        and permit["bindings_sha256"] == bindings_sha256,
        "SQL authorization or live bindings changed",
    )
    eligibility(candidate, receipt, now)
    db = adapter.connection
    need(
        db is not None and db.dialect.name == "postgresql", "PostgreSQL source transaction required"
    )
    need(
        db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}).scalar_one()
        is True,
        "Full partition advisory fence unavailable",
    )
    txid = db.execute(text("SELECT txid_current()::text")).scalar_one()
    evidence = proof_guard(candidate, receipt, now, db, lock_key, permit)
    need(
        type(evidence) is dict
        and set(evidence)
        == {
            "candidate",
            "transaction_id",
            "fence_key",
            "proof_contract_sha256",
            "fence_contract_sha256",
            "checks",
        }
        and evidence["candidate"] == candidate.identity
        and evidence["transaction_id"] == txid
        and type(evidence["fence_key"]) is int
        and evidence["fence_key"] == lock_key
        and evidence["proof_contract_sha256"] == permit["proof_contract_sha256"]
        and evidence["fence_contract_sha256"] == permit["fence_contract_sha256"]
        and type(evidence["checks"]) is dict
        and set(evidence["checks"]) == REQUIRED_PROOFS
        and all(value is True for value in evidence["checks"].values()),
        "Live complete SQL proof guard rejected",
    )
    if adapter.expected:
        need(adapter.inspect(candidate) == candidate, "SQL source rows or dependencies changed")
    checkpoint()
    now = utc(clock())
    eligibility(candidate, receipt, now)
    need(
        activation(authorization_path, authorization_sha256, candidate, now) == permit,
        "SQL authorization expired immediately before DML",
    )
    _guard_deadline(proof_guard)


def _audit_begin(
    ledger,
    candidate,
    permit,
    authorization_sha256,
    runtime_sha256,
    bindings_sha256,
    proof_guard,
    db,
    source_row_sha256,
):
    from . import sql_deletion_audit as audit
    from . import sql_deletion_scope as scope

    genesis = scope.current_epoch(ledger, proof_guard, runtime_sha256, bindings_sha256)
    need(
        audit.inspect(db, genesis["audit_schema_sha256"], proof_guard.config["source_identity"])
        == genesis["source_identity_sha256"],
        "SQL Source audit binding changed",
    )
    scope.verify_history(db, ledger, genesis, active_pilot=permit["pilot_id"])
    return scope.commit_body(genesis, candidate, permit, authorization_sha256, source_row_sha256)


def _audit_commit(db, body, *, already_absent):
    from . import sql_deletion_audit as audit

    if already_absent:
        return audit.read(db, "commit", body)
    exists = db.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM public.greenmind_sql_deletion_audit_v1 WHERE event_id=:id)"
        ),
        {"id": audit.event_id(body)},
    ).scalar_one()
    need(exists is False, "SQL Source audit already committed while candidate remains")
    return None


def evict(
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
    proof_guard=None,
):
    """Delete one exact SQL partition only inside a continuously proven fence.

    proof_guard must establish that EVERY writer of this range acquires the
    supplied advisory key and that predicates/rows, dependencies, backups and
    restore/readback proofs remain current. A missing guard always blocks.
    """
    need(
        callable(checkpoint) and callable(clock) and _production_guard(proof_guard),
        "Native SQL proof guard required",
    )
    _enabled()
    need(
        type(runtime_sha256) is str
        and _SHA.fullmatch(runtime_sha256)
        and type(bindings_sha256) is str
        and _SHA.fullmatch(bindings_sha256),
        "Pinned SQL bindings required",
    )
    spec = _scope(candidate, adapter, checkpoint)
    now = utc(clock())
    permit = activation(authorization_path, authorization_sha256, candidate, now)
    need(
        permit["runtime_sha256"] == runtime_sha256 and permit["bindings_sha256"] == bindings_sha256,
        "Running SQL bindings differ",
    )
    initial_receipt = ledger.get(candidate.identity)
    need(type(initial_receipt) is dict, "Verified SQL receipt required")
    eligibility(candidate, initial_receipt, now)
    _prepare_guard(proof_guard, candidate, initial_receipt, now, permit)
    from .sql_deletion_scope import current_epoch

    current_epoch(ledger, proof_guard, runtime_sha256, bindings_sha256)
    _budget(ledger, permit, candidate, authorization_sha256)
    lock_key = _lock_key(candidate)
    originals = list(adapter.expected)
    try:
        _begin_guard(proof_guard)
        with adapter.exclusive(candidate):
            receipt = ledger.get(candidate.identity)
            need(type(receipt) is dict, "Verified SQL receipt required")
            if receipt.get("state") == "deleting":
                intent = receipt.get("deletion_intent")
                expected = {
                    "sql_policy": SQL_POLICY_SHA256,
                    "authorization_sha256": authorization_sha256,
                    "candidate": candidate.identity,
                    "pilot_id": permit["pilot_id"],
                    "runtime_sha256": runtime_sha256,
                    "bindings_sha256": bindings_sha256,
                    "proof_contract_sha256": permit["proof_contract_sha256"],
                    "fence_contract_sha256": permit["fence_contract_sha256"],
                }
                need(
                    type(intent) is dict
                    and set(intent) == set(expected) | {"created_at", "source_row_sha256"}
                    and all(intent[key] == value for key, value in expected.items())
                    and utc(intent["created_at"]) <= utc(clock()),
                    "SQL deletion intent binding differs",
                )
            absent_before_fence = adapter.absent(candidate)
            if absent_before_fence:
                need(
                    receipt.get("state") in {"deleting", "evicted"},
                    "SQL source absent without matching durable intent",
                )
                # Under the fence the full live partition must now be empty.
                adapter.expected = []
            _guard(
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
            )
            adapter.expected = originals
            absent = adapter.absent(candidate)
            need(absent == absent_before_fence, "SQL source changed while acquiring fence")
            from . import sql_deletion_audit as audit

            if absent:
                intent = receipt.get("deletion_intent")
                need(type(intent) is dict, "SQL Source row audit intent missing")
                source_row_sha256 = intent.get("source_row_sha256")
            else:
                source_row_sha256 = audit.native_row_hashes(
                    adapter.connection, candidate, originals, checkpoint
                )
                if receipt.get("state") == "deleting":
                    need(
                        receipt["deletion_intent"].get("source_row_sha256") == source_row_sha256,
                        "Source native row hashes changed after SQL intent",
                    )
            audit_body = _audit_begin(
                ledger,
                candidate,
                permit,
                authorization_sha256,
                runtime_sha256,
                bindings_sha256,
                proof_guard,
                adapter.connection,
                source_row_sha256,
            )
            _audit_commit(adapter.connection, audit_body, already_absent=absent)
            if receipt["state"] == "evicted":
                need(absent, "Evicted SQL partition reappeared")
                return receipt
            if absent:
                intent = receipt.get("deletion_intent")
                need(
                    receipt["state"] == "deleting"
                    and type(intent) is dict
                    and intent.get("sql_policy") == SQL_POLICY_SHA256
                    and intent.get("authorization_sha256") == authorization_sha256
                    and intent.get("candidate") == candidate.identity,
                    "SQL source absent without matching durable intent",
                )
            else:
                need(adapter.inspect(candidate) == candidate, "SQL source changed before intent")
                now = utc(clock())
                if receipt["state"] == "verified":
                    receipt = ledger.save(
                        candidate,
                        "deleting",
                        dict(
                            receipt,
                            deletion_intent={
                                "sql_policy": SQL_POLICY_SHA256,
                                "authorization_sha256": authorization_sha256,
                                "candidate": candidate.identity,
                                "pilot_id": permit["pilot_id"],
                                "runtime_sha256": runtime_sha256,
                                "bindings_sha256": bindings_sha256,
                                "proof_contract_sha256": permit["proof_contract_sha256"],
                                "fence_contract_sha256": permit["fence_contract_sha256"],
                                "created_at": now.isoformat(),
                                "source_row_sha256": source_row_sha256,
                            },
                        ),
                        now,
                    )
                audit.arm_row_trigger(adapter.connection, audit_body)
                for item in originals:
                    if candidate.source == "gateway" and spec.table in {
                        "visual_reading",
                        "visual_wave",
                    }:
                        _guard(
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
                        )
                        # For these two allowlisted datasets preserve emits exactly one INSERT per row.
                        gateway_references.preserve(
                            adapter.connection,
                            spec.table,
                            [item["body"]["row"]],
                            candidate.identity,
                        )
                    _guard(
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
                    )
                    table = adapter.table(spec.table)
                    row = item["body"]["row"]
                    condition = and_(*(table.c[key] == row[key] for key in spec.keys))
                    need(
                        adapter.connection.execute(delete(table).where(condition)).rowcount == 1,
                        "Exact SQL deletion count differs",
                    )
                    adapter.expected = adapter.expected[1:]
                adapter.expected = originals
                need(adapter.absent(candidate), "Exact SQL source rows remain")
                # Recheck the active source fence and live proof before commit.
                adapter.expected = []
                _guard(
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
                )
                txid = adapter.connection.execute(
                    text("SELECT pg_catalog.txid_current()::text")
                ).scalar_one()
                audit.verify_row_trigger(adapter.connection, audit_body, txid)
                audit.insert(adapter.connection, "commit", audit_body)
                checkpoint()
                _guard(
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
                )
                adapter.expected = originals
        # Source transaction has committed before final ledger state.
        return ledger.save(candidate, "evicted", receipt, utc(clock()))
    finally:
        adapter.expected = originals
