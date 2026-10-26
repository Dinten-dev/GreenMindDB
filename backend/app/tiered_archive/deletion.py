"""Durable deletion intents under a versioned, explicitly activated policy."""

import hashlib
import json
import os
import re
import stat
from datetime import timedelta
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .policy import POLICY_SHA256, eligibility, utc


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
    if (
        data.get("policy") != POLICY_SHA256
        or data.get("environment") != candidate.environment
        or data.get("mode") not in {"pilot", "automatic"}
        or candidate.kind not in data.get("kinds", [])
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
):
    # adapter.exclusive() must fence ingestion/updates of exactly this partition.
    # Inspect and deletion therefore share the source-side transaction or lock.
    checkpoint()
    now = utc(clock())
    permit = activation(authorization_path, authorization_sha256, candidate, now)
    if permit["runtime_sha256"] != runtime_sha256:
        raise ArchiveBlocked("Running release differs from accepted release")
    if permit["bindings_sha256"] != bindings_sha256:
        raise ArchiveBlocked("Source or Storage Box differs from accepted deployment")
    with adapter.exclusive(candidate):
        receipt = ledger.get(candidate.identity)
        eligibility(candidate, receipt or {}, now)
        if candidate.kind == "wav":
            originals = [
                reference for reference in receipt["files"] if reference["format"] == "wav"
            ]
            if (
                len(originals) > limits(permit)["wavs"]
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
            ledger.save(candidate, "deleting", receipt, now)
            adapter.delete(candidate)
        if not adapter.absent(candidate):
            raise ArchiveBlocked("Exact source still exists after deletion")
    # The source transaction commits before the final journal entry. A crash
    # between these commits recovers using the durable deleting intent above.
    return ledger.save(candidate, "evicted", receipt, utc(clock()))
