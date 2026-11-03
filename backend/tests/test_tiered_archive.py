"""Losslessness, expiry, concurrency, tenant isolation and crash recovery."""

import json
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked, RemoteMissing, checksum
from app.tiered_archive import codec, parquet
from app.tiered_archive.deletion import WAV_DELETE_POLICY_SHA256, evict
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import publish, verify_snapshot
from app.tiered_archive.policy import POLICY_SHA256, Candidate, eligibility
from app.tiered_archive.reader import archived_rows, merge, points
from app.tiered_archive.recovery import restore
from app.tiered_archive.storage import Cache, validate_key

ORG = "11111111-1111-4111-8111-111111111111"
SENSOR = "22222222-2222-4222-8222-222222222222"
AT = datetime(2026, 10, 1, tzinfo=UTC)


class Box:
    identity = "local-test-storagebox"

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.gets = 0

    def publish(self, key, path):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and checksum(target) != checksum(path):
            raise ArchiveBlocked("Remote corruption")
        shutil.copyfile(path, target)

    def download(self, key, path, size, *, snapshot=None):
        self.gets += 1
        source = self.root / key
        if not source.exists():
            raise RemoteMissing("Missing")
        shutil.copyfile(source, path)
        if path.stat().st_size > size:
            raise ArchiveBlocked("Oversized")

    def download_tiered(self, key, path, size):
        return self.download(key, path, size)


class Provider:
    def __init__(self, at):
        self.at = at

    def read(self, identity):
        return {"id": identity, "storage_box": 4, "name": "snap-1", "created": self.at.isoformat()}


@pytest.fixture
def bundle(tmp_path):
    rows = [
        {
            "identity": "a",
            "organization": ORG,
            "sensor": SENSOR,
            "time_us": int(AT.timestamp() * 1_000_000),
            "body": {
                "row": {
                    "sensor_id": uuid.UUID(SENSOR),
                    "kind": "temperature",
                    "unit": "C",
                    "bucket": AT,
                    "seconds": 600,
                    "n": 2,
                    "total": 6.0,
                    "total2": 20.0,
                    "minimum": 2.0,
                    "maximum": 4.0,
                    "identities": b"\x00\xff",
                },
                "context": {},
            },
        }
    ]
    path = tmp_path / "rows.parquet"
    metadata = dict(parquet.write(path, rows), format="parquet")
    candidate = Candidate(
        "staging",
        "gateway",
        "visual_reading",
        ORG,
        AT,
        AT + timedelta(days=1),
        metadata["content_sha256"],
        1,
        "dashboard",
        AT + timedelta(days=1),
        complete=True,
        features_verified=True,
        projections_verified=True,
        dependencies_complete=True,
    )
    ledger = Ledger(tmp_path / "state")
    box = Box(tmp_path / "box")
    yield candidate, path, metadata, ledger, box, rows
    ledger.close()


def verified(bundle, at):
    candidate, path, metadata, ledger, box, _ = bundle
    files = [(path, metadata)]
    if candidate.kind == "wav":
        import wave

        original = path.parent / "synthetic-deletion-source.wav"
        with wave.open(str(original), "wb") as body:
            body.setnchannels(1)
            body.setsampwidth(2)
            body.setframerate(100)
            body.writeframes(b"\x00\x00" * 20)
        files.append(
            (
                original,
                {
                    "format": "wav",
                    "sha256": checksum(original),
                    "bytes": original.stat().st_size,
                    "key": "staging/test-guard.wav",
                    "wav_header": {"frames": 20, "rate": 100, "channels": 1, "width": 2},
                },
            )
        )
    publish(candidate, files, box, ledger, checkpoint=lambda: None, now=at)
    return verify_snapshot(candidate, box, ledger, Provider(at), 3, checkpoint=lambda: None, now=at)


def test_lossless_values_and_tag_collision():
    value = {
        "bytes": b"\0\xff",
        "decimal": Decimal("123.000000000000000001"),
        "time": AT,
        "uuid": uuid.UUID(ORG),
        "user_json": {"$type": "bytes", "value": "malicious"},
    }
    assert codec.decode(codec.encode(value)) == value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), datetime(2026, 1, 1), {1: "bad"}])
def test_unsafe_or_lossy_values_blocked(value):
    with pytest.raises(ArchiveBlocked):
        codec.encode(value)


def test_parquet_reads_every_value_and_rejects_corruption(bundle):
    _, path, metadata, _, _, rows = bundle
    assert list(parquet.read(path, metadata)) == rows
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(ArchiveBlocked):
        parquet.verify(path, metadata)


def test_logical_checksum_independent_of_file_checksum(bundle):
    _, path, metadata, _, _, _ = bundle
    with pytest.raises(ArchiveBlocked):
        parquet.verify(path, dict(metadata, content_sha256="0" * 64))


def test_source_rows_must_be_unique_and_ordered(tmp_path):
    row = {"identity": "x", "organization": ORG, "sensor": SENSOR, "time_us": 1, "body": {}}
    with pytest.raises(ArchiveBlocked):
        parquet.write(tmp_path / "bad.parquet", [row, row])


def test_snapshot_before_publication_never_authorizes(bundle):
    candidate, path, metadata, ledger, box, _ = bundle
    publish(
        candidate,
        [(path, metadata)],
        box,
        ledger,
        checkpoint=lambda: None,
        now=AT + timedelta(days=2),
    )
    with pytest.raises(ArchiveBlocked):
        verify_snapshot(
            candidate,
            box,
            ledger,
            Provider(AT),
            3,
            checkpoint=lambda: None,
            now=AT + timedelta(days=2),
        )
    assert ledger.get(candidate.identity)["state"] == "published"


