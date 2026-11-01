import hashlib
import json
import sqlite3
from datetime import timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import managed_catalog_labels, managed_catalog_reference
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.managed_snapshot import check_external_dependencies, cycle
from app.tiered_archive.pipeline import publish, verify_snapshot
from app.tiered_archive.registry_backup import restore
from app.tiered_archive.runtime import candidate_from_json
from tests.test_tiered_archive import AT, bundle  # noqa: F401


class Provider:
    box_id = 12

    def __init__(self, clock):
        self.clock = clock
        self.rows = [
            {"id": n, "storage_box": 12, "name": f"manual-{n}", "created": AT.isoformat()}
            for n in range(1, 8)
        ]
        self.posts = 0
        self.deletes = []
        self.lose_post = False
        self.lose_delete = False

    def inventory(self):
        return [dict(row) for row in self.rows], 10

    def read(self, identity):
        return next(row for row in self.rows if row["id"] == identity)

    def require_action(self, action_id, snapshot_id, operation):
        # Test double simulates successful provider reconciliation for unknown IDs.
        assert operation in {"create", "delete"}
        return action_id or (10000 + snapshot_id)

    def create_managed(self, intent, journal_reference):
        self.posts += 1
        self.rows.append(
            {
                "id": 100 + self.posts,
                "storage_box": 12,
                "name": f"managed-{self.posts}",
                "created": self.clock().isoformat(),
                "description": "greenmind-managed-" + intent,
                "labels": {
                    "purpose": "greenmind-managed",
                    "intent": intent,
                    **managed_catalog_labels(journal_reference),
                },
            }
        )
        if self.lose_post:
            raise OSError("lost response")
        return {"action": {"id": 1000 + self.posts}}

    def delete_managed(self, identity, *, ledger, intent):
        self.deletes.append(identity)
        self.rows = [row for row in self.rows if row["id"] != identity]
        if self.lose_delete:
            raise OSError("lost response")
        return {"action": {"id": 2000 + len(self.deletes)}}


@pytest.fixture
def managed(bundle):  # noqa: F811
    candidate, path, metadata, ledger, box, _ = bundle
    current = [AT + timedelta(days=2)]

    def clock():
        return current[0]

    publish(
        candidate,
        [(path, metadata)],
        box,
        ledger,
        checkpoint=lambda: None,
        now=current[0] - timedelta(seconds=1),
    )
    provider = Provider(clock)
    return ledger, box, provider, current, clock


def run(managed):
    ledger, box, provider, _, clock = managed
    return cycle(ledger, box, provider, lambda: None, now=clock, dependency_guard=lambda *_: None)


def test_three_full_readbacks_precede_rotation_and_manuals_survive(managed):
    ledger, _, provider, current, _ = managed
    assert run(managed) == 101
    assert run(managed) == 101
    current[0] += timedelta(hours=12)
    assert run(managed) == 102
    current[0] += timedelta(hours=12)
    assert run(managed) == 103
    assert provider.deletes == [101]
    assert {row["id"] for row in provider.rows} == set(range(1, 8)) | {102, 103}
    assert (
        ledger.db.execute(
            "SELECT count(*) FROM managed_snapshot WHERE state='verified'"
        ).fetchone()[0]
        == 2
    )


def test_unknown_post_is_reconciled_without_a_second_post(managed):
    _, _, provider, _, _ = managed
    provider.lose_post = True
    with pytest.raises(OSError):
        run(managed)
    provider.lose_post = False
    assert run(managed) == 101
    assert provider.posts == 1


def test_unknown_post_without_provider_match_never_retries(managed):
    _, _, provider, _, _ = managed
    calls = []

    def uncertain(intent, journal_reference):
        calls.append(intent)
        raise OSError("unknown outcome")

    provider.create_managed = uncertain
    with pytest.raises(OSError):
        run(managed)
    with pytest.raises(ArchiveBlocked, match="requires provider reconciliation"):
        run(managed)
    assert len(calls) == 1


def test_snapshot_recovers_ownership_journal_and_resumes_without_post(managed, tmp_path):
    _, box, provider, _, clock = managed
    run(managed)
    snapshot = provider.read(101)
    ref = managed_catalog_reference(snapshot)
    ledger = Ledger(tmp_path / "isolated-restored-catalog")

    class SnapshotStore:
        def download(self, key, path, size):
            box.download(key, path, size, snapshot=snapshot["name"])

    try:
        restore(
            ref, ledger, SnapshotStore(), checkpoint=lambda: None, expected_environment="staging"
        )
        assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone()[0] == "intent"
        assert cycle(ledger, box, provider, lambda: None, now=clock) == 101
        assert provider.posts == 1
        assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone()[0] == "verified"
    finally:
        ledger.close()


def test_missing_three_slots_blocks_before_snapshot_creation(managed):
    _, _, provider, _, _ = managed
    provider.rows.append(
        {"id": 8, "storage_box": 12, "name": "manual-8", "created": AT.isoformat()}
    )
    with pytest.raises(ArchiveBlocked, match="positions"):
        run(managed)
    assert provider.posts == 0
    assert provider.deletes == []


