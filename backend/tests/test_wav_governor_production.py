import copy
import json
import shutil
import wave
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import parquet
from app.tiered_archive.deletion import WAV_DELETE_POLICY_SHA256, activation, evict
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import publish, verify_snapshot
from app.tiered_archive.policy import POLICY_SHA256, Candidate, eligibility
from app.tiered_archive.wav_source import WavSource

NOW = datetime(2026, 10, 31, tzinfo=UTC)
ORG = "11111111-1111-4111-8111-111111111111"


class Box:
    identity = "local-test-box"

    def __init__(self, root):
        self.root = root
        root.mkdir()

    def publish(self, key, path):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def download(self, key, path, size, *, snapshot=None):
        shutil.copyfile(self.root / key, path)


class Provider:
    def __init__(self, at):
        self.at = at

    def read(self, identity):
        return {"id": identity, "storage_box": 7, "name": "snap1", "created": self.at.isoformat()}


@pytest.fixture
def archive(tmp_path):
    row = {"identity": "x", "organization": ORG, "sensor": "sensor", "time_us": 1, "body": {}}
    path = tmp_path / "meta.parquet"
    meta = dict(parquet.write(path, [row]), format="parquet")
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(100)
        f.writeframes(b"\0\0" * 20)
    original = {
        "format": "wav",
        "sha256": checksum(wav),
        "bytes": wav.stat().st_size,
        "wav_header": {"frames": 20, "rate": 100, "channels": 1, "width": 2},
        "key": "staging/source.wav",
    }
    candidate = Candidate(
        "staging",
        "gateway",
        "gateway_wav",
        ORG,
        NOW - timedelta(days=10),
        NOW - timedelta(days=9),
        meta["content_sha256"],
        1,
        "wav",
        NOW - timedelta(days=9),
        complete=True,
        features_verified=True,
        projections_verified=True,
        dependencies_complete=True,
    )
    ledger = Ledger(tmp_path / "state")
    box = Box(tmp_path / "box")
    first = NOW - timedelta(days=8)
    publish(
        candidate, [(path, meta), (wav, original)], box, ledger, checkpoint=lambda: None, now=first
    )
    verify_snapshot(candidate, box, ledger, Provider(first), 1, checkpoint=lambda: None, now=first)
    verify_snapshot(candidate, box, ledger, Provider(NOW), 2, checkpoint=lambda: None, now=NOW)
    yield candidate, ledger, box
    ledger.close()


def permit(tmp_path, candidate, now=NOW):
    data = {
        "policy": POLICY_SHA256,
        "wav_deletion_policy": WAV_DELETE_POLICY_SHA256,
        "work_lease_coverage_sha256": "c" * 64,
        "environment": "staging",
        "mode": "pilot",
        "kinds": ["wav"],
        "candidates": [candidate.identity],
        "authorized_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "runtime_sha256": "a" * 64,
        "bindings_sha256": "b" * 64,
        "acceptance": {
            "policy": POLICY_SHA256,
            "wav_deletion_policy": WAV_DELETE_POLICY_SHA256,
            "work_lease_coverage_sha256": "c" * 64,
            "environment": "staging",
            "runtime_sha256": "a" * 64,
            "bindings_sha256": "b" * 64,
            "archive_only_restore": True,
            "dashboard_parity": True,
            "tenant_isolation": True,
            "rollback_passed": True,
            "backlog_reconciled": True,
            "deployed_at": (now - timedelta(days=2)).isoformat(),
            "observation": {
                "started_at": (now - timedelta(days=2)).isoformat(),
                "finished_at": (now - timedelta(days=1)).isoformat(),
                "maximum_gap_seconds": 300,
                "samples": 288,
                "errors": 0,
                "progress": {"gateway": True, "direct": True},
            },
        },
    }
    p = tmp_path / "permit.json"
    p.write_text(json.dumps(data))
    p.chmod(0o600)
    return p, data


class Adapter:
    def __init__(self, candidate):
        self.candidate = candidate
        self.exists = True
        self.calls = 0
        self.crash = False
        self.guard_calls = 0

    @contextmanager
    def exclusive(self, candidate):
        yield

    def absent(self, candidate):
        return not self.exists

    def inspect(self, candidate):
        return self.candidate

    def delete(self, candidate):
        self.deletion_guard()
        self.guard_calls += 1
        self.exists = False
        self.calls += 1
        if self.crash:
            raise RuntimeError("synthetic crash after exact delete")