def test_snapshot_failure_does_not_create_success_receipt(bundle):
    candidate, path, metadata, ledger, box, _ = bundle
    receipt = publish(
        candidate,
        [(path, metadata)],
        box,
        ledger,
        checkpoint=lambda: None,
        now=AT + timedelta(days=2),
    )
    (box.root / receipt["files"][0]["key"]).write_bytes(b"bad")
    with pytest.raises(ArchiveBlocked):
        verify_snapshot(
            candidate,
            box,
            ledger,
            Provider(AT + timedelta(days=2)),
            3,
            checkpoint=lambda: None,
            now=AT + timedelta(days=2),
        )
    assert "first_verified_at" not in ledger.get(candidate.identity)


def test_refresh_preserves_seven_day_reserve_start(bundle):
    first = AT + timedelta(days=2)
    original = verified(bundle, first)
    candidate, _, _, ledger, box, _ = bundle
    fresh = verify_snapshot(
        candidate,
        box,
        ledger,
        Provider(first + timedelta(days=100)),
        4,
        checkpoint=lambda: None,
        now=first + timedelta(days=100),
    )
    assert fresh["first_verified_at"] == original["first_verified_at"]
    assert eligibility(candidate, fresh, first + timedelta(days=100)) <= first + timedelta(days=100)


@pytest.mark.parametrize(
    "kind,days", [("wav", 7), ("dashboard", 90), ("features", 90), ("logs", 30)]
)
def test_each_retention_rule(bundle, kind, days):
    original, path, metadata, ledger, box, _ = bundle
    candidate = replace(original, kind=kind)
    first = AT + timedelta(days=2)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=first)
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(first), 3, checkpoint=lambda: None, now=first
    )
    with pytest.raises(ArchiveBlocked):
        eligibility(candidate, receipt, first + timedelta(hours=23))
    at = max(first + timedelta(days=7), candidate.end + timedelta(days=days))
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(at), 4, checkpoint=lambda: None, now=at
    )
    eligibility(candidate, receipt, at)
    with pytest.raises(ArchiveBlocked):
        eligibility(candidate, receipt, at + timedelta(hours=25))


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("features_verified", False),
        ("projections_verified", False),
        ("dependencies_complete", False),
        ("active", True),
        ("quarantined", True),
    ],
)
def test_incomplete_or_active_data_remains_local(bundle, field, value):
    receipt = verified(bundle, AT + timedelta(days=100))
    candidate = replace(bundle[0], **{field: value})
    with pytest.raises(ArchiveBlocked):
        eligibility(candidate, receipt, AT + timedelta(days=100))


def permit(tmp_path, candidate, now):
    data = {
        "policy": POLICY_SHA256,
        "wav_deletion_policy": WAV_DELETE_POLICY_SHA256,
        "work_lease_coverage_sha256": "c" * 64,
        "environment": "staging",
        "mode": "pilot",
        "kinds": [candidate.kind],
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
                "samples": 288,
                "maximum_gap_seconds": 300,
                "errors": 0,
                "progress": {"gateway": True, "direct": True},
            },
        },
    }
    path = tmp_path / "permit.json"
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path, checksum(path)


class Adapter:
    def __init__(self, candidate):
        self.candidate = candidate
        self.exists = True
        self.crash = False
        self.calls = 0

    @contextmanager
    def exclusive(self, candidate):
        yield

    def inspect(self, candidate):
        return self.candidate

    def absent(self, candidate):
        return not self.exists

    def delete(self, candidate):
        self.exists = False
        self.calls += 1
        if self.crash:
            raise RuntimeError("Crash immediately after exact external delete")


def test_crash_after_external_delete_recovers_without_second_delete(bundle, tmp_path):
    bundle = (replace(bundle[0], kind="wav"), *bundle[1:])
    now = AT + timedelta(days=100)
    verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    path, sha = permit(tmp_path, candidate, now)
    adapter = Adapter(candidate)
    adapter.crash = True
    kwargs = dict(
        authorization_path=path,
        authorization_sha256=sha,
        runtime_sha256="a" * 64,
        bindings_sha256="b" * 64,
        checkpoint=lambda: None,
        clock=lambda: now,
    )
    with pytest.raises(RuntimeError):
        evict(candidate, adapter, ledger, **kwargs)
    assert ledger.get(candidate.identity)["state"] == "deleting"
    evict(candidate, adapter, ledger, **kwargs)
    assert ledger.get(candidate.identity)["state"] == "evicted"
    assert adapter.calls == 1


def test_changed_source_blocks_before_intent(bundle, tmp_path):
    bundle = (replace(bundle[0], kind="wav"), *bundle[1:])
    now = AT + timedelta(days=100)
    verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    path, sha = permit(tmp_path, candidate, now)
    adapter = Adapter(replace(candidate, content_sha256="f" * 64))
    with pytest.raises(ArchiveBlocked):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=path,
            authorization_sha256=sha,
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=lambda: now,
        )
    assert adapter.calls == 0 and ledger.get(candidate.identity)["state"] == "verified"


def test_restore_uses_only_archive(bundle, tmp_path):
    verified(bundle, AT + timedelta(days=2))
    candidate, path, _, ledger, box, rows = bundle
    path.unlink()
    output = tmp_path / "restored"
    report = restore(candidate.identity, output, ledger, box, checkpoint=lambda: None)
    assert report["source_accesses"] == 0
    assert [
        codec.decode(line) for line in (output / "0000.lossless.jsonl").read_bytes().splitlines()
    ] == rows


def test_cache_coalesces_concurrent_downloads(bundle, tmp_path):
    receipt = verified(bundle, AT + timedelta(days=2))
    box = bundle[4]
    cache = Cache(tmp_path / "cache")
    before = box.gets
    errors = []

    def read():
        try:
            with cache.file(receipt["files"][0], box) as path:
                assert checksum(path) == receipt["files"][0]["sha256"]
                time.sleep(0.01)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors and box.gets - before == 1


