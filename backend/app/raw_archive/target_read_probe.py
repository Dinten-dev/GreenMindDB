"""Exercise exact-version target checks through a read-only API boundary.

Only the two previously approved sample receipts are accepted by the operator
launcher. No eviction method, upload, SQL or policy activation is performed.
Sample preflight is not full-cohort or deletion acceptance.
"""

import hashlib
import os

from .health import SafetyPause
from .policy import ArchiveBlocked, Recording
from .storage import S3Source


def inspect(client, requests, healthy):
    if os.getenv("RAW_ARCHIVE_DELETE_ENABLED") != "false":
        raise ArchiveBlocked("Read-only preflight requires deletion disabled")
    if set(requests) != {"gateway", "direct"}:
        raise ArchiveBlocked("Exactly two approved sample receipts required")
    recordings, snapshots = [], []
    for kind, bucket in (
        ("gateway", "greenmind-raw"),
        ("direct", "greenmind-direct-production-hotspot"),
    ):
        record = Recording.from_receipt(requests[kind]["recording"])
        if record.kind != kind or record.bucket != bucket or not record.key.endswith(".wav"):
            raise ArchiveBlocked("Exact production sample identity required")
        snapshot = requests[kind].get("snapshot")
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"VersionId", "ETag", "ContentLength", "LastModified"}
            or snapshot["ContentLength"] != record.size
        ):
            raise ArchiveBlocked("Previously verified original snapshot required")
        recordings.append(record)
        snapshots.append(snapshot)

    approved = {(r.bucket, r.key) for r in recordings}
    buckets = {r.bucket for r in recordings}

    class ReadOnly:
        def __getattr__(self, method):
            if method not in {
                "head_object",
                "list_object_versions",
                "get_bucket_versioning",
                "get_bucket_lifecycle_configuration",
            }:
                raise ArchiveBlocked("Mutation and unapproved APIs are forbidden")

            def call(**arguments):
                if not healthy():
                    raise SafetyPause(healthy.last.get("reason", "source_headroom"))
                bucket = arguments.get("Bucket")
                if bucket not in buckets:
                    raise ArchiveBlocked("Unapproved sample bucket")
                if method in {"head_object", "list_object_versions"}:
                    key = arguments.get("Key", arguments.get("Prefix"))
                    if (bucket, key) not in approved:
                        raise ArchiveBlocked("Unapproved sample object")
                if method == "list_object_versions" and arguments.get("MaxKeys") != 2:
                    raise ArchiveBlocked("Exact bounded version listing required")
                return getattr(client, method)(**arguments)

            return call

    reader = ReadOnly()
    # Opt-in exists exclusively inside this no-mutation adapter. No environment
    # flag or deployed policy is changed; all ordinary workers stay fail closed.
    source = S3Source(reader, allow_legacy_null=True, diagnostic_client=reader)
    samples = []

    def identity(snapshot):
        version = snapshot["VersionId"]
        return (
            "null" if version in (None, "", "null") else version,
            snapshot["ETag"],
            snapshot["ContentLength"],
            snapshot["LastModified"],
        )

    for recording, original in zip(recordings, snapshots, strict=True):
        snapshot = source.snapshot(recording)
        if identity(snapshot) != identity(original):
            raise ArchiveBlocked("Original source snapshot changed")
        target = source.deletion_target(recording, snapshot)
        if target is None:
            raise ArchiveBlocked("Approved original is unavailable")
        samples.append(
            {
                "kind": recording.kind,
                "key_sha256": hashlib.sha256(recording.key.encode()).hexdigest(),
                "version_id": target["VersionId"],
                "legacy_null": target["VersionId"] == "null",
                "exact_target_checked": True,
            }
        )
    if not healthy():
        raise SafetyPause(healthy.last.get("reason", "source_headroom"))
    return {
        "status": "approved_target_preflight_passed",
        "samples": samples,
        "read_only": True,
        "complete_cohort": False,
        "legacy_null_enabled": False,
        "full_source_sha_verified": False,
        "deletion_readiness": False,
        "deleted_files": 0,
    }
