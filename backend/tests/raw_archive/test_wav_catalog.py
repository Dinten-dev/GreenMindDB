"""WAV-only exports reject account data and restore all bounded parts."""

import gzip
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive import wav_catalog as catalog
from app.raw_archive.policy import ArchiveBlocked, Config, Ledger, Recording, archive_one

from .test_policy import PAYLOAD, Destination, Source


def fixture_bundle(tmp_path):
    root = tmp_path / "backup"
    root.mkdir(mode=0o700)
    now = datetime.now(UTC)
    record = Recording(
        "gateway",
        "record",
        "greenmind-raw",
        "source.wav",
        __import__("hashlib").sha256(PAYLOAD).hexdigest(),
        len(PAYLOAD),
        now - timedelta(days=20),
        now - timedelta(days=20),
        "a" * 64,
        now - timedelta(days=20),
        "mac-14-c1-9f-d9-42-a4",
    )
    with_ledger = tmp_path / "source"
    ledger = Ledger(with_ledger)
    archive_one(
        Config(enabled=True, root=with_ledger, min_free_bytes=0),
        record,
        Source(),
        Destination(),
        ledger,
        lambda _: None,
        copy_only=True,
    )
    ledger.close()
    entries = catalog.export_ledger(with_ledger / "archive.sqlite3", root, lambda: None)
    tables = {kind: dict.fromkeys(names, 0) for kind, names in catalog.COLUMNS.items()}
    for kind, name, row in (
        ("gateway", "organization", {"id": "company-id"}),
        (
            "direct",
            "direct_device",
            {
                "id": "device-id",
                "organization_id": "company-id",
                "zone_id": "zone-id",
                "legacy_sensor_id": None,
                "mode": "DIRECT",
            },
        ),
    ):
        parts = catalog.Parts(root, kind, lambda: None)
        parts.write(catalog.validate_row(kind, name, row))
        parts.finish()
        entries.extend(parts.files)
        tables[kind][name] = 1
    manifest = {
        "schema": 2,
        "format": catalog.FORMAT,
        "allowlist_sha256": catalog.ALLOWLIST_SHA256,
        "created_at": now.isoformat(),
        "environment": "production",
        "files": entries,
        "tables": tables,
    }
    return root, manifest


def test_offsite_roundtrip_restores_new_archive_database_only(tmp_path):
    class Box:
        identity = "private-test-box"
        bodies = {}

        def publish_catalog(self, path, key):
            self.bodies[key] = path.read_bytes()

        def download_catalog(self, key, path, limit, *, snapshot=None):
            assert len(self.bodies[key]) <= limit
            path.write_bytes(self.bodies[key])

    root, manifest = fixture_bundle(tmp_path)
    box = Box()
    reference = catalog.publish(root, manifest, box, "production")
    proof = catalog.restore_bundle(box, reference, tmp_path / "restore", "production")
    assert proof["format"] == catalog.FORMAT and proof["deleted_files"] == 0
    assert proof["tables"] == manifest["tables"]
    from app.raw_archive.readiness import validate_metadata_restore

    validate_metadata_restore(json.loads(box.bodies[reference["key"]]), proof)
    with sqlite3.connect(tmp_path / "restore" / "wav-catalog.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM archive").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 2
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
    # Content-addressed publication is repeatable; existing backups remain intact.
    assert catalog.publish(root, manifest, box, "production") == reference
    assert all((root / entry["file"]).exists() for entry in manifest["files"])


@pytest.mark.parametrize(
    "name", ["email", "password_hash", "key_hash", "api_key_hash", "name", "description"]
)
def test_top_level_account_and_personal_fields_are_rejected(name):
    with pytest.raises(ArchiveBlocked, match="Unapproved"):
        catalog.validate_row("gateway", "organization", {"id": "id", name: "private"})


def test_nested_feature_secrets_and_session_extras_are_rejected():
    with pytest.raises(ArchiveBlocked):
        catalog.validate_feature({"quantiles": {"email": 1}}, catalog.FEATURE_FIELDS)
    with pytest.raises(ArchiveBlocked):
        catalog.validate_feature(
            {"spectral_bands": {"high_1_10_hz": "private"}}, catalog.FEATURE_FIELDS
        )
    row = dict.fromkeys(catalog.COLUMNS["direct"]["direct_session"])
    row["config"] = dict.fromkeys(catalog.SESSION_FIELDS, 1) | {"token": "private"}
    with pytest.raises(ArchiveBlocked):
        catalog.validate_row("direct", "direct_session", row)