def test_duckdb_tenant_filter_and_evicted_only(bundle, tmp_path):
    receipt = verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, rows = bundle
    kwargs = dict(
        environment="staging",
        source="gateway",
        organization=ORG,
        dataset="visual_reading",
        sensor=SENSOR,
        start=AT,
        end=AT + timedelta(days=1),
    )
    cache = Cache(tmp_path / "cache")
    assert not archived_rows(ledger, cache, box, **kwargs)
    ledger.save(candidate, "deleting", receipt, AT + timedelta(days=2))
    ledger.save(candidate, "evicted", receipt, AT + timedelta(days=2))
    assert archived_rows(ledger, cache, box, **kwargs) == [rows[0]["body"]["row"]]
    assert not archived_rows(ledger, cache, box, **(kwargs | {"organization": str(uuid.uuid4())}))


def test_weighted_moments_and_extremes():
    cold = [
        {
            "kind": "t",
            "unit": "C",
            "data": [
                {
                    "timestamp": AT.isoformat(),
                    "resolution_seconds": 600,
                    "reading_count": 2,
                    "value": 3,
                    "rms": 10**0.5,
                    "minimum": 2,
                    "maximum": 4,
                }
            ],
        }
    ]
    live = [
        {
            "kind": "t",
            "unit": "C",
            "data": [
                {
                    "timestamp": AT.isoformat(),
                    "resolution_seconds": 600,
                    "reading_count": 1,
                    "value": 9,
                    "rms": 9,
                    "minimum": 9,
                    "maximum": 9,
                }
            ],
        }
    ]
    point = merge(live, cold, SENSOR, source="gateway")[0]["data"][0]
    assert point["value"] == 5 and point["reading_count"] == 3
    assert point["minimum"] == 2 and point["maximum"] == 9
    assert point["rms"] == pytest.approx((101 / 3) ** 0.5)


@pytest.mark.parametrize(
    "key", ["../../secret", "production/tiered/a/../secret", "http://example.org/data"]
)
def test_archive_path_traversal_blocked(key):
    with pytest.raises(ArchiveBlocked):
        validate_key(key)


def test_encrypted_backup_authenticates_end_and_key(tmp_path):
    from app.tiered_archive.backup import decrypt, encrypt

    original = tmp_path / "database.dump"
    original.write_bytes(b"database-private-value\0" * 100000)
    sealed = tmp_path / "database.encrypted"
    key = b"x" * 32
    metadata = encrypt(original, sealed, key, checkpoint=lambda: None)
    assert b"database-private-value" not in sealed.read_bytes()
    restored = tmp_path / "restored.dump"
    decrypt(sealed, restored, key, metadata["plaintext_sha256"], checkpoint=lambda: None)
    assert restored.read_bytes() == original.read_bytes()
    with pytest.raises(ArchiveBlocked):
        decrypt(
            sealed,
            tmp_path / "wrong-key",
            b"y" * 32,
            metadata["plaintext_sha256"],
            checkpoint=lambda: None,
        )
    sealed.write_bytes(sealed.read_bytes()[:-20])
    with pytest.raises(ArchiveBlocked):
        decrypt(
            sealed,
            tmp_path / "truncated",
            key,
            metadata["plaintext_sha256"],
            checkpoint=lambda: None,
        )


def test_catalogue_can_be_restored_without_source_or_local_ledger(bundle, tmp_path):
    from app.tiered_archive.registry_backup import backup
    from app.tiered_archive.registry_backup import restore as restore_registry

    verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    reference = backup(ledger, box, checkpoint=lambda: None)
    recovered = Ledger(tmp_path / "recovered-state")
    try:
        with pytest.raises(ArchiveBlocked, match="environment"):
            restore_registry(
                reference,
                recovered,
                box,
                checkpoint=lambda: None,
                expected_environment="production",
            )
        assert recovered.status() == {}
        result = restore_registry(
            reference, recovered, box, checkpoint=lambda: None, expected_environment="staging"
        )
        assert result == {"restored_bundles": 1, "source_accesses": 0}
        assert recovered.get(candidate.identity) == ledger.get(candidate.identity)
        readonly = Ledger(tmp_path / "recovered-state", read_only=True)
        try:
            assert readonly.get(candidate.identity) == ledger.get(candidate.identity)
            with pytest.raises(ArchiveBlocked):
                readonly.save(candidate, "verified", {})
        finally:
            readonly.close()
    finally:
        recovered.close()


def test_snapshot_expiry_during_deletion_checkpoint_blocks(bundle, tmp_path):
    bundle = (replace(bundle[0], kind="wav"), *bundle[1:])
    verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    now = AT + timedelta(days=100)
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    path, sha = permit(tmp_path, candidate, now)
    times = iter([now, now + timedelta(days=2)])
    adapter = Adapter(candidate)
    with pytest.raises(ArchiveBlocked):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=path,
            authorization_sha256=sha,
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=lambda: next(times),
        )
    assert adapter.calls == 0


def test_old_pruning_does_not_bypass_new_controller(monkeypatch):
    from app.direct.retention import run_retention
    from app.visualization.worker import prune_chunk

    monkeypatch.setenv("TIERED_ARCHIVE_ENABLED", "true")
    assert prune_chunk(None, {}) is False
    assert run_retention(None, None, None)["tiered_archive_owns_retention"] is True


def test_repeated_publication_preserves_verified_proof(bundle):
    receipt = verified(bundle, AT + timedelta(days=2))
    candidate, path, metadata, ledger, box, _ = bundle
    again = publish(
        candidate,
        [(path, metadata)],
        box,
        ledger,
        checkpoint=lambda: None,
        now=AT + timedelta(days=3),
    )
    assert again["first_verified_at"] == receipt["first_verified_at"]
    assert again["verified_at"] == receipt["verified_at"]
    assert again["snapshot"] == receipt["snapshot"]


