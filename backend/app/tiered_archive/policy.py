"""Common deletion contract for exact, fully verified cold-storage bundles."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from app.raw_archive.policy import ArchiveBlocked

VERSION = "greenmind-tiered-v1"
RULES = {
    "wav": {"local_days": 0, "reserve_days": 7},
    "direct_payload": {"local_days": 0, "reserve_days": 7},
    "raw_readings": {"local_days": 0, "reserve_days": 7},
    "dashboard": {"local_days": 90, "reserve_days": 7},
    "features": {"local_days": 90, "reserve_days": 7},
    "logs": {"local_days": 30, "reserve_days": 7},
}
POLICY_SHA256 = hashlib.sha256(
    json.dumps({"version": VERSION, "rules": RULES}, sort_keys=True).encode()
).hexdigest()


def utc(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ArchiveBlocked("Timezone-aware proof timestamp required")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Candidate:
    environment: str
    source: str
    dataset: str
    organization: str
    start: datetime
    end: datetime
    content_sha256: str
    rows: int
    kind: str
    settled_at: datetime
    complete: bool = False
    features_verified: bool = False
    projections_verified: bool = False
    dependencies_complete: bool = False
    active: bool = False
    quarantined: bool = False
    late_until: datetime | None = None

    @property
    def identity(self):
        body = asdict(self)
        body = {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in body.items()
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def validate(self):
        import re
        import uuid

        if self.environment not in {"staging", "production"} or self.source not in {
            "gateway",
            "direct",
        }:
            raise ArchiveBlocked("Explicit archive environment and source required")
        uuid.UUID(self.organization)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.dataset):
            raise ArchiveBlocked("Unsafe archive dataset")
        if not re.fullmatch(r"[a-f0-9]{64}", self.content_sha256) or self.rows <= 0:
            raise ArchiveBlocked("Content identity and nonempty row count required")
        if self.kind not in RULES or utc(self.start) >= utc(self.end):
            raise ArchiveBlocked("Unclassified archive candidate")
        utc(self.settled_at)
        if self.late_until is not None:
            utc(self.late_until)


def eligibility(candidate, receipt, now):
    candidate.validate()
    now = utc(now)
    if (
        not (
            candidate.complete
            and candidate.features_verified
            and candidate.projections_verified
            and candidate.dependencies_complete
        )
        or candidate.active
        or candidate.quarantined
    ):
        raise ArchiveBlocked("Processing, dependencies or dashboard coverage incomplete")
    if receipt.get("candidate") != candidate.identity or receipt.get("policy") != POLICY_SHA256:
        raise ArchiveBlocked("Proof belongs to another source revision or policy")
    if receipt.get("state") not in {"verified", "deleting", "evicted"}:
        raise ArchiveBlocked("No complete archive proof")
    first, checked = utc(receipt["first_verified_at"]), utc(receipt["verified_at"])
    snapshot = receipt["snapshot"]
    created = utc(snapshot["created_at"])
    if (
        not first <= checked <= now
        or not candidate.settled_at <= first
        or not created <= checked
        or now - checked > timedelta(hours=24)
        or now - created > timedelta(hours=24)
    ):
        raise ArchiveBlocked("Archive or snapshot proof is stale or future-dated")
    if not snapshot.get("provider_id") or not snapshot.get("box_id"):
        raise ArchiveBlocked("Provider-bound snapshot required")
    rule = RULES[candidate.kind]
    ready = max(
        first + timedelta(days=rule["reserve_days"]),
        utc(candidate.end) + timedelta(days=rule["local_days"]),
        utc(candidate.late_until or candidate.end),
    )
    if now < ready:
        raise ArchiveBlocked("Local reserve, hot period or late-arrival horizon not complete")
    return ready
