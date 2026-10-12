"""Feature recovery is verified independently and cannot authorize RAW deletion."""

import hashlib
import json
import sqlite3

import pytest

from app.raw_archive import feature_backup as features
from app.raw_archive import wav_catalog as catalog
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.storage import catalog_capacity


class Box:
    identity = "private-test-box"

    def __init__(self, *, free=10**12, corrupt=False):
        self.bodies = {}
        self.free, self.corrupt = free, corrupt

    def capacity(self):
        return {"total_bytes": 10**12, "available_bytes": self.free}

    def publish_catalog(self, path, key):
        self.bodies[key] = path.read_bytes()

    def download_catalog(self, key, path, limit):
        body = self.bodies[key]
        assert len(body) <= limit
        path.write_bytes(body if not self.corrupt else b"changed")


def bundle(tmp_path):
    output = tmp_path / "backup"
    output.mkdir(mode=0o700)
    payload = {
        "mean": 12.5,
        "rms": 15.0,
        "extractor_version": "v1",
        "spectral_bands": {"noise_50_nyquist_hz": 0.125},
    }
    row = dict.fromkeys(catalog.COLUMNS["gateway"]["wav_feature_version"])
    row.update(id="feature-1", wav_file_id="wav-1", feature_payload=payload)
    parts = catalog.Parts(output, "gateway", lambda: None)
    parts.write(catalog.validate_row("gateway", "wav_feature_version", row))
    parts.finish()
    tables = {kind: dict.fromkeys(names, 0) for kind, names in features.TABLES.items()}
    tables["gateway"]["wav_feature_version"] = 1
    manifest = {
        "schema": 1,
        "format": features.FORMAT,
        "environment": "production",
        "allowlist_sha256": catalog.ALLOWLIST_SHA256,
        "tables": tables,
        "files": parts.files,
    }
    return output, manifest, payload


def test_features_roundtrip_and_scientific_values(tmp_path):
    output, manifest, payload = bundle(tmp_path)
    box = Box()
    proof = features.publish_and_restore(output, manifest, box)
    assert proof["status"] == "verified"
    assert proof["deleted_files"] == 0 and proof["deletion_readiness"] is False
    assert proof["tables"] == manifest["tables"]
    with sqlite3.connect(output / "readback/features.sqlite3") as db:
        row = json.loads(db.execute("SELECT body FROM records").fetchone()[0])
        assert row["feature_payload"] == payload
    with pytest.raises(ArchiveBlocked):
        catalog.manifest_check(json.loads(box.bodies[proof["reference"]["key"]]))


def test_low_quota_does_not_upload(tmp_path):
    output, manifest, _ = bundle(tmp_path)
    box = Box(free=1024)
    with pytest.raises(SafetyPause, match="storage_box_space"):
        features.publish_and_restore(output, manifest, box)
    assert not box.bodies


def test_verification_uses_separate_read_identity(tmp_path):
    output, manifest, _ = bundle(tmp_path)
    box = Box()

    class Reader:
        download_catalog = box.download_catalog

    def reject(*args):
        pytest.fail("Upload access must not verify the feature bundle")

    box.download_catalog = reject
    proof = features.publish_and_restore(output, manifest, box, verification_box=Reader())
    assert proof["independent_read_identity"] is True


def test_interrupted_readback_can_resume_without_overwriting_evidence(tmp_path):
    output, manifest, _ = bundle(tmp_path)
    box = Box(corrupt=True)
    with pytest.raises(ArchiveBlocked):
        features.publish_and_restore(output, manifest, box)
    first = (output / "readback/manifest.json").read_bytes()
    box.corrupt = False
    assert features.publish_and_restore(output, manifest, box)["status"] == "verified"
    assert (output / "readback/manifest.json").read_bytes() == first


def test_corrupt_remote_manifest_never_creates_success(tmp_path):
    output, manifest, _ = bundle(tmp_path)
    with pytest.raises(ArchiveBlocked, match="manifest readback"):
        features.publish_and_restore(output, manifest, Box(corrupt=True))
    assert not (output / "verified.json").exists()


def test_corrupt_part_or_missing_rows_blocks_restore(tmp_path):
    output, manifest, _ = bundle(tmp_path)
    files = {entry["file"]: output / entry["file"] for entry in manifest["files"]}
    manifest["files"][0]["sha256"] = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ArchiveBlocked, match="checksum"):
        features.restore(manifest, files, tmp_path / "restore.sqlite3")