def test_feature_query_uses_recording_time_despite_late_processing(bundle, tmp_path):
    original, _, _, ledger, box, _ = bundle
    rows = []
    for number in range(3):
        started = AT + timedelta(hours=number)
        rows.append(
            {
                "identity": str(number),
                "organization": ORG,
                "sensor": SENSOR,
                "time_us": int(started.timestamp() * 1_000_000),
                "body": {
                    "row": {
                        "id": str(number),
                        "started_at": started,
                        "ended_at": started + timedelta(minutes=1),
                        "is_anomaly": number == 0,
                    },
                    "context": {},
                },
            }
        )
    path = tmp_path / "features.parquet"
    metadata = dict(parquet.write(path, rows), format="parquet")
    candidate = replace(
        original,
        dataset="wav_feature",
        kind="features",
        start=AT + timedelta(days=10),
        end=AT + timedelta(days=11),
        content_sha256=metadata["content_sha256"],
        rows=3,
    )
    publish(
        candidate,
        [(path, metadata)],
        box,
        ledger,
        checkpoint=lambda: None,
        now=AT + timedelta(days=12),
    )
    receipt = verify_snapshot(
        candidate,
        box,
        ledger,
        Provider(AT + timedelta(days=12)),
        3,
        checkpoint=lambda: None,
        now=AT + timedelta(days=12),
    )
    ledger.save(candidate, "deleting", receipt)
    ledger.save(candidate, "evicted", receipt)
    kwargs = dict(
        environment="staging",
        source="gateway",
        organization=ORG,
        dataset="wav_feature",
        sensor=SENSOR,
        start=AT,
        end=AT + timedelta(days=1),
        maximum_rows=1,
        newest=True,
    )
    result = archived_rows(ledger, Cache(tmp_path / "cache"), box, **kwargs)
    assert len(result) == 1 and result[0]["id"] == "2"
    anomaly = archived_rows(ledger, Cache(tmp_path / "cache"), box, **kwargs, anomalies_only=True)
    assert len(anomaly) == 1 and anomaly[0]["id"] == "0"


def test_staging_installer_rejects_production_or_shared_journal():
    import runpy

    installer = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/tiered-archive/install-staging.py")
    )
    profile = {
        "RAW_ARCHIVE_ENVIRONMENT": "staging",
        "TIERED_ARCHIVE_ENVIRONMENT": "staging",
        "RAW_ARCHIVE_GATEWAY_DATABASE_URL": "postgresql://127.0.0.1:26432/gateway_staging",
        "RAW_ARCHIVE_DIRECT_DATABASE_URL": "postgresql://127.0.0.1:26433/direct_staging",
        "RAW_ARCHIVE_DIRECT_S3_BUCKET": "greenmind-direct-staging-test",
        "TIERED_ARCHIVE_STATE_DIR": "/var/lib/greenmind/tiered-archive/staging",
        "RAW_ARCHIVE_STATE_DIR": "/var/lib/greenmind/tiered-archive/staging/raw",
    }
    schedule = {"schema": 1, "environment": "staging"}
    profile.update(
        TIERED_ARCHIVE_SOURCE_ORIGIN="isolated_staging_copy",
        RAW_ARCHIVE_WORKER_ROLE="isolated-staging",
        RAW_ARCHIVE_GATEWAY_S3_ENDPOINT="http://127.0.0.1:26900",
        RAW_ARCHIVE_DIRECT_S3_ENDPOINT="http://127.0.0.1:26900",
        RAW_ARCHIVE_SFTP_USER="u123-sub1",
        RAW_ARCHIVE_SFTP_HOST="u123-sub1.your-storagebox.de",
        RAW_ARCHIVE_SFTP_ROOT="greenmind-staging",
        RAW_ARCHIVE_GATEWAY_S3_BUCKET="greenmind-gateway-staging-20261026",
        RAW_ARCHIVE_MAX_LOAD_PER_CPU="0.5",
        RAW_ARCHIVE_HEALTH_URLS="http://127.0.0.1:26812/health,http://127.0.0.1:26803/health",
        TIERED_ARCHIVE_MAX_WAV_BUNDLE_BYTES=str(5 * 1024**2),
        TIERED_ARCHIVE_MAX_SQL_PAGE_BYTES=str(1024**2),
    )
    installer["validate"](profile, schedule)
    for key, value in [
        ("TIERED_ARCHIVE_DELETE_ENABLED", "true"),
        ("RAW_ARCHIVE_ENVIRONMENT", "production"),
        ("RAW_ARCHIVE_GATEWAY_DATABASE_URL", "postgresql://localhost/plantdb"),
        ("RAW_ARCHIVE_STATE_DIR", "/var/lib/greenmind/archive-copy-20261010"),
        ("TIERED_ARCHIVE_SOURCE_ORIGIN", "production_read_only"),
        ("RAW_ARCHIVE_GATEWAY_S3_ENDPOINT", "http://188.245.247.156:9000"),
        ("TIERED_ARCHIVE_MAX_WAV_BUNDLE_BYTES", str(64 * 1024**2)),
        ("TIERED_ARCHIVE_MAX_SQL_PAGE_BYTES", str(8 * 1024**2)),
        ("RAW_ARCHIVE_WORKER_ROLE", "copy"),
    ]:
        with pytest.raises(ValueError):
            installer["validate"](profile | {key: value}, schedule)
    for addition in ({"backups": ["gateway"]}, {"scheduled_snapshots": True}):
        with pytest.raises(ValueError, match="excludes inline"):
            installer["validate"](profile, schedule | addition)


def test_cold_query_preserves_overlapping_left_bucket(bundle, tmp_path):
    from app.tiered_archive.reader import cold_data

    receipt = verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    ledger.save(candidate, "deleting", receipt)
    ledger.save(candidate, "evicted", receipt)
    packet = cold_data(
        ledger,
        Cache(tmp_path / "cache"),
        box,
        environment="staging",
        source="gateway",
        organization=ORG,
        dataset="visual_reading",
        sensor=SENSOR,
        start=AT + timedelta(minutes=5),
        end=AT + timedelta(minutes=10),
        step=600,
    )
    assert packet["series"][0]["data"][0]["timestamp"] == AT.isoformat()