def run_delete(tmp_path, archive, adapter, **extra):
    candidate, ledger, _ = archive
    p, _ = permit(tmp_path, candidate)
    return evict(
        candidate,
        adapter,
        ledger,
        authorization_path=p,
        authorization_sha256=checksum(p),
        runtime_sha256="a" * 64,
        bindings_sha256="b" * 64,
        checkpoint=lambda: None,
        clock=lambda: NOW,
        **extra,
    )


def test_WAV_only_rejects_SQL_before_adapter_or_auth_file(archive):
    candidate, ledger, _ = archive
    with pytest.raises(ArchiveBlocked, match="Only source WAV"):
        evict(
            replace(candidate, kind="dashboard"),
            None,
            ledger,
            authorization_path="/nonexistent",
            authorization_sha256="",
            runtime_sha256="",
            bindings_sha256="",
            checkpoint=lambda: None,
            clock=lambda: NOW,
        )


def test_mixed_or_legacy_release_never_deletes(tmp_path, archive):
    candidate, _, _ = archive
    p, data = permit(tmp_path, candidate)
    for value in (["wav", "features"], ["features"]):
        data["kinds"] = value
        p.write_text(json.dumps(data))
        with pytest.raises(ArchiveBlocked, match="WAV-only"):
            activation(p, checksum(p), candidate, NOW)
    data["kinds"] = ["wav"]
    data.pop("wav_deletion_policy")
    p.write_text(json.dumps(data))
    with pytest.raises(ArchiveBlocked, match="WAV-only"):
        activation(p, checksum(p), candidate, NOW)


def test_first_reserve_begins_only_after_snapshot_and_is_preserved(archive):
    candidate, ledger, _ = archive
    receipt = ledger.get(candidate.identity)
    assert receipt["first_verified_at"] == (NOW - timedelta(days=8)).isoformat()
    assert receipt["snapshot"]["checked_at"] == NOW.isoformat()
    assert eligibility(candidate, receipt, NOW) <= NOW
    pending = copy.deepcopy(receipt)
    pending["first_verified_at"] = (NOW - timedelta(days=6)).isoformat()
    with pytest.raises(ArchiveBlocked, match="reserve"):
        eligibility(candidate, pending, NOW)


def test_snapshot_readback_missing_stale_or_future_blocks(archive):
    candidate, ledger, _ = archive
    receipt = ledger.get(candidate.identity)
    for at in (None, NOW - timedelta(hours=25), NOW + timedelta(seconds=1)):
        bad = copy.deepcopy(receipt)
        if at is None:
            bad["snapshot"].pop("checked_at")
        else:
            bad["snapshot"]["checked_at"] = at.isoformat()
        with pytest.raises(ArchiveBlocked):
            eligibility(candidate, bad, NOW)


def test_existing_snapshot_created_age_protection_is_preserved(archive):
    candidate, ledger, _ = archive
    receipt = ledger.get(candidate.identity)
    receipt["snapshot"]["created_at"] = (NOW - timedelta(hours=25)).isoformat()
    with pytest.raises(ArchiveBlocked):
        eligibility(candidate, receipt, NOW)


def test_positive_version_bound_delete_and_no_repeat(tmp_path, archive):
    candidate, _, _ = archive
    adapter = Adapter(candidate)
    assert run_delete(tmp_path, archive, adapter)["state"] == "evicted"
    assert adapter.calls == adapter.guard_calls == 1
    assert run_delete(tmp_path, archive, adapter)["state"] == "evicted"
    assert adapter.calls == 1


