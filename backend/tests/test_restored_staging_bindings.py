"""A restored copy must not weaken endpoint, deletion or phase boundaries."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_existing_staging_bindings import profiles, reuse

NOW = datetime(2026, 10, 28, 13, tzinfo=UTC)


def restored():
    writer, reader, schedule = profiles()
    writer["TIERED_ARCHIVE_READS_ENABLED"] = "true"
    reader["TIERED_ARCHIVE_READS_ENABLED"] = "true"
    schedule.update(
        snapshot_id=1532426,
        scopes=[
            {
                "dataset": "visual_reading",
                "organization": "11111111-1111-4111-8111-111111111111",
                "start": "2026-09-01T00:00:00+00:00",
            }
        ],
    )
    evidence = {
        "schema": 1,
        "environment": "staging",
        "phase": "restore_verified_configuration",
        "runtime_sha256": "cdc65fbaf137e8e244d4e760b7de62a13970dd8b6556b80be8e8063b5b6d6697",
        "bindings_sha256": "b" * 64,
        "previous_ledger_sha256": "c" * 64,
        "activation_authorized": False,
        "deletion_enabled": False,
        "existing_receipts_preserved": True,
        "box_writes": 0,
        "source_writes": 0,
        "historical_snapshot_restore_passed": True,
        "sql_bundles_added": 1,
        "verified_at": NOW.isoformat(),
        "wav_sources": ["gateway", "direct"],
        "snapshot": {
            "id": 1532426,
            "storage_box": 658321,
            "name": "2026-10-28T10-54-04",
            "created": (NOW - timedelta(hours=1)).isoformat(),
        },
        "sql_archives": [
            {
                "identity": "d" * 64,
                "source": "gateway",
                "dataset": "visual_reading",
                "organization": "11111111-1111-4111-8111-111111111111",
                "rows": 1,
                "manifest_sha256": "e" * 64,
                "state": "verified",
                "start": "2026-09-19T00:00:00+00:00",
                "end": "2026-09-20T00:00:00+00:00",
                "published_at": (NOW - timedelta(hours=2)).isoformat(),
                "verified_at": NOW.isoformat(),
                "dependencies_complete": False,
                "current_readback_verified": True,
                "snapshot_readback_verified": True,
                "source_matches_current": True,
            }
        ],
    }
    return writer, reader, schedule, evidence


def test_restore_phase_binds_existing_archive_without_activation():
    result = reuse.validate_restored(*restored(), now=NOW)
    assert result["snapshot_id"] == 1532426 and result["sql_bundles"] == 1
    assert not result["activation_authorized"] and not result["deletion_enabled"]
    with pytest.raises(ValueError, match="First copy phase"):
        reuse.validate(*restored()[:3])


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "production"),
        ("runtime_sha256", "a" * 64),
        ("activation_authorized", True),
        ("deletion_enabled", True),
        ("existing_receipts_preserved", False),
        ("source_writes", 1),
        ("box_writes", 1),
        ("historical_snapshot_restore_passed", False),
        ("sql_bundles_added", 2),
        ("bindings_sha256", ""),
        ("previous_ledger_sha256", ""),
        ("wav_sources", ["gateway"]),
        ("verified_at", (NOW + timedelta(seconds=1)).isoformat()),
    ],
)
def test_incomplete_or_changed_evidence_rejected(field, value):
    writer, reader, schedule, evidence = restored()
    evidence[field] = value
    with pytest.raises(ValueError):
        reuse.validate_restored(writer, reader, schedule, evidence, now=NOW)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_matches_current", False),
        ("current_readback_verified", False),
        ("snapshot_readback_verified", False),
        ("dependencies_complete", True),
        ("state", "evicted"),
        ("rows", 0),
        ("identity", "no-hash"),
        ("manifest_sha256", ""),
        ("source", "direct"),
        ("dataset", "sensor_reading"),
        ("published_at", NOW.isoformat()),
        ("verified_at", (NOW + timedelta(seconds=1)).isoformat()),
    ],
)
def test_archive_cannot_claim_unproven_state(field, value):
    writer, reader, schedule, evidence = restored()
    evidence["sql_archives"][0][field] = value
    with pytest.raises(ValueError):
        reuse.validate_restored(writer, reader, schedule, evidence, now=NOW)


@pytest.mark.parametrize(
    "mutation",
    [
        "production",
        "deletion",
        "reader_upload",
        "reads_disabled",
        "snapshot",
        "automatic_snapshots",
        "backup",
        "scope",
        "duplicate",
        "stale",
    ],
)
def test_existing_guards_remain_enforced(mutation):
    writer, reader, schedule, evidence = restored()
    if mutation == "production":
        writer["RAW_ARCHIVE_GATEWAY_DATABASE_URL"] = writer[
            "RAW_ARCHIVE_GATEWAY_DATABASE_URL"
        ].replace("127.0.0.1", "188.245.247.156")
    elif mutation == "deletion":
        writer["TIERED_ARCHIVE_DELETE_ENABLED"] = "true"
    elif mutation == "reader_upload":
        reader["RAW_ARCHIVE_SFTP_KEY"] = "/unexpected"
    elif mutation == "reads_disabled":
        reader["TIERED_ARCHIVE_READS_ENABLED"] = "false"
    elif mutation == "snapshot":
        schedule["snapshot_id"] = 999
    elif mutation == "automatic_snapshots":
        schedule["scheduled_snapshots"] = True
    elif mutation == "backup":
        schedule["backups"] = ["gateway"]
    elif mutation == "scope":
        schedule["scopes"][0]["organization"] = "22222222-2222-4222-8222-222222222222"
    elif mutation == "duplicate":
        evidence["sql_archives"].append(deepcopy(evidence["sql_archives"][0]))
        evidence["sql_bundles_added"] = 2
    else:
        evidence["snapshot"]["created"] = (NOW - timedelta(hours=25)).isoformat()
    with pytest.raises(ValueError):
        reuse.validate_restored(writer, reader, schedule, evidence, now=NOW)
