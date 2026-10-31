"""Durable deletion intents under a versioned, explicitly activated policy."""

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .policy import POLICY_SHA256, eligibility, utc

WAV_DELETE_RULES = {
    "version": "greenmind-wav-delete-v2",
    "kinds": ["wav"],
    "reserve_days_after_first_complete_confirmation": 7,
    "maximum_snapshot_readback_age_hours": 24,
    "exact_source_revision_and_work_lease": True,
    "preserve_features_sql_and_storage_box": True,
}
WAV_DELETE_POLICY_SHA256 = hashlib.sha256(
    json.dumps(WAV_DELETE_RULES, sort_keys=True).encode()
).hexdigest()


def wav_only(candidate):
    if candidate.kind != "wav":
        raise ArchiveBlocked("Only source WAV eviction is within this deletion release")


def deletion_gate(permit, candidate):
    wav_only(candidate)
    if (
        permit.get("wav_deletion_policy") != WAV_DELETE_POLICY_SHA256
        or permit.get("kinds") != ["wav"]
        or permit.get("acceptance", {}).get("wav_deletion_policy") != WAV_DELETE_POLICY_SHA256
        or re.fullmatch(r"[a-f0-9]{64}", permit.get("work_lease_coverage_sha256", "")) is None
        or permit.get("acceptance", {}).get("work_lease_coverage_sha256")
        != permit.get("work_lease_coverage_sha256")
    ):
        raise ArchiveBlocked("Versioned WAV-only release acceptance required")


def limits(permit):
    maximum = {"wavs": 10, "wav_bytes": 5 * 1024**2, "sql_partitions": 1}
    if permit["mode"] == "automatic":
        maximum.update(permit.get("limits", {}))
    if set(maximum) != {"wavs", "wav_bytes", "sql_partitions"} or any(
        type(value) is not int for value in maximum.values()
    ):
        raise ArchiveBlocked("Explicit integer deletion budgets required")
    if not (
        1 <= maximum["wavs"] <= 100
        and 1 <= maximum["wav_bytes"] <= 64 * 1024**2
        and 1 <= maximum["sql_partitions"] <= 100
    ):
        raise ArchiveBlocked("Deletion budgets exceed reviewed worker ceilings")
    return maximum


def activation(path, expected_sha256, candidate, now):
    wav_only(candidate)
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        info = os.fstat(body.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o077
        ):
            raise ArchiveBlocked("Private operator activation file required")
        raw = body.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ArchiveBlocked("Activation file changed")
    data = json.loads(raw)
    deletion_gate(data, candidate)
    if (
        data.get("policy") != POLICY_SHA256
        or data.get("environment") != candidate.environment
        or data.get("mode") not in {"pilot", "automatic"}
        or data.get("kinds") != ["wav"]
        or not utc(data["authorized_at"]) <= now < utc(data["expires_at"])
    ):
        raise ArchiveBlocked("This archive operation is not authorized")
    if data["mode"] == "pilot" and candidate.identity not in data.get("candidates", []):
        raise ArchiveBlocked("Candidate is outside the exact pilot")
    acceptance = data["acceptance"]
    if (
        acceptance.get("policy") != POLICY_SHA256
        or acceptance.get("environment") != candidate.environment
        or acceptance.get("runtime_sha256") != data.get("runtime_sha256")
        or not re.fullmatch(r"[a-f0-9]{64}", data.get("bindings_sha256", ""))
        or acceptance.get("bindings_sha256") != data.get("bindings_sha256")
        or not acceptance.get("archive_only_restore")
        or not acceptance.get("dashboard_parity")
        or not acceptance.get("tenant_isolation")
        or not acceptance.get("rollback_passed")
        or not acceptance.get("backlog_reconciled")
    ):
        raise ArchiveBlocked("Complete accepted release evidence required")
    observation = acceptance["observation"]
    if (
        utc(observation["finished_at"]) - utc(observation["started_at"]) < timedelta(hours=24)
        or not utc(acceptance["deployed_at"]) <= utc(observation["started_at"])
        or utc(observation["finished_at"]) > now
        or observation.get("maximum_gap_seconds", 601) > 600
        or observation.get("samples", 0) < 280
        or observation.get("errors") != 0
        or observation.get("progress") != {"gateway": True, "direct": True}
    ):
        raise ArchiveBlocked("Completed post-deployment 24-hour observation required")
    return data