def test_partial_cold_wave_stats_merge_with_local_wave_without_changing_mean():
    from app.tiered_archive.reader import apply_waves

    live = [
        {
            "kind": "bio_signal",
            "unit": "mV",
            "data": [
                {
                    "timestamp": AT.isoformat(),
                    "resolution_seconds": 600,
                    "value": 3.0,
                    "reading_count": 2,
                    "_wave": {
                        "n": 100,
                        "total": 300.0,
                        "total2": 1000.0,
                        "minimum": 2.0,
                        "maximum": 4.0,
                        "duration": 300.0,
                    },
                }
            ],
        }
    ]
    wave_rows = [
        {
            "kind": "bio_signal",
            "unit": "mV",
            "bucket": AT,
            "seconds": 600,
            "n": 100,
            "total": 500.0,
            "total2": 2600.0,
            "minimum": 4.0,
            "maximum": 6.0,
            "duration": 300.0,
        }
    ]
    result = apply_waves(live, points(wave_rows, source="gateway", step=600))[0]["data"][0]
    assert result["value"] == 3.0 and result["reading_count"] == 2
    assert result["minimum"] == 2 and result["maximum"] == 6 and result["coverage_ratio"] == 1
    assert result["rms"] == round(18**0.5, 4)


def test_pending_wave_points_merge_without_none_arithmetic():
    row = {
        "timestamp": AT.isoformat(),
        "resolution_seconds": 600,
        "reading_count": 1,
        "value": 2.0,
        "rms": None,
        "minimum": None,
        "maximum": None,
        "_total": 2.0,
        "_total2": 4.0,
    }
    result = merge(
        [{"kind": "bio_signal", "unit": "mV", "data": [dict(row)]}],
        [{"kind": "bio_signal", "unit": "mV", "data": [dict(row)]}],
        SENSOR,
        source="gateway",
    )
    assert result[0]["data"][0]["reading_count"] == 2


def test_projected_statistics_cannot_differ_from_original(bundle):
    import pyarrow as pa
    import pyarrow.parquet as pq

    _, path, metadata, _, _, _ = bundle
    table = pq.read_table(path)
    index = table.schema.get_field_index("stat_total")
    table = table.set_column(index, table.schema.field(index), pa.array([999.0]))
    pq.write_table(table, path, compression="zstd")
    forged = dict(metadata, sha256=checksum(path), bytes=path.stat().st_size)
    with pytest.raises(ArchiveBlocked, match="statistics differ"):
        parquet.verify(path, forged)


def test_year_view_projects_moments_beyond_raw_point_limit(bundle, tmp_path, monkeypatch):
    from app.tiered_archive import reader

    original, _, _, ledger, box, seed = bundle

    def rows():
        for number in range(21001):
            at = AT + timedelta(minutes=10 * number)
            yield dict(
                seed[0],
                identity=str(number),
                time_us=codec.epoch_us(at),
                body={"row": dict(seed[0]["body"]["row"], bucket=at), "context": {}},
            )

    path = tmp_path / "year.parquet"
    metadata = dict(parquet.write(path, rows()), format="parquet")
    end = AT + timedelta(days=150)
    candidate = replace(
        original, rows=21001, end=end, settled_at=end, content_sha256=metadata["content_sha256"]
    )
    at = end + timedelta(days=2)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=at)
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(at), 3, checkpoint=lambda: None, now=at
    )
    ledger.save(candidate, "deleting", receipt, at)
    ledger.save(candidate, "evicted", receipt, at)
    monkeypatch.setattr(
        reader, "decode", lambda _: pytest.fail("Year queries must not decode payload blobs")
    )
    scope = dict(
        environment="staging",
        source="gateway",
        organization=ORG,
        dataset="visual_reading",
        sensor=SENSOR,
        start=AT,
        end=end,
        step=3600,
    )
    result = reader.archived_statistics(ledger, Cache(tmp_path / "year-cache"), box, **scope)
    assert len(result) == 3501
    assert sum(row["n"] for row in result) == 42002
    assert all(row["minimum"] == 2 and row["maximum"] == 4 for row in result)
    assert not reader.archived_statistics(
        ledger, Cache(tmp_path / "year-cache"), box, **(scope | {"organization": str(uuid.uuid4())})
    )


def test_changed_deployment_binding_cannot_delete(bundle, tmp_path):
    bundle = (replace(bundle[0], kind="wav"), *bundle[1:])
    now = AT + timedelta(days=100)
    verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    path, sha = permit(tmp_path, candidate, now)
    adapter = Adapter(candidate)
    with pytest.raises(ArchiveBlocked, match="differs from accepted deployment"):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=path,
            authorization_sha256=sha,
            runtime_sha256="a" * 64,
            bindings_sha256="c" * 64,
            checkpoint=lambda: None,
            clock=lambda: now,
        )
    assert adapter.calls == 0


def test_cache_preserves_disk_safety_reserve(bundle, tmp_path, monkeypatch):
    from app.tiered_archive import storage

    receipt = verified(bundle, AT + timedelta(days=2))
    box = bundle[4]
    before = box.gets
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 1024})())
    with pytest.raises(ArchiveBlocked, match="disk safety reserve"):
        with Cache(tmp_path / "full-cache").file(receipt["files"][0], box):
            pytest.fail("Low disk space must stop before downloading")
    assert box.gets == before


def test_pilot_cannot_increase_limits():
    from app.tiered_archive.deletion import limits

    assert limits({"mode": "pilot", "limits": {"wavs": 100}})["wavs"] == 10
    with pytest.raises(ArchiveBlocked):
        limits({"mode": "automatic", "limits": {"wav_bytes": 1024**3}})