def test_restore_refuses_existing_database_and_tampered_part(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    files = {entry["file"]: root / entry["file"] for entry in manifest["files"]}
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"existing")
    with pytest.raises(ArchiveBlocked, match="new absolute"):
        catalog.restore_parts(manifest, files, existing)
    assert existing.read_bytes() == b"existing"
    path = next(iter(files.values()))
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        catalog.restore_parts(manifest, files, tmp_path / "restore.sqlite3")


def test_parts_rollover_and_preserve_all_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "PART_BYTES", 90)
    parts = catalog.Parts(tmp_path, "gateway", lambda: None)
    for index in range(10):
        parts.write(catalog.validate_row("gateway", "organization", {"id": str(index)}))
    parts.finish()
    assert len(parts.files) > 1 and sum(entry["rows"] for entry in parts.files) == 10
    for entry in parts.files:
        with gzip.open(tmp_path / entry["file"], "rb") as source:
            assert len(source.read()) == entry["decoded_bytes"] <= 90


def test_incomplete_table_manifest_cannot_publish(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    del manifest["tables"]["gateway"]["wav_feature_version"]
    with pytest.raises(ArchiveBlocked, match="omits"):
        catalog.publish(root, manifest, None, "production")


def test_journal_unknown_fields_and_checksum_mismatch_block_export(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    entry = manifest["files"][0]
    with gzip.open(root / entry["file"], "rb") as body:
        row = json.loads(body.readline())
    row["receipt"]["token"] = "private"
    with pytest.raises(ArchiveBlocked, match="Unapproved"):
        catalog.safe_receipt(row)
    del row["receipt"]["token"]
    row["id"] = "changed"
    with pytest.raises(ArchiveBlocked, match="identity"):
        catalog.safe_receipt(row)


def test_resource_pause_precedes_files_or_connections(tmp_path, monkeypatch, capsys):
    from app.raw_archive import runner

    class Probe:
        last = {"reason": "memory"}

        def __call__(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(runner, "health_probe", Probe)
    monkeypatch.setattr("sys.argv", ["wav_catalog", "--output", str(tmp_path / "new")])
    assert catalog.entrypoint() == 75
    assert json.loads(capsys.readouterr().out)["reason"] == "memory"
    assert not (tmp_path / "new").exists()


def test_failed_restore_has_no_success_proof(tmp_path):
    root, manifest = fixture_bundle(tmp_path)
    manifest["files"][0]["rows"] += 1
    with pytest.raises(ArchiveBlocked, match="rows"):
        catalog.restore_parts(
            manifest,
            {entry["file"]: root / entry["file"] for entry in manifest["files"]},
            tmp_path / "new.sqlite3",
        )
    assert not (tmp_path / "restore-proof.json").exists()


def test_restore_cli_publishes_only_actual_success_proof(tmp_path, monkeypatch, capsys):
    from app.raw_archive import runner

    class Probe:
        def __call__(self):
            return True

        def close(self):
            pass

    class Box:
        identity = "private-test-box"
        bodies = {}

        def publish_catalog(self, path, key):
            self.bodies[key] = path.read_bytes()

        def download_catalog(self, key, path, limit, *, snapshot=None):
            path.write_bytes(self.bodies[key])

        def close(self):
            pass

    root, manifest = fixture_bundle(tmp_path)
    box = Box()
    reference = catalog.publish(root, manifest, box, "production")
    source = tmp_path / "reference.json"
    catalog.save_json(source, reference)
    monkeypatch.setattr(runner, "health_probe", Probe)
    monkeypatch.setattr(runner, "destination_from_environment", lambda: box)
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setattr(
        "sys.argv",
        [
            "wav_catalog",
            "--output",
            str(tmp_path / "restore"),
            "--restore-reference",
            str(source),
            "--publish",
        ],
    )
    assert catalog.entrypoint() == 0
    result = json.loads(capsys.readouterr().out)
    proof = json.loads(box.bodies[result["proof"]["key"]])
    assert result["published"] and proof["format"] == catalog.FORMAT
    assert proof["deleted_files"] == 0 and proof["tables"] == manifest["tables"]
