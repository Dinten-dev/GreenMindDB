"""Native archive authentication, compatibility and scope gates without a database."""

import copy
import json
import secrets
from dataclasses import replace
from datetime import timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import backup, parquet, runtime
from app.tiered_archive import native_backup as native
from app.tiered_archive.codec import digest
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.pipeline import candidate_json, publish, verify_snapshot
from app.tiered_archive.recovery import restore
from tests.test_tiered_archive import AT, Box, Provider


def proof(source="gateway"):
    tables = {
        name: {"rows": 0, "bytes": 21, "binary_sha256": "a" * 64}
        for name in backup.master_tables(source)
    }
    return {
        "public_tables": tables,
        "proof_bytes": 21 * len(tables) + 100,
        "structure": {
            name: "b" * 64
            for name in (
                "columns",
                "constraints",
                "indexes",
                "views",
                "extensions",
                "hypertables",
                "chunks",
                "dimensions",
                "compression_settings",
            )
        },
        "sequences": {
            "public.test_id_seq": {
                "settings": [1, 1, 100, 1, 1, False],
                "last_value": 7,
                "is_called": True,
            }
        },
    }


@pytest.fixture(autouse=True)
def no_postgres(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Archive authentication must not open PostgreSQL")

    monkeypatch.setattr(native, "create_engine", forbidden)
    monkeypatch.setattr("app.tiered_archive.database_restore.run_client", forbidden)


@pytest.fixture
def bundle(tmp_path):
    folder = tmp_path / "capture"
    folder.mkdir(mode=0o700)
    plain = folder / "original.dump"
    plain.write_bytes(b"PGDMP synthetic authenticated full data")
    key = secrets.token_bytes(32)
    candidate, files = native.seal(
        "gateway", AT, plain, proof(), "c" * 64, key, folder, checkpoint=lambda: None
    )
    ledger, box = Ledger(tmp_path / "ledger"), Box(tmp_path / "box")
    published = publish(candidate, files, box, ledger, checkpoint=lambda: None, now=AT)
    receipt = verify_snapshot(
        candidate, box, ledger, Provider(AT), 3, checkpoint=lambda: None, now=AT
    )
    output = tmp_path / "restored"
    result = restore(candidate.identity, output, ledger, box, checkpoint=lambda: None)
    yield output, result["manifest_sha256"], key, receipt, published
    ledger.close()


def reindex(folder, manifest, body):
    row = {
        "identity": digest(body),
        "organization": native.SYSTEM_ORG,
        "sensor": "system",
        "time_us": int(AT.timestamp() * 1_000_000),
        "body": body,
    }
    reference = dict(
        parquet.write(folder / "replacement.parquet", [row]),
        format="parquet",
        backup_format=native.FORMAT,
        native_proof_version=1,
        native_proof_sha256=body["native_proof_sha256"],
    )
    (folder / "replacement.parquet").replace(folder / "0000.parquet")
    manifest["files"][0] = reference
    candidate = replace(
        runtime.candidate_from_json(manifest["candidate"]),
        content_sha256=reference["content_sha256"],
    )
    manifest["candidate"], manifest["identity"] = candidate_json(candidate), candidate.identity
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return checksum(folder / "manifest.json")


def read_index(folder, manifest):
    return list(parquet.read(folder / "0000.parquet", manifest["files"][0]))[0]["body"]


def test_regular_publication_snapshot_readback_and_native_authentication(bundle):
    folder, sha, key, receipt, published = bundle
    assert published["state"] == "published" and receipt["state"] == "verified"
    with native.authenticated_bundle(folder, sha, key, checkpoint=lambda: None) as (dump, metadata):
        assert dump.read_bytes() == b"PGDMP synthetic authenticated full data"
        assert metadata["proof"] == proof() and metadata["jobs_proof"] == "c" * 64
        assert metadata["proof_sha256"] == digest(proof())
    assert not list(folder.glob("native-auth-*"))


def test_wrong_key_never_reaches_database_and_removes_decrypted_files(bundle):
    folder, sha, _, _, _ = bundle
    with pytest.raises(ArchiveBlocked, match="authentication"):
        with native.authenticated_bundle(
            folder, sha, secrets.token_bytes(32), checkpoint=lambda: None
        ):
            pytest.fail("Wrong key accepted")
    assert not list(folder.glob("native-auth-*"))


def test_ciphertext_tamper_rejected_even_with_recomputed_outer_hashes(bundle):
    folder, _, key, _, _ = bundle
    manifest = json.loads((folder / "manifest.json").read_text())
    body = read_index(folder, manifest)
    target = folder / "0002.bin"
    data = bytearray(target.read_bytes())
    data[-1] ^= 1
    target.write_bytes(data)
    manifest["files"][2]["sha256"] = checksum(target)
    body["files"][1]["sha256"] = checksum(target)
    sha = reindex(folder, manifest, body)
    with pytest.raises(ArchiveBlocked, match="authentication"):
        with native.authenticated_bundle(folder, sha, key, checkpoint=lambda: None):
            pytest.fail("Forged metadata accepted")
    assert not list(folder.glob("native-auth-*"))


@pytest.mark.parametrize(
    "change", ["source", "database", "capture_time", "dump_hash", "proof_hash", "version"]
)
def test_authenticated_but_wrong_metadata_bindings_are_rejected(bundle, change):
    folder, _, key, _, _ = bundle
    manifest = json.loads((folder / "manifest.json").read_text())
    body = read_index(folder, manifest)
    plain = folder / "edit.json"
    backup.decrypt(
        folder / "0002.bin",
        plain,
        key,
        manifest["files"][2]["plaintext_sha256"],
        checkpoint=lambda: None,
    )
    data = json.loads(plain.read_text())
    field, value = {
        "source": ("source", "direct"),
        "database": ("source_database", "production"),
        "capture_time": ("captured_at", (AT + timedelta(days=1)).isoformat()),
        "dump_hash": ("dump_plaintext_sha256", "f" * 64),
        "proof_hash": ("proof_sha256", "f" * 64),
        "version": ("version", True),
    }[change]
    data[field] = value
    plain.write_text(json.dumps(data))
    ref = backup.encrypt(plain, folder / "changed.bin", key, checkpoint=lambda: None)
    (folder / "changed.bin").replace(folder / "0002.bin")
    plain.unlink()
    manifest["files"][2] = ref
    body["files"][1] = dict(ref)
    sha = reindex(folder, manifest, body)
    with pytest.raises(ArchiveBlocked, match="binding"):
        with native.authenticated_bundle(folder, sha, key, checkpoint=lambda: None):
            pytest.fail("Wrong native binding accepted")
    assert not list(folder.glob("native-auth-*"))


@pytest.mark.parametrize(
    "change",
    ["missing_master", "incomplete_shape", "negative_rows", "boolean_counter", "scope", "budget"],
)
def test_incomplete_proof_rejected_before_sealing(change):
    value = proof()
    if change == "missing_master":
        value["public_tables"].pop("users")
    elif change == "incomplete_shape":
        value["structure"].pop("chunks")
    elif change == "negative_rows":
        value["public_tables"]["users"]["rows"] = -1
    elif change == "boolean_counter":
        value["sequences"]["public.test_id_seq"]["last_value"] = True
    elif change == "scope":
        value["sequences"]["public.test_id_seq;SELECT 1"] = value["sequences"].pop(
            "public.test_id_seq"
        )
    elif change == "budget":
        value["proof_bytes"] = native.MAX + 1
    with pytest.raises(ArchiveBlocked):
        native.validate_proof(value, "gateway")


@pytest.mark.parametrize(
    "url,environment",
    [
        (
            "postgresql+psycopg2://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/plantdb_staging",
            "production",
        ),
        ("postgresql+psycopg2://root:synthetic@127.0.0.1:5433/plantdb_staging", "staging"),
        (
            "postgresql+psycopg2://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/plantdb_staging?host=production",
            "staging",
        ),
        (
            "postgresql+psycopg2://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/production",
            "staging",
        ),
    ],
)
def test_native_source_scope_cannot_expand_to_production_or_writer(url, environment):
    with pytest.raises(ArchiveBlocked):
        native.source_url(url, "gateway", environment)


def test_native_receipt_cannot_authorize_deletion_even_if_legacy_field_is_added(bundle):
    _, _, _, receipt, _ = bundle
    changed = copy.deepcopy(receipt)
    changed["files"][0]["master_proof_version"] = backup.MASTER_PROOF_VERSION
    with pytest.raises(ArchiveBlocked, match="master-data backup"):
        backup.require_current(
            [changed],
            source="gateway",
            environment="staging",
            destination=receipt["destination"],
            now=AT,
        )


def test_production_native_request_rejected_before_worker_or_store(monkeypatch):
    monkeypatch.setattr(runtime, "environment", lambda: "production")
    monkeypatch.setattr(runtime, "worker", lambda: pytest.fail("Opened worker before source gate"))
    with pytest.raises(ArchiveBlocked, match="Staging"):
        runtime.backup("gateway", backup_format=native.FORMAT)


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg2"])
@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_existing_readonly_sources_accepted_with_both_supported_url_spellings(driver, source):
    url = native.source_url(
        f"{driver}://greenmind_archive_staging_ro:synthetic@127.0.0.1:5433/{native.SOURCE_DATABASES[source]}",
        source,
        "staging",
    )
    assert url.database == native.SOURCE_DATABASES[source]


def test_legacy_database_replay_rejects_native_format_before_connection(bundle):
    from app.tiered_archive.database_restore import restore_backup

    folder, sha, key, _, _ = bundle
    with pytest.raises(ArchiveBlocked, match="controlled Timescale"):
        restore_backup(
            folder,
            "postgresql://local:synthetic@127.0.0.1/test_restore_probe",
            sha,
            key,
            checkpoint=lambda: None,
        )


def test_manifest_boolean_version_is_not_format_version_one(bundle):
    folder, _, key, _, _ = bundle
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["version"] = True
    path.write_text(json.dumps(manifest))
    with pytest.raises(ArchiveBlocked, match="versioned native"):
        with native.authenticated_bundle(folder, checksum(path), key, checkpoint=lambda: None):
            pytest.fail("Boolean version accepted")