def test_staging_reader_rejects_upload_credentials():
    import runpy

    validate = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/tiered-archive/install-staging.py")
    )["validate_reader"]
    profile = {
        "TIERED_ARCHIVE_ENVIRONMENT": "staging",
        "RAW_ARCHIVE_ENVIRONMENT": "staging",
        "TIERED_ARCHIVE_READS_ENABLED": "true",
        "RAW_ARCHIVE_READ_ONLY_SFTP_ROOT": "greenmind-staging",
        "TIERED_ARCHIVE_STATE_DIR": "/var/lib/greenmind/tiered-archive/staging",
        "TIERED_ARCHIVE_READ_BROKER_SOCKET": "/run/greenmind-tiered-archive-staging/history.sock",
        **{
            "RAW_ARCHIVE_READ_ONLY_SFTP_" + key: "test"
            for key in ("USER", "HOST", "KEY", "KNOWN_HOSTS")
        },
    }
    validate(profile, profile)
    with pytest.raises(ValueError, match="upload or database"):
        validate(profile | {"RAW_ARCHIVE_SFTP_KEY": "/secret"}, profile)


def test_private_history_broker_reads_only_evicted_tenant_scope(bundle, tmp_path, monkeypatch):
    from app.raw_archive import read_broker
    from app.raw_archive.worker import configuration
    from app.tiered_archive.client import request
    from app.tiered_archive.read_service import ReadHandler

    receipt = verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    ledger.save(candidate, "deleting", receipt)
    ledger.save(candidate, "evicted", receipt)
    import tempfile

    short = tempfile.TemporaryDirectory(prefix="gm-sock-", dir="/private/tmp")
    path = Path(short.name) / "history.sock"
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(ledger.root))
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_READ_BROKER_SOCKET", str(path))
    health_calls = []
    monkeypatch.setattr(read_broker, "healthy", lambda: pytest.fail("Legacy health must not run"))
    payload = dict(
        source="gateway",
        organization=ORG,
        sensor=SENSOR,
        start=AT.isoformat(),
        end=(AT + timedelta(days=1)).isoformat(),
        step=600,
    )
    with read_broker.Server(
        str(path),
        configuration(),
        box,
        tmp_path / "scratch",
        health_check=lambda: health_calls.append("bound-staging-health"),
    ) as server:
        server.RequestHandlerClass = ReadHandler
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection, response = request("/tiered-series", payload)
            try:
                result = json.loads(response.read())
                assert result["series"][0]["data"][0]["reading_count"] == 2
            finally:
                connection.close()
            connection, response = request(
                "/tiered-series", payload | {"organization": str(uuid.uuid4())}
            )
            try:
                assert json.loads(response.read()) == {"series": [], "waves": []}
            finally:
                connection.close()
            assert health_calls == ["bound-staging-health", "bound-staging-health"]
        finally:
            server.shutdown()
            thread.join(timeout=5)
            short.cleanup()


def test_unfinished_eviction_returns_error_instead_of_missing_history(bundle, tmp_path):
    from app.tiered_archive.reader import archived_statistics

    receipt = verified(bundle, AT + timedelta(days=2))
    candidate, _, _, ledger, box, _ = bundle
    ledger.save(candidate, "deleting", receipt)
    scope = dict(
        environment="staging",
        source="gateway",
        organization=ORG,
        dataset="visual_reading",
        sensor=SENSOR,
        start=AT,
        end=AT + timedelta(days=1),
    )
    with pytest.raises(ArchiveBlocked, match="ownership transition"):
        archived_statistics(ledger, Cache(tmp_path / "cache"), box, **scope, step=600)
    with pytest.raises(ArchiveBlocked, match="ownership transition"):
        archived_rows(ledger, Cache(tmp_path / "cache"), box, **scope)


def test_wav_full_decode_must_match_feature_sample_count(tmp_path):
    import wave

    from app.tiered_archive.pipeline import verify_file

    path = tmp_path / "original.wav"
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 100, 0, "NONE", "not compressed"))
        out.writeframes(b"\0" * 200)
    reference = dict(
        format="wav",
        bytes=path.stat().st_size,
        sha256=checksum(path),
        wav_header=dict(frames=100, rate=100, channels=1, width=2),
    )
    verify_file(path, reference, lambda: None)
    with pytest.raises(ArchiveBlocked, match="sample count"):
        verify_file(
            path, dict(reference, wav_header=dict(reference["wav_header"], frames=99)), lambda: None
        )


def test_master_backup_proof_must_match_source_destination_and_be_current():
    import copy

    from app.tiered_archive.backup import MASTER_PROOF_VERSION, require_current

    now = AT + timedelta(days=100)
    receipt = {
        "state": "verified",
        "policy": POLICY_SHA256,
        "destination": "box-a",
        "files": [{"master_proof_version": MASTER_PROOF_VERSION}],
        "source": {
            "dataset": "database_backup",
            "source": "gateway",
            "environment": "staging",
            "end": (now - timedelta(hours=2)).isoformat(),
        },
        "verified_at": (now - timedelta(hours=1)).isoformat(),
        "snapshot": {
            "provider_id": 3,
            "box_id": 4,
            "created_at": (now - timedelta(minutes=90)).isoformat(),
            "checked_at": (now - timedelta(hours=1)).isoformat(),
        },
    }
    kwargs = dict(source="gateway", environment="staging", destination="box-a", now=now)
    assert require_current([receipt], **kwargs) == receipt
    for change in (
        {"state": "published"},
        {"files": []},
        {"files": [{"master_proof_version": 1}]},
        {"files": [{"master_proof_version": 2}]},
        {"files": [{"master_proof_version": True}]},
        {"destination": "box-b"},
        {
            "snapshot": {
                key: value for key, value in receipt["snapshot"].items() if key != "checked_at"
            }
        },
        {"snapshot": dict(receipt["snapshot"], checked_at=(now + timedelta(hours=1)).isoformat())},
        {"verified_at": (now + timedelta(hours=1)).isoformat()},
        {"policy": "0" * 64},
        {"source": dict(receipt["source"], source="direct")},
        {"source": dict(receipt["source"], end=(now - timedelta(hours=25)).isoformat())},
    ):
        with pytest.raises(ArchiveBlocked, match="master-data backup"):
            require_current([copy.deepcopy(receipt) | change], **kwargs)
    with pytest.raises(ArchiveBlocked):
        require_current([], **kwargs)