def test_corrupt_snapshot_readback_blocks_verification_and_rotation(managed):
    ledger, box, provider, _, _ = managed
    real = box.download

    def corrupt(key, path, size, *, snapshot=None):
        real(key, path, size, snapshot=snapshot)
        if snapshot:
            path.write_bytes(b"corrupt")

    box.download = corrupt
    with pytest.raises(ArchiveBlocked):
        run(managed)
    assert provider.deletes == []
    assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone()[0] == "bound"


def test_unknown_delete_is_reconciled_without_a_second_delete(managed):
    _, _, provider, current, _ = managed
    run(managed)
    current[0] += timedelta(hours=12)
    run(managed)
    current[0] += timedelta(hours=12)
    provider.lose_delete = True
    with pytest.raises(OSError):
        run(managed)
    provider.lose_delete = False
    assert run(managed) == 103
    assert provider.deletes == [101]


def test_rotation_blocks_when_older_snapshot_remains_in_use(managed):
    ledger, _, provider, current, _ = managed
    run(managed)
    current[0] += timedelta(hours=12)
    run(managed)
    identity, raw = ledger.db.execute("SELECT id,receipt FROM bundle").fetchone()
    receipt = json.loads(raw)
    receipt["snapshot"] = {"provider_id": 101}
    with ledger.db:
        ledger.db.execute("UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
    current[0] += timedelta(hours=12)
    with pytest.raises(ArchiveBlocked, match="dependencies"):
        run(managed)
    assert provider.deletes == []
    assert {row["id"] for row in provider.rows} >= {101, 102, 103}


def test_rotation_refreshes_existing_proof_without_resetting_first_confirmation(managed):
    ledger, box, provider, current, clock = managed
    run(managed)
    raw = ledger.db.execute("SELECT receipt FROM bundle").fetchone()[0]
    candidate = candidate_from_json(json.loads(raw)["source"])
    first = verify_snapshot(
        candidate, box, ledger, provider, 101, checkpoint=lambda: None, now=clock()
    )
    current[0] += timedelta(hours=12)
    run(managed)
    current[0] += timedelta(hours=12)
    run(managed)
    refreshed = ledger.get(candidate.identity)
    assert refreshed["snapshot"]["provider_id"] == 103
    assert refreshed["first_verified_at"] == first["first_verified_at"]
    assert provider.deletes == [101]


def test_unknown_external_dependencies_pause_before_any_delete(managed):
    ledger, box, provider, current, clock = managed
    run(managed)
    current[0] += timedelta(hours=12)
    run(managed)
    current[0] += timedelta(hours=12)
    with pytest.raises(ArchiveBlocked, match="External snapshot restore"):
        cycle(ledger, box, provider, lambda: None, now=clock)
    assert provider.deletes == []
    assert (
        ledger.db.execute(
            "SELECT count(*) FROM managed_snapshot WHERE state='verified'"
        ).fetchone()[0]
        == 3
    )


def test_pinned_raw_journal_blocks_deleting_and_unknown_restore_scope(tmp_path, monkeypatch):
    path = tmp_path / "archive.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE archive (id TEXT, state TEXT, receipt TEXT)")
        db.execute("INSERT INTO archive VALUES ('a','verified','{}')")
    info = path.stat()
    config = tmp_path / "dependencies.json"
    config.write_text(
        json.dumps(
            {
                "schema": 1,
                "environment": "production",
                "raw_ledgers": [{"path": str(path), "device": info.st_dev, "inode": info.st_ino}],
            }
        )
    )
    config.chmod(0o600)
    monkeypatch.setenv("TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE", str(config))
    monkeypatch.setenv(
        "TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256",
        hashlib.sha256(config.read_bytes()).hexdigest(),
    )
    with pytest.raises(ArchiveBlocked, match="restore and pilot"):
        check_external_dependencies({"id": 1, "name": "s"}, [])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE archive SET state='deleting'")
    with pytest.raises(ArchiveBlocked, match="deletion intent"):
        check_external_dependencies({"id": 1, "name": "s"}, [])


def test_changed_survivor_keeps_deletion_unconfirmed(managed):
    ledger, _, provider, current, _ = managed
    run(managed)
    current[0] += timedelta(hours=12)
    run(managed)
    original_delete = provider.delete_managed

    def changed(identity, *, ledger, intent):
        response = original_delete(identity, ledger=ledger, intent=intent)
        next(row for row in provider.rows if row["id"] == 102)["labels"] = {}
        return response

    provider.delete_managed = changed
    current[0] += timedelta(hours=12)
    with pytest.raises(ArchiveBlocked, match="survivor identity"):
        run(managed)
    assert (
        ledger.db.execute(
            "SELECT count(*) FROM managed_snapshot WHERE state='delete_intent'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(ArchiveBlocked, match="survivor identity"):
        run(managed)
