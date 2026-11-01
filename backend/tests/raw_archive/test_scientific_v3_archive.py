"""Local-only failure and resume tests; no server calls."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.raw_archive import scientific_parts as parts
from app.raw_archive import scientific_v3 as science
from app.raw_archive.policy import ArchiveBlocked, RemoteMissing
from tests.raw_archive.test_scientific_v3 import manifest


class Ledger:
    read_only = False

    def __init__(self):
        self.db = sqlite3.connect(":memory:")


class Store:
    identity = "test-destination"

    def __init__(self, objects):
        self.objects = objects
        self.interrupt = False
        self.interrupt_read = False

    def download(self, key, path, size):
        if key not in self.objects:
            raise RemoteMissing("missing")
        Path(path).write_bytes(self.objects[key])
        if self.interrupt_read:
            self.interrupt_read = False
            raise RuntimeError("interrupted download")

    def publish(self, key, path):
        self.objects[key] = Path(path).read_bytes()
        if self.interrupt:
            self.interrupt = False
            raise RuntimeError("outcome unknown")


def setup_job(tmp_path):
    source, _, sha = manifest(tmp_path, {})
    index = tmp_path / "index.json"
    result = parts.prepare_index(source, sha, index, checkpoint=lambda: None)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    objects = {}
    publisher, verifier = Store(objects), Store(objects)
    kwargs = dict(
        source=source,
        capture_sha256=sha,
        index_file=index,
        index_sha256=result["index_sha256"],
        position=0,
        workdir=work,
        key=b"k" * 32,
        publisher=publisher,
        verifier=verifier,
        ledger=Ledger(),
        key_prefix="production/tiered/12345678-1234-1234-1234-123456789abc/science/2026-11-01/",
        checkpoint=lambda: None,
    )
    db = kwargs["ledger"].db
    db.execute("CREATE TABLE science_v3_scope (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
    db.execute("INSERT INTO science_v3_scope VALUES (1,'current')")
    db.execute("""CREATE TABLE science_v3_scope_epoch (id INTEGER PRIMARY KEY, epoch TEXT NOT NULL,
        source_session TEXT NOT NULL, capture_id TEXT NOT NULL, capture_sha256 TEXT NOT NULL,
        index_sha256 TEXT NOT NULL, frame_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)""")
    db.execute(
        "INSERT INTO science_v3_scope_epoch VALUES (1,?,?,?,?,?,?,?)",
        (
            "1" * 32,
            "2" * 32,
            "a" * 32,
            sha,
            result["index_sha256"],
            "3" * 64,
            "2026-11-01T00:00:00+00:00",
        ),
    )
    db.commit()
    return kwargs, objects


def run(kwargs):
    with patch.object(science.storage, "disk_reserve"), patch.object(science, "_stage_authority"):
        with patch.object(
            parts.shutil,
            "disk_usage",
            return_value=type("Disk", (), {"free": 20 * 1024**3, "total": 100 * 1024**3})(),
        ):
            return science.publish_part(**kwargs)


def test_part_readback_and_reverification_do_not_allow_writer(tmp_path):
    args, objects = setup_job(tmp_path)
    receipt = run(args)
    assert receipt["ciphertext_readback_verified"]
    assert not receipt["writer_allowed"] and not receipt["native_restore_verified"]
    assert len(objects) == 1
    assert run(args)["reference"] == receipt["reference"]
    with pytest.raises(ArchiveBlocked):
        science.writer_gate(receipt)


def test_unknown_upload_reconciles_same_ciphertext(tmp_path):
    args, objects = setup_job(tmp_path)
    args["publisher"].interrupt = True
    with pytest.raises(RuntimeError):
        run(args)
    original = next(iter(objects.values()))
    receipt = run(args)
    assert len(objects) == 1 and next(iter(objects.values())) == original
    assert receipt["ciphertext_readback_verified"]


def test_corrupt_remote_never_passes(tmp_path):
    args, objects = setup_job(tmp_path)
    receipt = run(args)
    objects[receipt["reference"]["key"]] = b"bad"
    with pytest.raises(ArchiveBlocked):
        run(args)


def test_wrong_key_never_passes(tmp_path):
    args, _ = setup_job(tmp_path)
    run(args)
    args["key"] = b"x" * 32
    with pytest.raises(ArchiveBlocked, match="authentication failed"):
        run(args)
    args["key"] = b"k" * 32
    assert run(args)["ciphertext_readback_verified"]


def test_interrupted_readback_can_resume(tmp_path):
    args, objects = setup_job(tmp_path)
    args["verifier"].interrupt_read = True
    with pytest.raises(RuntimeError, match="interrupted download"):
        run(args)
    frozen = dict(objects)
    assert run(args)["ciphertext_readback_verified"] and objects == frozen


def test_identical_verifier_is_rejected(tmp_path):
    args, _ = setup_job(tmp_path)
    args["verifier"] = args["publisher"]
    with pytest.raises(ArchiveBlocked):
        run(args)


def whole(args):
    params = {k: v for k, v in args.items() if k not in {"position", "received_part"}}
    with patch.object(science.storage, "disk_reserve"):
        return science.publish_whole(**params)


def test_whole_requires_all_ordered_parts_and_is_authenticated(tmp_path):
    args, objects = setup_job(tmp_path)
    run(args)
    with pytest.raises(ArchiveBlocked, match="Every ordered"):
        whole(args)
    args["position"] = 1
    run(args)
    result = whole(args)
    assert result["whole_inventory_authenticated"] and result["part_count"] == 2
    assert not result["native_restore_verified"] and not result["writer_allowed"]
    assert len(objects) == 3
    assert whole(args)["reference"] == result["reference"]


def test_whole_unknown_upload_reconciles_same_ciphertext(tmp_path):
    args, objects = setup_job(tmp_path)
    run(args)
    args["position"] = 1
    run(args)
    args["publisher"].interrupt = True
    with pytest.raises(RuntimeError):
        whole(args)
    original = dict(objects)
    result = whole(args)
    assert objects == original and result["whole_inventory_authenticated"]


def test_two_host_part_upload_needs_only_manifest_index_and_received_file(tmp_path):
    args, _ = setup_job(tmp_path)
    received = tmp_path / "received.pgcopy"
    with patch.object(
        parts.shutil,
        "disk_usage",
        return_value=type("Disk", (), {"free": 20 * 1024**3, "total": 100 * 1024**3})(),
    ):
        parts.materialize_part(
            args["source"],
            args["capture_sha256"],
            args["index_file"],
            args["index_sha256"],
            0,
            received,
            checkpoint=lambda: None,
        )
    metadata = tmp_path / "archive-metadata"
    metadata.mkdir(mode=0o700)
    (metadata / "capture.json").write_bytes((args["source"] / "capture.json").read_bytes())
    (metadata / "capture.json").chmod(0o400)
    args["source"] = metadata
    args["received_part"] = received
    assert run(args)["ciphertext_readback_verified"]
    assert not (metadata / "wav_feature.pgcopy").exists()


def test_two_host_part_rejects_wrong_received_content(tmp_path):
    args, _ = setup_job(tmp_path)
    received = tmp_path / "received.pgcopy"
    received.write_bytes(b"x" * args["source"].joinpath("wav_feature.pgcopy").stat().st_size)
    received.chmod(0o600)
    args["received_part"] = received
    with pytest.raises(ArchiveBlocked):
        run(args)


def test_direct_context_binds_absence_without_claiming_baseline(tmp_path):
    from app.raw_archive.scientific_scope_v3 import SOURCE_SCOPE

    identity = {
        "database": "greenmind_direct_production",
        "database_oid": 301614,
        "role": "greenmind_archive_reader_33fe395",
        "session_role": "greenmind_archive_reader_33fe395",
        "role_oid": 499352,
        "session_oid": 499352,
        "role_unprivileged": True,
        "server_version_num": 150010,
        "transaction_read_only": "on",
        "in_recovery": False,
        "server_address": "172.28.20.5/32",
        "server_port": 5432,
        "postmaster_start": SOURCE_SCOPE["postmaster_start"],
    }
    absence = {
        "format": "greenmind.science-direct-absence.v3",
        "capture_id": "a" * 32,
        "source": "direct",
        "identity": identity,
        "snapshot_sha256": "b" * 64,
        "tables": {"direct_feature_result": "absent", "direct_feature_job": "absent"},
        "complete": True,
        "cross_database_atomic": False,
        "prior_direct_data_captured": False,
    }
    import hashlib

    sha = hashlib.sha256((science._canonical(absence) + "\n").encode()).hexdigest()
    context = science.direct_baseline_context(absence, sha, "c" * 64, "d" * 64)
    assert not context["scientific_preservation_proven"] and not context["writer_allowed"]
    absence["tables"]["direct_feature_job"] = "present_empty"
    with pytest.raises(ArchiveBlocked):
        science.direct_baseline_context(absence, sha, "c" * 64, "d" * 64)


def test_linux_cache_release_only_targets_private_derivative(tmp_path):
    path = tmp_path / "owned-part"
    path.write_bytes(b"private")
    with (
        patch.object(science.sys, "platform", "linux"),
        patch.object(science.os, "posix_fadvise", create=True) as advise,
        patch.object(science.os, "POSIX_FADV_DONTNEED", 4, create=True),
    ):
        science._drop_cache(path)
        assert advise.call_count == 1
        advise.side_effect = OSError("unsupported")
        with pytest.raises(ArchiveBlocked, match="cache release failed"):
            science._drop_cache(path)
    assert path.read_bytes() == b"private"


def test_staged_publication_preserves_frozen_cipher_until_final_typed(tmp_path, monkeypatch):
    args, objects = setup_job(tmp_path)
    events = []
    original_typed = science._typed
    original_put, original_get = args["publisher"].publish, args["verifier"].download
    monkeypatch.setattr(science, "_typed", lambda *a: (events.append("typed"), original_typed(*a))[1])
    monkeypatch.setattr(args["publisher"], "publish", lambda *a: (events.append("put"), original_put(*a))[1])
    monkeypatch.setattr(args["verifier"], "download", lambda *a: (events.append("get"), original_get(*a))[1])
    args["stage"] = "prepare"
    prepared = run(args)
    assert events == ["typed"] and not objects
    assert prepared["status"] == "PREPARED" and prepared["archive_accepted"] is False
    db = args["ledger"].db
    row = db.execute("SELECT state,reference FROM science_v3_part").fetchone()
    sealed = list(args["workdir"].glob("*.sealed"))
    assert row[0] == "prepared" and len(sealed) == 1 and not list(args["workdir"].glob("*.native"))
    frozen = sealed[0].read_bytes()
    args["stage"] = "transfer"
    transferred = run(args)
    assert events == ["typed", "get", "put", "get"]
    assert transferred["status"] == "CIPHERTEXT_READBACK_VERIFIED" and not transferred["typed_final_verified"]
    assert not transferred["writer_allowed"] and not transferred["archive_accepted"]
    assert db.execute("SELECT state,reference FROM science_v3_part").fetchone() == row
    assert sealed[0].read_bytes() == frozen and list(objects.values()) == [frozen]
    assert not list(args["workdir"].glob("*.returned")) and not list(args["workdir"].glob("*.opened"))
    args["stage"] = "verify"
    verified = run(args)
    assert events == ["typed", "get", "put", "get", "get", "typed"]
    assert verified["status"] == "PART_VERIFIED" and verified["typed_final_verified"]
    assert not verified["archive_accepted"] and not verified["writer_allowed"]
    assert db.execute("SELECT state FROM science_v3_part").fetchone() == ("verified",)
    assert not list(args["workdir"].glob("*.sealed"))


def test_staged_unknown_transfer_reconciles_without_reencrypting(tmp_path):
    args, objects = setup_job(tmp_path)
    args["stage"] = "prepare"
    first = run(args)
    args["stage"] = "transfer"
    args["publisher"].interrupt = True
    with pytest.raises(RuntimeError, match="outcome unknown"):
        run(args)
    frozen = dict(objects)
    assert run(args)["reference"] == first["reference"] and objects == frozen
    assert args["ledger"].db.execute("SELECT state FROM science_v3_part").fetchone() == ("prepared",)
    args["stage"] = "verify"
    assert run(args)["reference"] == first["reference"]


def test_stages_never_publish_on_missing_or_tampered_cipher(tmp_path, monkeypatch):
    args, objects = setup_job(tmp_path)
    args["stage"] = "verify"
    with pytest.raises(ArchiveBlocked, match="Prepared V3 journal"):
        run(args)
    args["stage"] = "prepare"
    run(args)
    args["stage"] = "verify"
    monkeypatch.setattr(args["publisher"], "publish", lambda *a: pytest.fail("verify must not publish"))
    with pytest.raises(ArchiveBlocked, match="cannot publish"):
        run(args)
    assert not objects
    list(args["workdir"].glob("*.sealed"))[0].unlink()
    with pytest.raises(ArchiveBlocked, match="Frozen ciphertext"):
        run(args)
    assert args["ledger"].db.execute("SELECT state FROM science_v3_part").fetchone() == ("prepared",)


def test_staged_final_type_or_wrong_key_stays_prepared(tmp_path, monkeypatch):
    args, _ = setup_job(tmp_path)
    args["stage"] = "prepare"
    run(args)
    args["stage"] = "transfer"
    args["key"] = b"x" * 32
    with pytest.raises(ArchiveBlocked, match="authentication failed"):
        run(args)
    assert not args["publisher"].objects
    args["key"] = b"k" * 32
    run(args)
    args["stage"] = "verify"
    original = science._typed
    monkeypatch.setattr(science, "_typed", lambda *_: (_ for _ in ()).throw(ArchiveBlocked("typed failure")))
    with pytest.raises(ArchiveBlocked, match="typed failure"):
        run(args)
    assert args["ledger"].db.execute("SELECT state FROM science_v3_part").fetchone() == ("prepared",)
    monkeypatch.setattr(science, "_typed", original)
    assert run(args)["typed_final_verified"]


def test_staged_scope_change_before_commit_rolls_back(tmp_path):
    args, _ = setup_job(tmp_path)
    args["stage"] = "prepare"
    run(args)
    args["stage"] = "transfer"
    run(args)
    args["stage"] = "verify"
    db = args["ledger"].db
    checks = [0]
    def change_scope():
        checks[0] += 1
        if checks[0] == 4:
            db.execute("UPDATE science_v3_scope SET state='needs_reconcile'")
            db.commit()
    args["checkpoint"] = change_scope
    with pytest.raises(ArchiveBlocked):
        run(args)
    assert db.execute("SELECT state FROM science_v3_part").fetchone() == ("prepared",)


def test_staged_prepare_revalidates_key_and_never_reseals(tmp_path):
    args, objects = setup_job(tmp_path)
    args["stage"] = "prepare"
    first = run(args)
    frozen = list(args["workdir"].glob("*.sealed"))[0].read_bytes()
    assert run(args)["reference"] == first["reference"] and not objects
    args["key"] = b"x" * 32
    with pytest.raises(ArchiveBlocked, match="authentication failed"):
        run(args)
    assert list(args["workdir"].glob("*.sealed"))[0].read_bytes() == frozen


def test_staged_api_requires_real_bound_authority(tmp_path):
    args, _ = setup_job(tmp_path)
    args["stage"] = "prepare"
    with pytest.raises(ArchiveBlocked, match="Bound archive jobs lock required"):
        science.publish_part(**args)
    assert not args["ledger"].db.execute("SELECT name FROM sqlite_master WHERE name='science_v3_part'").fetchone()


def test_invalid_stage_never_touches_journal(tmp_path):
    args, _ = setup_job(tmp_path)
    args["stage"] = []
    with pytest.raises(ArchiveBlocked, match="Invalid V3 publication stage"):
        science.publish_part(**args)


def test_staged_workset_keeps_unknown_files_and_rejects_unknown_directory(tmp_path):
    args, objects = setup_job(tmp_path)
    args["stage"] = "prepare"
    unknown = args["workdir"] / "other-job"
    unknown.mkdir()
    with pytest.raises(ArchiveBlocked, match="Unknown V3 work directory entry"):
        run(args)
    assert unknown.is_dir() and not objects
    assert not args["ledger"].db.execute("SELECT 1 FROM science_v3_part").fetchone()


def test_staged_part_limit_checked_before_copy_or_network(tmp_path, monkeypatch):
    args, objects = setup_job(tmp_path)
    args["stage"] = "prepare"
    real = parts.load_index
    def too_large(*a, **kw):
        proof, index = real(*a, **kw)
        index["parts"][0]["bytes"] = 16 * 1024**2 + 1
        return proof, index
    monkeypatch.setattr(parts, "load_index", too_large)
    with pytest.raises(ArchiveBlocked, match="16 MiB"):
        run(args)
    assert not objects and not list(args["workdir"].glob("*.sealed"))