def test_source_s3_deletion_cannot_fall_back_to_copy_credentials(monkeypatch):
    from app.tiered_archive.runtime import writer_s3_credentials

    monkeypatch.delenv("TIERED_ARCHIVE_GATEWAY_WRITER_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TIERED_ARCHIVE_GATEWAY_WRITER_S3_SECRET_KEY", raising=False)
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_S3_ACCESS_KEY", "copy-only")
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_S3_SECRET_KEY", "copy-only")
    with pytest.raises(ArchiveBlocked, match="Separate source deletion"):
        writer_s3_credentials("gateway")


def test_backup_schedule_survives_restart_and_waits_twelve_hours(tmp_path, monkeypatch):
    from app.tiered_archive import runtime, schedule

    root = tmp_path / "state"
    calls = []
    monkeypatch.setattr(schedule, "environment", lambda: "staging")
    monkeypatch.setattr(schedule, "state_root", lambda: root)

    class Healthy:
        def __call__(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(schedule, "health_probe", lambda kind: Healthy())
    monkeypatch.setattr(runtime, "state_root", lambda: root)
    monkeypatch.setattr(runtime, "backup", lambda source: calls.append(source))

    @contextmanager
    def worker():
        ledger = Ledger(root)
        try:
            yield ledger, None, lambda: None
        finally:
            ledger.close()

    monkeypatch.setattr(runtime, "worker", worker)
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    path = tmp_path / "schedule.json"
    path.write_text(
        json.dumps(dict(schema=1, environment="staging", backups=["gateway", "direct"]))
    )
    path.chmod(0o600)
    assert schedule.tick(path, clock=lambda: AT)["published"] == 2
    assert schedule.tick(path, clock=lambda: AT + timedelta(hours=1))["published"] == 0
    assert schedule.tick(path, clock=lambda: AT + timedelta(hours=13))["published"] == 2
    assert calls == ["gateway", "direct", "gateway", "direct"]


def test_release_verification_rejects_unlisted_executable_content(tmp_path):
    import hashlib
    import runpy

    verify = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/tiered-archive/install-staging.py")
    )["verify"]
    release = tmp_path / "release"
    release.mkdir()
    (release / "main.py").write_text("safe=1\n")
    manifest = release / "release.json"
    manifest.write_text(json.dumps({"files": {"main.py": checksum(release / "main.py")}}))
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert verify(release, expected)["files"]
    (release / "sitecustomize.py").write_text("unexpected=1\n")
    with pytest.raises(ValueError, match="Unexpected"):
        verify(release, expected)


@pytest.mark.parametrize("step", [600, 3600, 604800])
@pytest.mark.parametrize(
    "source,dataset", [("gateway", "visual_reading"), ("direct", "direct_visual_point")]
)
def test_cold_window_filters_original_intervals_before_display_buckets(
    bundle, tmp_path, step, source, dataset
):
    from app.tiered_archive.reader import cold_data

    original, _, _, ledger, box, seed = bundle
    rows = []
    # The first ends exactly at the left boundary; the last starts at the
    # exclusive right boundary. Rebinning must not pull either into the window.
    for index, (offset, seconds, value) in enumerate(
        [(240, 60, 900), (299, 60, 2), (330, 1, 4), (360, 60, 800)]
    ):
        stamp = AT + timedelta(seconds=offset)
        rows.append(
            dict(
                seed[0],
                identity=str(index),
                time_us=codec.epoch_us(stamp),
                body={
                    "row": dict(
                        seed[0]["body"]["row"],
                        bucket=stamp,
                        seconds=seconds,
                        n=1,
                        total=value,
                        total2=value * value,
                        minimum=value,
                        maximum=value,
                    ),
                    "context": {},
                },
            )
        )
    path = tmp_path / "boundaries.parquet"
    metadata = dict(parquet.write(path, rows), format="parquet")
    candidate = replace(
        original,
        source=source,
        dataset=dataset,
        rows=len(rows),
        content_sha256=metadata["content_sha256"],
    )
    now = AT + timedelta(days=2)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=now)
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(now), 3, checkpoint=lambda: None, now=now
    )
    ledger.save(candidate, "deleting", receipt)
    ledger.save(candidate, "evicted", receipt)
    scope = dict(
        environment="staging",
        source=source,
        organization=ORG,
        dataset=dataset,
        sensor=SENSOR,
        start=AT + timedelta(seconds=300),
        end=AT + timedelta(seconds=360),
        step=step,
    )
    result = cold_data(ledger, Cache(tmp_path / "boundary-cache"), box, **scope)["series"]
    assert len(result) == 1 and len(result[0]["data"]) == 1
    point = result[0]["data"][0]
    assert point["reading_count"] == 2
    assert point["value"] == 3
    assert (point["minimum"], point["maximum"]) == (2, 4)
    assert point["rms"] == pytest.approx(10**0.5)
    assert point["resolution_seconds"] == step
    assert not cold_data(
        ledger,
        Cache(tmp_path / "boundary-cache"),
        box,
        **(scope | {"organization": str(uuid.uuid4())}),
    )["series"]


