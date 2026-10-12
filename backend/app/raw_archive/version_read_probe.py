"""Bounded version reads on the two approved sample WAVs; no mutation APIs."""

import hashlib

from .diagnostics import inspect_buckets
from .health import SafetyPause
from .policy import ArchiveBlocked, Recording


def inspect(client, requests, healthy):
    from botocore.exceptions import ClientError

    if set(requests) != {"gateway", "direct"}:
        raise ArchiveBlocked("Two exact approved WAV receipts are required")

    def guard():
        if not healthy():
            raise SafetyPause(healthy.last.get("reason", "source_headroom"))

    report = {
        "read_only": True,
        "complete_catalog": False,
        "legacy_null_accepted": False,
        "deletion_readiness": False,
        "deleted_files": 0,
        "samples": [],
    }
    for kind in ("gateway", "direct"):
        record = Recording.from_receipt(requests[kind]["recording"])
        bucket = "greenmind-raw" if kind == "gateway" else "greenmind-direct-production-hotspot"
        if record.kind != kind or record.bucket != bucket or not record.key.endswith(".wav"):
            raise ArchiveBlocked("Sample does not belong to an exact production WAV bucket")
        guard()
        metadata = inspect_buckets(client, [bucket])[0]
        if metadata.get("versioning", {}).get("Status") != "Enabled":
            raise ArchiveBlocked("Actual bucket versioning is not Enabled")
        lifecycle = metadata["lifecycle"]
        if "error" in lifecycle and lifecycle["error"] != "NoSuchLifecycleConfiguration":
            raise ArchiveBlocked("Actual lifecycle state could not be read")
        guard()
        versions = client.list_object_versions(Bucket=bucket, Prefix=record.key, MaxKeys=1)
        matching = [x for x in versions.get("Versions", []) if x.get("Key") == record.key]
        if len(matching) != 1 or not matching[0].get("VersionId"):
            raise ArchiveBlocked("Exact sample version is unavailable")
        version = matching[0]["VersionId"]
        guard()
        headers = client.head_object(Bucket=bucket, Key=record.key, VersionId=version)
        if headers.get("ContentLength") != record.size:
            raise ArchiveBlocked("Sample size changed")
        guard()
        response = client.get_object(
            Bucket=bucket, Key=record.key, VersionId=version, Range="bytes=0-43"
        )
        try:
            if response.get("ContentLength") != 44:
                raise ArchiveBlocked("Bounded range response required")
            raw = response["Body"].read(45)
        finally:
            response["Body"].close()
        if len(raw) != 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise ArchiveBlocked("Sample does not have a readable WAV header")
        report["samples"].append(
            {
                **metadata,
                "key_sha256": hashlib.sha256(record.key.encode()).hexdigest(),
                "version_id": version,
                "legacy_null": version == "null",
                "list_versions": True,
                "specific_version_head": True,
                "specific_version_range_get": True,
                "header_bytes": 44,
                "full_wav_decode": False,
            }
        )
    guard()
    try:
        client.list_buckets()
    except ClientError as error:
        if error.response["Error"]["Code"] != "AccessDenied":
            raise ArchiveBlocked("Global listing denial was not proved") from None
    else:
        raise ArchiveBlocked("Diagnostic access unexpectedly allows global listing")
    guard()
    report.update(status="passed", global_list_denied=True)
    return report