def _evict(
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
    wav_only(candidate)
    if candidate.environment == "production" and not callable(proof_guard):
        raise ArchiveBlocked("Production snapshot and database proof guard required")
    # adapter.exclusive() must fence ingestion/updates of exactly this partition.
    # Inspect and deletion therefore share the source-side transaction or lock.
    checkpoint()
    now = utc(clock())
    permit = activation(authorization_path, authorization_sha256, candidate, now)
    if permit["runtime_sha256"] != runtime_sha256:
        raise ArchiveBlocked("Running release differs from accepted release")
    if permit["bindings_sha256"] != bindings_sha256:
        raise ArchiveBlocked("Source or Storage Box differs from accepted deployment")
    if candidate.environment == "production" and (
        os.getenv("WAV_SOURCE_WORK_LEASES_ENABLED") != "true"
        or os.getenv("TIERED_ARCHIVE_WORK_LEASE_COVERAGE_SHA256")
        != permit["work_lease_coverage_sha256"]
    ):
        raise ArchiveBlocked("Live source work-lease deployment differs from accepted coverage")
    with adapter.exclusive(candidate):
        receipt = ledger.get(candidate.identity)
        adapter.deletion_intent_active = bool(receipt and receipt.get("state") == "deleting")
        eligibility(candidate, receipt or {}, now)
        if proof_guard is not None:
            proof_guard(receipt, now)
        if candidate.kind == "wav":
            originals = [
                reference for reference in receipt["files"] if reference["format"] == "wav"
            ]
            if (
                not originals
                or len(originals) > limits(permit)["wavs"]
                or sum(reference["bytes"] for reference in originals) > limits(permit)["wav_bytes"]
            ):
                raise ArchiveBlocked("Initial exact WAV deletion budget exceeded")
        if receipt["state"] == "evicted":
            if not adapter.absent(candidate):
                raise ArchiveBlocked("An evicted source unexpectedly reappeared")
            return receipt
        if adapter.absent(candidate):
            if receipt["state"] != "deleting":
                raise ArchiveBlocked("Source vanished without durable deletion intent")
        else:
            if adapter.inspect(candidate) != candidate:
                raise ArchiveBlocked("Source or dependencies changed since archive verification")
            checkpoint()
            now = utc(clock())
            eligibility(candidate, receipt, now)
            activation(authorization_path, authorization_sha256, candidate, now)
            receipt = ledger.save(
                candidate,
                "deleting",
                dict(
                    receipt,
                    deletion_intent={
                        "wav_deletion_policy": WAV_DELETE_POLICY_SHA256,
                        "authorization_sha256": authorization_sha256,
                        "runtime_sha256": runtime_sha256,
                        "bindings_sha256": bindings_sha256,
                        "work_lease_coverage_sha256": permit["work_lease_coverage_sha256"],
                        "source_revision": candidate.identity,
                        "created_at": now.isoformat(),
                    },
                ),
                now,
            )
            adapter.deletion_intent_active = True

            def exact_guard():
                checkpoint()
                current = utc(clock())
                eligibility(candidate, receipt, current)
                activation(authorization_path, authorization_sha256, candidate, current)
                if proof_guard is not None:
                    proof_guard(receipt, current)
                if adapter.inspect(candidate) != candidate:
                    raise ArchiveBlocked("Source changed immediately before exact WAV deletion")
                checkpoint()
                final_at = utc(clock())
                eligibility(candidate, receipt, final_at)
                activation(authorization_path, authorization_sha256, candidate, final_at)

            previous_guard = getattr(adapter, "deletion_guard", None)
            adapter.deletion_guard = exact_guard
            try:
                adapter.delete(candidate)
            finally:
                adapter.deletion_guard = previous_guard
        if not adapter.absent(candidate):
            raise ArchiveBlocked("Exact source still exists after deletion")
        finalize = getattr(adapter, "complete_eviction", None)
        if callable(finalize):
            finalize(candidate)
    # The source transaction commits before the final journal entry. A crash
    # between these commits recovers using the durable deleting intent above.
    return ledger.save(candidate, "evicted", receipt, utc(clock()))


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
    try:
        return _evict(
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
    except BaseException as error:
        ledger.audit_attempt(
            candidate,
            "wav_delete_blocked",
            {
                "wav_deletion_policy": WAV_DELETE_POLICY_SHA256,
                "failure_type": type(error).__name__,
                "automatic_retry": False,
            },
            datetime.now(UTC),
        )
        raise