@pytest.mark.parametrize("source", ["gateway", "direct"])
@pytest.mark.parametrize("days", [1, 7, 90, 365, 3650])
def test_http_chart_and_csv_share_hot_cold_values(bundle, tmp_path, monkeypatch, source, days):
    """Real routes, archive files and merge; SQL/authentication are fixture adapters.

    This proves response parity and the authorization call barrier, not live
    PostgreSQL rollups, authentication, or production tenant provisioning.
    """
    import csv
    import io
    import zipfile
    from types import SimpleNamespace

    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.database import get_db
    from app.tiered_archive import runtime
    from app.visualization import api, direct, direct_api
    from app.visualization.resolution import display_step

    original, path, metadata, ledger, box, _ = bundle
    candidate = replace(
        original,
        source=source,
        dataset="visual_reading" if source == "gateway" else "direct_visual_point",
    )
    now = AT + timedelta(days=2)
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=now)
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(now), 3, checkpoint=lambda: None, now=now
    )
    ledger.save(candidate, "deleting", receipt)
    ledger.save(candidate, "evicted", receipt)
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "staging")
    for name in ("TIERED_ARCHIVE_READ_BROKER_SOCKET", "RAW_ARCHIVE_READ_BROKER_SOCKET"):
        monkeypatch.delenv(name, raising=False)
    reads = []

    def resources():
        reads.append(1)
        return Ledger(ledger.root, read_only=True), Cache(tmp_path / "http-cache"), box

    monkeypatch.setattr(runtime, "reader_resources", resources)
    end = AT + timedelta(days=days)
    step = display_step(AT, end)

    def local(*_):
        return points(
            [
                dict(
                    kind="temperature",
                    unit="C",
                    bucket=AT,
                    seconds=600,
                    n=1,
                    total=9,
                    total2=81,
                    minimum=9,
                    maximum=9,
                )
            ],
            source=source,
            step=step,
        )

    monkeypatch.setattr(api, "_query_local_series", local)
    monkeypatch.setattr(direct, "_query_local_series", local)
    allowed = True

    def authorize(*_):
        if not allowed:
            raise HTTPException(404, "Sensor not found")
        return {"mode": "DIRECT"}

    monkeypatch.setattr(api, "authorize", authorize)
    monkeypatch.setattr(direct_api, "authorize", authorize)
    monkeypatch.setattr(api.limiter, "enabled", False)
    result = SimpleNamespace(scalar_one=lambda: ORG, scalar=lambda: None)
    result.mappings = lambda: SimpleNamespace(
        one=lambda: {"sensor": {}, "gateway": {}, "zone": {}}, all=lambda: []
    )
    db = SimpleNamespace(execute=lambda *_args, **_kwargs: result)
    overrides = dict(api.app.dependency_overrides)
    api.app.dependency_overrides[get_db] = lambda: db
    api.app.dependency_overrides[direct_api.connection] = lambda: db
    api.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="fixture-user")
    prefix = (
        f"/api/v1/sensors/{SENSOR}"
        if source == "gateway"
        else f"/api/v1/visualization/direct/{SENSOR}"
    )
    params = {"from_dt": AT.isoformat(), "to_dt": end.isoformat()}
    try:
        with TestClient(api.app) as client:
            chart = client.get(prefix + "/data", params=params)
            exported = client.get(prefix + "/export", params=params)
            assert chart.status_code == exported.status_code == 200
            assert chart.headers["cache-control"] == "private, no-store"
            point = chart.json()[0]["data"][0]
            assert point["reading_count"] == 3 and point["value"] == 5
            assert point["minimum"] == 2 and point["maximum"] == 9
            assert point["resolution_seconds"] == max(600, step)
            assert not any(key.startswith("_") for key in point)
            if source == "gateway":
                with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                    content = archive.read(archive.namelist()[0]).decode()
            else:
                content = exported.text
            rows = list(
                csv.DictReader(line for line in content.splitlines() if not line.startswith("#"))
            )
            assert len(rows) == 1
            row = rows[0]
            assert row["timestamp_utc"] == point["timestamp"]
            for field in ("minimum", "maximum", "rms", "standard_deviation", "resolution_seconds"):
                assert float(row[field]) == pytest.approx(point[field])
            assert float(row["value" if source == "gateway" else "mean"]) == point["value"]
            assert (
                int(row["reading_count" if source == "gateway" else "sample_count"])
                == point["reading_count"]
            )
            expected_reads = 2
            if days == 365:
                from concurrent.futures import ThreadPoolExecutor

                def repeated(index):
                    route = "/data" if index % 2 == 0 else "/export"
                    response = client.get(prefix + route, params=params)
                    assert response.status_code == 200
                    if route == "/data":
                        assert response.json() == chart.json()
                    else:
                        assert response.content  # CSV/ZIP was fully assembled.

                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(repeated, range(8)))
                expected_reads += 8
            assert len(reads) == expected_reads
            allowed = False
            assert client.get(prefix + "/data", params=params).status_code == 404
            assert client.get(prefix + "/export", params=params).status_code == 404
            assert len(reads) == expected_reads  # A rejected caller cannot reach the archive.
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(overrides)


def test_SQL_archive_copy_restore_survives_but_WAV_release_never_deletes_SQL(bundle, tmp_path):
    now = AT + timedelta(days=100)
    verified(bundle, AT + timedelta(days=2))
    candidate, source, _, ledger, box, rows = bundle
    verify_snapshot(candidate, box, ledger, Provider(now), 4, checkpoint=lambda: None, now=now)
    before = checksum(source)
    path, sha = permit(tmp_path, candidate, now)
    adapter = Adapter(candidate)
    with pytest.raises(ArchiveBlocked, match="Only source WAV"):
        evict(
            candidate,
            adapter,
            ledger,
            authorization_path=path,
            authorization_sha256=sha,
            runtime_sha256="a" * 64,
            bindings_sha256="b" * 64,
            checkpoint=lambda: None,
            clock=lambda: now,
        )
    assert adapter.calls == 0 and adapter.exists and checksum(source) == before
    assert ledger.get(candidate.identity)["state"] == "verified"
    output = tmp_path / "SQL-archive-restore-still-supported"
    restored = restore(candidate.identity, output, ledger, box, checkpoint=lambda: None)
    assert restored["source_accesses"] == 0
    assert [
        codec.decode(line) for line in (output / "0000.lossless.jsonl").read_bytes().splitlines()
    ] == rows
