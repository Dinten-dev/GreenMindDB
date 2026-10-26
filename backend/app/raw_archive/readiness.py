"""Release acceptance pinned inside the separately human-reviewed pilot manifest."""

import re
from datetime import timedelta

from .policy import ArchiveBlocked

REQUIRED_TESTS = {
    "gateway_old_url_local",
    "gateway_old_url_archive_only",
    "direct_old_url_local",
    "direct_old_url_archive_only",
    "real_zone_denial",
    "job_owner_isolation",
    "cookie_authentication",
    "sha256_and_wav_decode",
    "zip_manifest",
    "series_1h",
    "series_7d",
    "series_30d",
    "series_1y",
    "csv_display_agreement",
    "proxy_rollback",
    "receiver_continuity",
    "memory_guard_download_progress",
    "empty_metadata_restore",
    "snapshot_readback",
}


def validate_release(data, recording, now):
    from .deletion import stamp

    release = data.get("release_acceptance", {})
    if (
        release.get("schema") != 2
        or release.get("environment") != data.get("environment")
        or release.get("destination") != data.get("destination")
        or release.get("catalog_manifest") != data.get("catalog_manifest")
        or release.get("recovery_index") != data.get("recovery_index")
        or release.get("snapshot") != data.get("snapshot")
    ):
        raise ArchiveBlocked("Pinned release acceptance differs from this pilot")
    if not re.fullmatch(r"[0-9a-f]{40}", release.get("revision", "")):
        raise ArchiveBlocked("Accepted code revision is required")
    if not now - timedelta(hours=24) <= stamp(release.get("checked_at")) <= now:
        raise ArchiveBlocked("Release acceptance is stale")
    evidence = release.get("evidence_sha256", {})
    for name in ("downloads", "observation", "reconciliation", "diagnostic", "provider"):
        if not re.fullmatch(r"[0-9a-f]{64}", evidence.get(name, "")):
            raise ArchiveBlocked("Complete hash-pinned release evidence is required")
    tests = release.get("tests", {})
    if any(tests.get(name) is not True for name in REQUIRED_TESTS):
        raise ArchiveBlocked("Real reader, download and rollback acceptance is incomplete")
    if release.get("reader_live") is not True:
        raise ArchiveBlocked("Candidate-only acceptance cannot authorize production removal")
    observation = release.get("observation", {})
    if (
        observation.get("receiver_observation_passed") is not True
        or observation.get("samples", 0) < 280
        or observation.get("elapsed_seconds", 0) < 86400
        or observation.get("maximum_gap_seconds", 601) > 600
        or any(
            observation.get(name) != 0
            for name in ("errors", "protected_changes", "unhealthy_samples")
        )
        or observation.get("source_progress") != {"gateway": True, "direct": True}
    ):
        raise ArchiveBlocked("Complete receiver observation is required")
    deployed = stamp(release.get("deployed_at"))
    begin, end = stamp(observation.get("started_at")), stamp(observation.get("finished_at"))
    if not deployed <= begin < end <= now or end - begin < timedelta(hours=24):
        raise ArchiveBlocked("Observation must follow the accepted deployment")
    reconciliation = release.get("reconciliation", {})
    if (
        reconciliation.get("complete") is not True
        or reconciliation.get("eligible_pending_files") != 0
        or reconciliation.get("receipt_mismatches") != 0
        or reconciliation.get("unknown_eligible_files") != 0
        or not re.fullmatch(r"[0-9a-f]{64}", reconciliation.get("inventory_sha256", ""))
    ):
        raise ArchiveBlocked("Complete fixed eligible-backlog reconciliation is required")
    if not begin <= stamp(reconciliation.get("cutoff")) <= now:
        raise ArchiveBlocked("Reconciliation cutoff predates the accepted release")
    snapshot = release["snapshot"]
    if (
        type(snapshot.get("provider_id")) is not int
        or snapshot["provider_id"] <= 0
        or type(snapshot.get("storage_box_id")) is not int
        or snapshot["storage_box_id"] <= 0
        or snapshot.get("provider_verified") is not True
    ):
        raise ArchiveBlocked("Provider snapshot identity is unproven")
    diagnostic = release.get("buckets", {}).get(recording.bucket, {})
    if diagnostic.get("versioning") != "Enabled" or diagnostic.get("enabled_lifecycle_rules") != 0:
        raise ArchiveBlocked("Actual bucket versioning/lifecycle acceptance is required")
    if not now - timedelta(hours=24) <= stamp(diagnostic.get("checked_at")) <= now:
        raise ArchiveBlocked("Bucket diagnostic acceptance is stale")


def validate_metadata_restore(catalog, proof):
    from .wav_catalog import ALLOWLIST_SHA256, FORMAT, manifest_check

    manifest_check(catalog)
    if (
        proof.get("schema") != 2
        or proof.get("format") != FORMAT
        or proof.get("allowlist_sha256") != ALLOWLIST_SHA256
        or proof.get("tables") != catalog["tables"]
    ):
        raise ArchiveBlocked("WAV-only metadata restoration proof is required")
    restored = proof.get("restored", [])
    expected = [
        (item["kind"], item["file"], item["sha256"], item["rows"]) for item in catalog["files"]
    ]
    actual = [
        (item.get("kind"), item.get("file"), item.get("sha256"), item.get("rows"))
        for item in restored
    ]
    if actual != expected:
        raise ArchiveBlocked("Complete restored metadata parts differ from the manifest")
    for kind in ("gateway", "direct"):
        if sum(item["rows"] for item in catalog["files"] if item["kind"] == kind) != sum(
            catalog["tables"][kind].values()
        ):
            raise ArchiveBlocked("Metadata table coverage differs from restored rows")
