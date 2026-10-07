"""Explicit protective configuration only; never rewrites or deletes objects."""

from botocore.exceptions import ClientError

from .policy import ArchiveBlocked

BUCKETS = ("greenmind-raw", "greenmind-direct-production-hotspot")


def inspected(client, bucket):
    value = client.get_bucket_versioning(Bucket=bucket)
    status = value.get("Status")
    if status not in {None, "Enabled"}:
        raise ArchiveBlocked("Unexpected source versioning state requires review")
    try:
        client.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
            raise
    else:
        raise ArchiveBlocked("Existing lifecycle configuration requires separate review")
    return {"bucket": bucket, "versioning": status, "lifecycle": "absent"}


def enable_reviewed(client, *, checkpoint=lambda: None):
    checkpoint()
    # Inspect both buckets before changing either. No guessed bucket names.
    before = [inspected(client, bucket) for bucket in BUCKETS]
    changed = []
    for entry in before:
        checkpoint()
        if entry["versioning"] is None:
            client.put_bucket_versioning(
                Bucket=entry["bucket"], VersioningConfiguration={"Status": "Enabled"}
            )
            changed.append(entry["bucket"])
    checkpoint()
    after = [inspected(client, bucket) for bucket in BUCKETS]
    if any(entry["versioning"] != "Enabled" for entry in after):
        raise ArchiveBlocked("Versioning change could not be independently read back")
    return {
        "before": before,
        "after": after,
        "changed": changed,
        "original_objects_rewritten": 0,
        "deleted_files": 0,
        "legacy_null_deletion_enabled": False,
    }