def test_crash_after_delete_recovers_exact_intent_without_redelete(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    adapter.crash = True
    with pytest.raises(RuntimeError):
        run_delete(tmp_path, archive, adapter)
    assert ledger.get(candidate.identity)["state"] == "deleting"
    assert run_delete(tmp_path, archive, adapter)["state"] == "evicted"
    assert adapter.calls == 1


def test_fresh_database_or_snapshot_guard_failure_retains_source(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)

    def guard(receipt, at):
        raise ArchiveBlocked("missing current source database backup")

    with pytest.raises(ArchiveBlocked):
        run_delete(tmp_path, archive, adapter, proof_guard=guard)
    assert (
        adapter.exists
        and adapter.calls == 0
        and ledger.get(candidate.identity)["state"] == "verified"
    )


def test_production_requires_native_proof_guard_before_any_source_access(archive):
    candidate, ledger, _ = archive
    with pytest.raises(ArchiveBlocked, match="proof guard"):
        evict(
            replace(candidate, environment="production"),
            None,
            ledger,
            authorization_path="/none",
            authorization_sha256="",
            runtime_sha256="",
            bindings_sha256="",
            checkpoint=lambda: None,
            clock=lambda: NOW,
        )


def test_production_WavSource_requires_live_lease_flag_before_SQL(archive, monkeypatch):
    candidate, _, _ = archive
    monkeypatch.delenv("WAV_SOURCE_WORK_LEASES_ENABLED", raising=False)
    source = WavSource(None, None, None, "gateway", "production", None, lambda: None)
    source.expected = [{"body": {}}]
    with pytest.raises(ArchiveBlocked, match="work leases"):
        with source.exclusive(replace(candidate, environment="production")):
            pass


def test_expiry_after_intent_blocks_before_object_delete(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    p, _ = permit(tmp_path, candidate)
    count = [0]

    def clock():
        count[0] += 1
        return NOW if count[0] < 3 else NOW + timedelta(hours=2)

    with pytest.raises(ArchiveBlocked):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=p,
            authorization_sha256=checksum(p),
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=clock,
        )
    assert (
        adapter.exists
        and adapter.calls == 0
        and ledger.get(candidate.identity)["state"] == "deleting"
    )


def test_crash_recovery_completes_catalog_marker_before_evicted_receipt(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    adapter.crash = True
    finalized = []

    def complete(candidate):
        assert ledger.get(candidate.identity)["state"] == "deleting"
        finalized.append(candidate.identity)

    adapter.complete_eviction = complete
    with pytest.raises(RuntimeError):
        run_delete(tmp_path, archive, adapter)
    assert finalized == []
    assert run_delete(tmp_path, archive, adapter)["state"] == "evicted"
    assert finalized == [candidate.identity] and adapter.calls == 1


def test_proof_guard_rechecked_inside_durable_intent_before_each_WAV(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    states = []

    def proof(receipt, at):
        states.append(ledger.get(candidate.identity)["state"])

    run_delete(tmp_path, archive, adapter, proof_guard=proof)
    assert states == ["verified", "deleting"]


def inspection_source(candidate, mutate=None, absent=False):
    from types import SimpleNamespace

    from app.raw_archive.policy import Recording
    from app.tiered_archive.wav_source import record_json

    record = Recording(
        "gateway",
        "22222222-2222-4222-8222-222222222222",
        "greenmind-raw",
        "fixed.wav",
        "d" * 64,
        100,
        candidate.end,
        candidate.settled_at,
        "e" * 64,
        candidate.start,
        "sensor",
    )
    envelope = {
        "identity": "fixed",
        "organization": candidate.organization,
        "sensor": "sensor",
        "time_us": 1,
        "body": {
            "reference": [record.identity],
            "metadata": {
                "feature": {"value": 7},
                "feature_versions": [{"version": "a", "value": 1}, {"version": "b", "value": 2}],
            },
            "originals": [{"recording": record_json(record), "snapshot": {"VersionId": "v1"}}],
        },
    }
    fresh = copy.deepcopy(envelope)
    for item in fresh["body"]["originals"]:
        item.pop("snapshot")
    if mutate:
        mutate(fresh)
    source = WavSource(
        None,
        SimpleNamespace(resolve=lambda ref: [record]),
        SimpleNamespace(deletion_target=lambda r, s: None if absent else {"VersionId": "v1"}),
        "gateway",
        "staging",
        None,
        lambda: None,
    )
    source.db = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(scalar_one=lambda: candidate.organization)
    )
    source.expected = [envelope]
    flags = {
        "complete": True,
        "features_verified": True,
        "projections_verified": True,
        "dependencies_complete": True,
    }
    source.metadata = lambda reference: (fresh, {}, flags)
    return source


def test_feature_version_order_is_not_a_source_mutation(archive):
    candidate, _, _ = archive
    source = inspection_source(
        candidate, lambda value: value["body"]["metadata"]["feature_versions"].reverse()
    )
    assert source.inspect(candidate) == candidate


def test_new_or_changed_feature_version_blocks_original_removal(archive):
    candidate, _, _ = archive

    def append(value):
        value["body"]["metadata"]["feature_versions"].append({"version": "c", "value": 3})

    def change(value):
        value["body"]["metadata"]["feature_versions"][0]["value"] = 999

    for mutation in (append, change):
        with pytest.raises(ArchiveBlocked, match="feature versions"):
            inspection_source(candidate, mutation).inspect(candidate)


def test_partial_multi_run_recovery_needs_a_preexisting_durable_intent(archive):
    candidate, _, _ = archive
    source = inspection_source(candidate, absent=True)
    with pytest.raises(ArchiveBlocked, match="durable deletion intent"):
        source.inspect(candidate)
    source.deletion_intent_active = True
    assert source.inspect(candidate) == candidate


def test_database_backup_source_destination_and_checked_at_are_mandatory():
    from app.tiered_archive.backup import MASTER_PROOF_VERSION, require_current

    receipt = {
        "state": "verified",
        "policy": POLICY_SHA256,
        "destination": "box",
        "source": {
            "dataset": "database_backup",
            "source": "gateway",
            "environment": "production",
            "end": NOW.isoformat(),
        },
        "files": [
            {"backup_format": "schema-master-v2", "master_proof_version": MASTER_PROOF_VERSION}
        ],
        "verified_at": NOW.isoformat(),
        "snapshot": {
            "provider_id": 1,
            "box_id": 7,
            "created_at": NOW.isoformat(),
            "checked_at": NOW.isoformat(),
        },
    }
    kwargs = {"source": "gateway", "environment": "production", "destination": "box", "now": NOW}
    assert require_current([receipt], **kwargs) is receipt
    for change in ("source", "destination", "checked_at", "stale"):
        bad = copy.deepcopy(receipt)
        if change == "source":
            bad["source"]["source"] = "direct"
        if change == "destination":
            bad["destination"] = "other-box"
        if change == "checked_at":
            bad["snapshot"].pop("checked_at")
        if change == "stale":
            bad["source"]["end"] = (NOW - timedelta(hours=25)).isoformat()
        with pytest.raises(ArchiveBlocked):
            require_current([bad], **kwargs)


def test_shared_helper_key_matches_exact_signed64_contract():
    import hashlib

    from app.raw_archive.worklease import object_lock_key

    value = ["gateway", "greenmind-raw", "fixed.wav"]
    expected = int.from_bytes(
        hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).digest()[:8],
        "big",
        signed=True,
    )
    assert object_lock_key(*value) == expected
    assert object_lock_key("direct", value[1], value[2]) != expected


def test_active_shared_reader_blocks_writer_before_any_row_or_object_mutation(archive, monkeypatch):
    from types import SimpleNamespace

    candidate, _, _ = archive
    monkeypatch.setenv("WAV_SOURCE_WORK_LEASES_ENABLED", "true")
    calls = []

    class DB:
        def execute(self, sql, args=None):
            calls.append(str(sql))
            return SimpleNamespace(scalar_one=lambda: False)

        def query(self, *args):
            pytest.fail("row mutation/read after active-work conflict")

    class Sessions:
        @contextmanager
        def begin(self):
            yield DB()

    source = WavSource(Sessions(), None, None, "gateway", "production", None, lambda: None)
    source.expected = [
        {
            "body": {
                "reference": ["22222222-2222-4222-8222-222222222222"],
                "originals": [{"recording": {"bucket": "greenmind-raw", "key": "fixed.wav"}}],
            }
        }
    ]
    with pytest.raises(ArchiveBlocked, match="active reader"):
        with source.exclusive(replace(candidate, environment="production")):
            pytest.fail("exclusive lease entered")
    assert calls[-1] == "SELECT pg_try_advisory_xact_lock(:key)" and source.db is None


@pytest.mark.parametrize(
    "kind,expected",
    [
        (
            "gateway",
            ["wav_file", "sensor", "gateway", "zone", "wav_feature", "wav_feature_version"],
        ),
        (
            "direct",
            [
                "direct_segment",
                "direct_revision",
                "direct_device",
                "direct_session",
                "direct_enrollment",
            ],
        ),
    ],
)
def test_assignment_parent_and_revision_rows_share_the_delete_transaction(
    archive, monkeypatch, kind, expected
):
    import uuid
    from types import SimpleNamespace

    candidate, _, _ = archive
    monkeypatch.setenv("WAV_SOURCE_WORK_LEASES_ENABLED", "true")
    locked = []

    class Query:
        def __init__(self, model):
            self.table = model.__tablename__
            self.locked = False

        def filter_by(self, **kwargs):
            return self

        def with_for_update(self, **kwargs):
            assert kwargs == {"nowait": True}
            self.locked = True
            locked.append(self.table)
            return self

        def one(self):
            assert self.locked
            return SimpleNamespace(
                sensor_id=uuid.UUID(ORG),
                gateway_id=uuid.UUID(ORG),
                zone_id=uuid.UUID(ORG),
                device_id=ORG,
                session_id=ORG,
            )

        def all(self):
            assert self.locked
            return []

    class DB:
        def execute(self, sql, args=None):
            return SimpleNamespace(scalar_one=lambda: True)

        def query(self, model):
            return Query(model)

    database = DB()

    class Sessions:
        @contextmanager
        def begin(self):
            yield database

    source = WavSource(Sessions(), None, None, kind, "production", None, lambda: None)
    scientific_checks = []
    if kind == "direct":
        # This unit isolates row locking; acceptance itself has dedicated tests.
        monkeypatch.setattr(
            source, "_verify_scientific_history", lambda: scientific_checks.append(True)
        )
    source.expected = [
        {
            "body": {
                "reference": [ORG] if kind == "gateway" else [ORG, 1],
                "originals": [{"recording": {"bucket": "greenmind-raw", "key": "fixed.wav"}}],
            }
        }
    ]
    with source.exclusive(replace(candidate, source=kind, environment="production")):
        assert source.db is database and locked == expected
        assert scientific_checks == ([True] if kind == "direct" else [])
    assert source.db is None


def test_blocked_delete_is_durable_audit_without_corrupting_valid_receipt(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(replace(candidate, content_sha256="f" * 64))
    before = ledger.get(candidate.identity)
    with pytest.raises(ArchiveBlocked):
        run_delete(tmp_path, archive, adapter)
    assert ledger.get(candidate.identity) == before and adapter.calls == 0
    event = ledger.db.execute(
        "SELECT state,body FROM event ORDER BY ordinal DESC LIMIT 1"
    ).fetchone()
    assert (
        event[0] == "wav_delete_blocked"
        and json.loads(event[1])["failure_type"] == "ArchiveBlocked"
    )


def test_intent_binds_exact_release_authorization_and_work_lease_before_source_delete(
    tmp_path, archive
):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    original = adapter.delete

    def inspect_intent_then_delete(value):
        receipt = ledger.get(value.identity)
        assert receipt["state"] == "deleting"
        assert receipt["deletion_intent"]["wav_deletion_policy"] == WAV_DELETE_POLICY_SHA256
        assert receipt["deletion_intent"]["source_revision"] == value.identity
        assert receipt["deletion_intent"]["work_lease_coverage_sha256"] == "c" * 64
        original(value)

    adapter.delete = inspect_intent_then_delete
    assert run_delete(tmp_path, archive, adapter)["state"] == "evicted"


def test_permission_expiry_during_final_source_scan_blocks_delete(tmp_path, archive):
    candidate, ledger, _ = archive
    adapter = Adapter(candidate)
    p, _ = permit(tmp_path, candidate)
    scans = [0]

    def inspect(value):
        scans[0] += 1
        return candidate

    adapter.inspect = inspect

    def clock():
        return NOW + timedelta(hours=2) if scans[0] >= 2 else NOW

    with pytest.raises(ArchiveBlocked):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=p,
            authorization_sha256=checksum(p),
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=clock,
        )
    assert (
        adapter.exists
        and adapter.calls == 0
        and ledger.get(candidate.identity)["state"] == "deleting"
    )


def test_only_exclusive_writer_connection_prevents_silent_idle_lease_loss(monkeypatch):
    import sqlalchemy

    from app.raw_archive import runner
    from app.tiered_archive import runtime

    reader = "postgresql://reader:synthetic@127.0.0.1:5433/plantdb_staging"
    writer = "postgresql://writer:synthetic@127.0.0.1:5433/plantdb_staging"
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_GATEWAY_WRITER_DATABASE_URL", writer)
    monkeypatch.setattr(runner, "metadata_source_settings", lambda *args: {"database": reader})
    calls = []
    monkeypatch.setattr(
        sqlalchemy, "create_engine", lambda *args, **kwargs: calls.append(kwargs) or object()
    )
    runtime.engine("gateway", writing=False)
    runtime.engine("gateway", writing=True)
    reader_options = calls[0]["connect_args"]["options"]
    writer_options = calls[1]["connect_args"]["options"]
    assert "idle_in_transaction_session_timeout" not in reader_options
    assert writer_options.endswith("-c idle_in_transaction_session_timeout=0")
    for option in ("statement_timeout=8000", "lock_timeout=150", "work_mem=4096"):
        assert option in reader_options and option in writer_options