def test_feature_export_uses_exact_table_allowlist_before_connecting(tmp_path):
    with pytest.raises(ArchiveBlocked, match="Unapproved"):
        catalog.export_metadata(None, "gateway", tmp_path, lambda: None, tables=("users",))


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Size Used Avail (root) %Capacity\n0 0 0 0 0%",
        "Size Used Avail (root) %Capacity\n100 1 99 2 1%",
    ],
)
def test_unknown_or_invalid_quota_blocks(body):
    with pytest.raises(ArchiveBlocked):
        catalog_capacity(body)


def test_sftp_quota_uses_unprivileged_free_bytes():
    assert catalog_capacity(
        'sftp> df "greenmind-raw"\nSize Used Avail (root) %Capacity\n1000000 100000 800000 900000 10%\n'
    ) == {"total_bytes": 1024000000, "available_bytes": 819200000}


def test_empty_features_can_be_verified_without_invented_rows(tmp_path):
    output = tmp_path / "empty"
    output.mkdir(mode=0o700)
    manifest = {
        "schema": 1,
        "format": features.FORMAT,
        "environment": "production",
        "allowlist_sha256": catalog.ALLOWLIST_SHA256,
        "tables": {kind: dict.fromkeys(names, 0) for kind, names in features.TABLES.items()},
        "files": [],
    }
    assert features.publish_and_restore(output, manifest, Box())["compressed_bytes"] == 0


def test_direct_channel_features_preserved(tmp_path):
    from app.direct.protocol import ChunkMetadata

    output, manifest, _ = bundle(tmp_path)
    config = ChunkMetadata(
        protocol_version=1,
        device_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
        sequence=0,
        session_start_us=1791475200000000,
        first_frame=0,
        sample_rate=380,
        channels=1,
        sample_bits=16,
        frame_count=380,
        channel_labels=["electrode"],
        calibration_version="unsigned-mv-linear-int16-v1",
        firmware_version="v1",
        payload_sha256="0" * 64,
    ).session_config()
    channel = {
        "channel": 0,
        "unit": "mV",
        "mean": 25.0,
        "rms": 30.0,
        "spectral_bands": {"noise_50_nyquist_hz": 0.05},
    }
    revision = dict.fromkeys(catalog.MANIFEST_FIELDS)
    revision.update(
        config=config,
        runs=[
            {
                "first_frame": 0,
                "frame_count": 380,
                "started_at_us": 1791475200000000,
                "key": "source.wav",
                "sha256": "0" * 64,
                "features": [channel],
            }
        ],
    )
    row = dict.fromkeys(catalog.COLUMNS["direct"]["direct_revision"])
    row.update(
        segment_id="segment-1",
        revision=1,
        manifest=revision,
        manifest_sha256=hashlib.sha256(catalog.raw_json(revision)).hexdigest(),
    )
    parts = catalog.Parts(output, "direct", lambda: None)
    parts.write(catalog.validate_row("direct", "direct_revision", row))
    parts.finish()
    manifest["files"].extend(parts.files)
    manifest["tables"]["direct"]["direct_revision"] = 1
    proof = features.publish_and_restore(output, manifest, Box())
    assert proof["tables"]["direct"]["direct_revision"] == 1
    with sqlite3.connect(output / "readback/features.sqlite3") as db:
        restored = json.loads(
            db.execute("SELECT body FROM records WHERE kind='direct'").fetchone()[0]
        )
        assert restored["manifest"]["runs"][0]["features"] == [channel]


def test_low_source_memory_opens_no_sql_or_archive(tmp_path, monkeypatch):
    from app.raw_archive import runner

    class Probe:
        last = {"reason": "memory"}

        def __call__(self):
            return False

        def close(self):
            pass

    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "health_probe", lambda: Probe())
    monkeypatch.setattr(
        runner, "destination_from_environment", lambda: pytest.fail("No archive on resource pause")
    )
    monkeypatch.setattr(
        "sqlalchemy.create_engine", lambda *a, **k: pytest.fail("No SQL on resource pause")
    )
    monkeypatch.setattr("sys.argv", ["feature_backup", "--output", str(tmp_path / "backup")])
    with pytest.raises(SafetyPause, match="memory"):
        features.main()
    assert not (tmp_path / "backup").exists()
