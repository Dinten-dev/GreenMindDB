"""Local end-to-end V5 import tests; no native or provider calls."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import backup, registry_backup, registry_v5
from app.tiered_archive import scientific_registry_import as subject
from app.tiered_archive.ledger import Ledger, initialize_snapshot_pins
from app.tiered_archive.pipeline import publish
from tests.test_tiered_archive import AT, bundle  # noqa: F401

META = (
    Path(__file__).resolve().parents[4]
    / "outputs/greenmind-approved-science-native-capture-20261101/metadata"
)


@pytest.fixture
def import_case(bundle, tmp_path, monkeypatch):  # noqa: F811
    candidate, data, metadata, authority, box, _ = bundle
    candidate = replace(candidate, environment="production")
    box.identity = "sftp://u1@u1.your-storagebox.de:23/greenmind-raw"
    publish(
        candidate,
        [(data, metadata)],
        box,
        authority,
        checkpoint=lambda: None,
        now=AT + timedelta(days=2),
    )
    initialize_snapshot_pins(authority.db)
    with authority.db:
        authority.db.execute(
            "INSERT INTO managed_snapshot_scope VALUES(1,?,?,'current')", ("1" * 32, "2" * 64)
        )
        authority.db.execute(
            "CREATE TABLE sql_deletion_scope(id INTEGER PRIMARY KEY,state TEXT NOT NULL)"
        )
        authority.db.execute("INSERT INTO sql_deletion_scope VALUES(1,'needs_reconcile')")
    source = Ledger(tmp_path / "science")
    source.db.execute(
        "CREATE TABLE science_v3_scope(id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL)"
    )
    source.db.execute("INSERT INTO science_v3_scope VALUES(1,'current')")
    for table in subject.TABLES:
        source.db.execute(registry_v5.DDL[table])
    capture_raw, index_raw = (
        (META / "capture.json").read_bytes(),
        (META / "index.json").read_bytes(),
    )
    capture, index = json.loads(capture_raw), json.loads(index_raw)
    capture_sha, index_sha = (
        hashlib.sha256(capture_raw).hexdigest(),
        hashlib.sha256(index_raw).hexdigest(),
    )
    capture_root = tmp_path / "metadata"
    capture_root.mkdir(mode=0o700)
    (capture_root / "capture.json").write_bytes(capture_raw)
    (capture_root / "capture.json").chmod(0o400)
    index_path = capture_root / "index.json"
    index_path.write_bytes(index_raw)
    index_path.chmod(0o400)
    capture_id = capture["capture_id"]
    source_binding = {
        "machine_sha256": "8" * 64,
        "hostname": "source-test",
        "lock_device": 22,
        "lock_inode": 33,
        "lease_uid": 994,
        "lease_command_sha256": "a" * 64,
        "lease_executable_sha256": "b" * 64,
    }
    epoch_frame = {
        "format": "greenmind.science-source-handoff.v1",
        "source": "gateway",
        "capture_sha256": capture_sha,
        "index_sha256": index_sha,
        "capture_id": capture_id,
        "scope_sha256": capture["scope_sha256"],
        "native_files": [
            {k: item[k] for k in ("table", "rows", "bytes", "sha256")} for item in capture["files"]
        ],
        "part_count": 46,
        "hostname": source_binding["hostname"],
        "machine_sha256": source_binding["machine_sha256"],
        "boot_id": "1" * 36,
        "uid": 0,
        "lease_session": "4" * 32,
        "source_lock": {
            "device": 22,
            "inode": 33,
            "pid": 12,
            "uid": 994,
            "start_ticks": 123,
            "command_sha256": "a" * 64,
            "executable_sha256": "b" * 64,
        },
        "producer_sha256": "c" * 64,
        "parts_code_sha256": "d" * 64,
        "capture_code_sha256": "e" * 64,
        "observed_at": capture["finished_at"],
    }
    epoch_path = tmp_path / "source-epoch-frame.json"
    epoch_path.write_text(json.dumps(epoch_frame))
    epoch_path.chmod(0o400)
    epoch_sha = hashlib.sha256(epoch_path.read_bytes()).hexdigest()
    bootstrap = {
        "action": "bootstrap",
        "owned_worker_wait_exit": 0,
        "worker_generation_gone": True,
        "worker_cgroup_gone": True,
        "archive_accepted": False,
        "wip_not_release": True,
        "frame_proof": {"source_owned_wait_exit": 0, "frame_sha256": epoch_sha},
        "result": {
            "writer_allowed": False,
            "archive_accepted": False,
            "sql_allowed": False,
            "result": {
                "status": "SCIENCE_SCOPE_CURRENT",
                "scope_epoch": "3" * 32,
                "capture_id": capture_id,
                "sql_scope": "HOLD",
            },
        },
    }
    bootstrap_path = tmp_path / "bootstrap-receipt.json"
    bootstrap_path.write_text(json.dumps(bootstrap))
    bootstrap_path.chmod(0o400)
    bootstrap_sha = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
    source.db.execute(
        "INSERT INTO science_v3_scope_epoch VALUES(1,?,?,?,?,?,?,?)",
        ("3" * 32, "4" * 32, capture_id, capture_sha, index_sha, epoch_sha, AT.isoformat()),
    )
    prefix = "production/tiered/11111111-1111-4111-8111-111111111111/science/2026-10-01/"
    refs = []
    for pos, part in enumerate(index["parts"]):
        cipher = hashlib.sha256(str(pos).encode()).hexdigest()
        binding = {
            "capture_sha256": capture_sha,
            "index_sha256": index_sha,
            "capture_id": capture_id,
            "position": pos,
            "part": part,
            "destination": box.identity,
            "key_prefix": prefix,
        }
        raw = registry_v5.canonical(binding)
        ref = {
            "format": "bin",
            "sha256": cipher,
            "bytes": part["bytes"] + 32,
            "plaintext_sha256": part["sha256"],
            "key": prefix + cipher + ".bin",
        }
        source.db.execute(
            "INSERT INTO science_v3_part VALUES(?,?,'verified',?)",
            (hashlib.sha256(raw.encode()).hexdigest(), raw, registry_v5.canonical(ref)),
        )
        refs.append(ref)
    whole = {
        "format": "greenmind.science-whole.v3",
        "capture": capture,
        "capture_sha256": capture_sha,
        "index": index,
        "index_sha256": index_sha,
        "destination": box.identity,
        "references": refs,
        "native_restore_verified": False,
        "parent_fk_closure_proven": False,
        "cross_database_atomic": False,
    }
    raw = (registry_v5.canonical(whole) + "\n").encode()
    whole_sha = hashlib.sha256(raw).hexdigest()
    plaintext, ciphertext = tmp_path / "whole", tmp_path / "whole.enc"
    plaintext.write_bytes(raw)
    key = b"k" * 32
    meta = backup.encrypt(plaintext, ciphertext, key, checkpoint=lambda: None)
    whole_ref = {**meta, "key": prefix + meta["sha256"] + ".bin"}
    box.publish(whole_ref["key"], ciphertext)
    source.db.execute(
        "INSERT INTO science_v3_whole VALUES(?,?,'verified',?)",
        (
            hashlib.sha256(("whole:" + whole_sha).encode()).hexdigest(),
            whole_sha,
            registry_v5.canonical(whole_ref),
        ),
    )
    for pos, part in enumerate(index["parts"]):
        role = "science_v3_" + "6" * 32
        audit = {
            "format": "greenmind.science-part-restore.v3",
            "whole_sha256": whole_sha,
            "capture_id": capture_id,
            "index_sha256": index_sha,
            "position": pos,
            "part_sha256": part["sha256"],
            "rows": part["rows"],
            "target": {
                "database": role,
                "database_oid": 10,
                "role": role,
                "role_oid": 11,
                "port": 19442,
                "server_address": "127.0.0.1/32",
                "postmaster_start": "actual-test",
            },
            "native_row_multiset_equal": True,
            "parent_fk_closure_proven": False,
            "writer_allowed": False,
        }
        source.db.execute(
            "INSERT INTO science_v3_restore VALUES(?,?,?)",
            (whole_sha, pos, registry_v5.canonical(audit)),
        )
    source.db.commit()
    proof = {
        "provider_readonly_verified": True,
        "matched_configured_subaccount": True,
        "request_method": "GET",
        "mutations": 0,
        "credential_values_exported": False,
        "provider_selected_fields": {"access_settings_booleans": {"readonly": True}},
    }
    proof_path = tmp_path / "provider.json"
    proof_path.write_text(json.dumps(proof))
    proof_path.chmod(0o400)
    proof_sha = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    terminal_path = tmp_path / "provider-terminal.json"
    terminal_path.write_text(
        json.dumps(
            {
                "exit": 0,
                "owned_pid": 123,
                "stdout_sha256": proof_sha,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            }
        )
    )
    terminal_path.chmod(0o400)
    terminal_sha = hashlib.sha256(terminal_path.read_bytes()).hexdigest()
    monkeypatch.setattr(subject, "_live_authority", lambda *_: ("7" * 32, source_binding))
    monkeypatch.setattr(subject, "_transport", lambda publisher, verifier: publisher.identity)
    binding_path = tmp_path / "source-binding.json"
    binding_path.write_text(json.dumps(source_binding))
    binding_path.chmod(0o400)
    binding_sha = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    kwargs = dict(
        source_binding_path=binding_path,
        source_binding_sha256=binding_sha,
        provider_evidence_path=proof_path,
        provider_evidence_sha256=proof_sha,
        provider_terminal_path=terminal_path,
        provider_terminal_sha256=terminal_sha,
        source_epoch_frame_path=epoch_path,
        bootstrap_receipt_path=bootstrap_path,
        bootstrap_receipt_sha256=bootstrap_sha,
        capture_root=capture_root,
        capture_sha256=capture_sha,
        index_file=index_path,
        index_sha256=index_sha,
        whole_sha256=whole_sha,
        publisher=box,
        verifier=box,
        key=key,
        checkpoint=lambda: None,
    )
    yield authority, source, box, kwargs
    source.close()


def manifest(case, tmp_path):
    authority, source, _, kwargs = case
    prepared = subject.prepare_manifest(authority, source.root, **kwargs)
    path = tmp_path / "import-manifest.json"
    path.write_bytes(prepared["manifest"])
    path.chmod(0o400)
    return path, prepared["sha256"]


def apply(case, path, sha):
    authority, source, _, kwargs = case
    return subject.import_manifest(
        authority, source.root, manifest_path=path, manifest_sha256=sha, **kwargs
    )


def test_full_import_backups_independently_and_keeps_scopes_closed(import_case, tmp_path):
    authority, source, box, kwargs = import_case
    database = source.root / "tiered.sqlite3"
    wal = source.root / "tiered.sqlite3-wal"
    before_images = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (database, wal)]
    path, sha = manifest(import_case, tmp_path)
    before = source.db.execute("SELECT count(*) FROM science_v3_part").fetchone()
    result = apply(import_case, path, sha)
    assert (
        result["status"] == "IMPORTED_AND_READBACK_VERIFIED" and result["writer_allowed"] is False
    )
    assert source.db.execute("SELECT count(*) FROM science_v3_part").fetchone() == before == (46,)
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (database, wal)
    ] == before_images
    assert source.db.execute("SELECT state FROM science_v3_scope").fetchone() == ("current",)
    assert authority.db.execute("SELECT state FROM science_v3_scope").fetchone() == (
        "needs_reconcile",
    )
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "needs_reconcile",
    )
    assert authority.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    assert authority.db.execute("SELECT count(*) FROM science_v3_import_provenance").fetchone() == (
        1,
    )
    index = json.loads((box.root / result["registry_reference"]["key"]).read_bytes())
    assert (
        index["schema"] == 5
        and index["journal_v5"]["tables"]["science_v3_import_provenance"]["rows"] == 1
    )
    assert apply(import_case, path, sha)["status"] == "ALREADY_IMPORTED"


def test_import_crash_after_atomic_commit_resumes_without_duplicate(
    import_case, tmp_path, monkeypatch
):
    authority, source, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    original = registry_backup.backup
    monkeypatch.setattr(
        registry_backup, "backup", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError, match="interrupted"):
        apply(import_case, path, sha)
    assert authority.db.execute("SELECT state FROM science_registry_import_intent").fetchone() == (
        "applied",
    )
    assert authority.db.execute("SELECT count(*) FROM science_v3_part").fetchone() == (46,)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "needs_reconcile",
    )
    monkeypatch.setattr(registry_backup, "backup", original)
    assert apply(import_case, path, sha)["status"] == "IMPORTED_AND_READBACK_VERIFIED"
    assert authority.db.execute("SELECT count(*) FROM science_v3_part").fetchone() == (46,)


def test_changed_source_after_manifest_blocks_without_promotion(import_case, tmp_path):
    authority, source, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    with source.db:
        source.db.execute("UPDATE science_v3_part SET state='prepared' WHERE rowid=1")
    with pytest.raises(ArchiveBlocked):
        apply(import_case, path, sha)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )
    assert not authority.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='science_registry_import_intent'"
    ).fetchone()


def test_existing_owned_snapshot_blocks_import_without_changing_it(import_case, tmp_path):
    authority, _, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    registry_backup.initialize_managed(authority.db)
    with authority.db:
        authority.db.execute(
            "INSERT INTO managed_snapshot VALUES(?, 'verified', '{}', '{}', '{}', ?)",
            ("a" * 32, AT.isoformat()),
        )
    with pytest.raises(ArchiveBlocked, match="Existing owned snapshots"):
        apply(import_case, path, sha)
    assert authority.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("verified",)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_interrupted_atomic_row_copy_rolls_back_and_resumes(import_case, tmp_path):
    authority, source, _, kwargs = import_case
    path, sha = manifest(import_case, tmp_path)
    frozen = json.loads(path.read_bytes())
    _, rows = subject._collect(authority, source.root, **kwargs)
    assert subject._prepare_intent(authority.db, frozen, sha, lambda: None)[0] == "prepared"
    count = [0]

    def interrupted():
        count[0] += 1
        if count[0] == 10:
            raise RuntimeError("interrupted atomic copy")

    with pytest.raises(RuntimeError, match="interrupted atomic copy"):
        evidence = subject._seal_evidence(
            subject._evidence_plain(path.read_bytes(), frozen, kwargs),
            kwargs["key"],
            subject._import_id(frozen),
        )
        subject._apply_rows(authority, frozen, sha, rows, evidence, interrupted, lambda: None)
    assert authority.db.execute("SELECT state FROM science_registry_import_intent").fetchone() == (
        "prepared",
    )
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "needs_reconcile",
    )
    assert not authority.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='science_v3_part'"
    ).fetchone()
    with pytest.raises(ArchiveBlocked, match="Incomplete science registry import"):
        registry_backup.backup(authority, import_case[2], checkpoint=lambda: None)
    assert apply(import_case, path, sha)["status"] == "IMPORTED_AND_READBACK_VERIFIED"


def test_science_source_unknown_tables_and_wrong_whole_hold(import_case, tmp_path):
    authority, source, _, kwargs = import_case
    with source.db:
        source.db.execute("CREATE TABLE science_v3_unreviewed(value TEXT)")
    with pytest.raises(ArchiveBlocked, match="Unknown source Science journal table"):
        subject.prepare_manifest(authority, source.root, **kwargs)
    with source.db:
        source.db.execute("DROP TABLE science_v3_unreviewed")
    wrong = dict(kwargs, whole_sha256="0" * 64)
    with pytest.raises(ArchiveBlocked, match="Verified Science whole"):
        subject.prepare_manifest(authority, source.root, **wrong)


def test_provider_terminal_must_bind_exact_owned_wait(import_case, tmp_path):
    authority, source, _, kwargs = import_case
    terminal = tmp_path / "wrong-terminal.json"
    terminal.write_text(
        json.dumps(
            {"exit": 0, "owned_pid": 123, "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64}
        )
    )
    terminal.chmod(0o400)
    modified = dict(
        kwargs,
        provider_terminal_path=terminal,
        provider_terminal_sha256=hashlib.sha256(terminal.read_bytes()).hexdigest(),
    )
    with pytest.raises(ArchiveBlocked, match="exact successful owned wait"):
        subject.prepare_manifest(authority, source.root, **modified)


def test_changed_source_observation_session_requires_durable_intent(
    import_case, tmp_path, monkeypatch
):
    authority, source, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    binding = subject._live_authority()[1]
    monkeypatch.setattr(subject, "_live_authority", lambda *_: ("a" * 32, binding))
    with pytest.raises(ArchiveBlocked, match="lacks durable import intent"):
        apply(import_case, path, sha)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_resume_under_new_real_lease_keeps_original_provenance(import_case, tmp_path, monkeypatch):
    authority, _, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    frozen = json.loads(path.read_bytes())
    assert subject._prepare_intent(authority.db, frozen, sha, lambda: None)[0] == "prepared"
    binding = subject._live_authority()[1]
    monkeypatch.setattr(subject, "_live_authority", lambda *_: ("a" * 32, binding))
    assert apply(import_case, path, sha)["status"] == "IMPORTED_AND_READBACK_VERIFIED"
    raw = authority.db.execute("SELECT record FROM science_v3_import_provenance").fetchone()[0]
    assert json.loads(raw)["observation_session"] == "7" * 32


def test_production_import_requires_real_job_lock_and_separate_readonly_transport(
    import_case, tmp_path, monkeypatch
):
    from app.tiered_archive.storage import Store

    authority, _, box, _ = import_case
    monkeypatch.undo()
    monkeypatch.setattr(subject.os, "geteuid", lambda: 0)
    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
        subject._live_authority(authority, tmp_path / "binding", "a" * 64, lambda: None)
    first, second = Store(box), Store(box)
    with pytest.raises(ArchiveBlocked, match="Independent matching read-only"):
        subject._transport(first, second)


def test_lost_bound_authority_rolls_back_import_intent(import_case, tmp_path):
    authority, _, _, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    frozen = json.loads(path.read_bytes())

    def lost():
        raise ArchiveBlocked("source lease lost")

    with pytest.raises(ArchiveBlocked, match="source lease lost"):
        subject._prepare_intent(authority.db, frozen, sha, lost)
    assert authority.db.execute(
        "SELECT count(*) FROM science_registry_import_intent"
    ).fetchone() == (0,)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_unexpected_source_rollback_journal_holds_without_mutation(import_case, tmp_path):
    authority, source, _, kwargs = import_case
    journal = source.root / "tiered.sqlite3-journal"
    journal.write_text("unknown")
    journal.chmod(0o600)
    with pytest.raises(ArchiveBlocked, match="rollback journal"):
        subject.prepare_manifest(authority, source.root, **kwargs)
    assert journal.read_text() == "unknown"
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_corrupt_independent_registry_readback_never_completes(import_case, tmp_path, monkeypatch):
    authority, _, box, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    original = box.download

    def corrupt(key, output, size, *, snapshot=None):
        original(key, output, size, snapshot=snapshot)
        if key.endswith(".json"):
            output.write_bytes(b"corrupt")

    monkeypatch.setattr(box, "download", corrupt)
    with pytest.raises(ArchiveBlocked, match="checksum"):
        apply(import_case, path, sha)
    assert authority.db.execute("SELECT state FROM science_registry_import_intent").fetchone() == (
        "applied",
    )
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "needs_reconcile",
    )
    monkeypatch.setattr(box, "download", original)
    assert apply(import_case, path, sha)["status"] == "IMPORTED_AND_READBACK_VERIFIED"


def test_wrong_encryption_key_never_creates_import_intent(import_case):
    authority, source, _, kwargs = import_case
    with pytest.raises(ArchiveBlocked):
        subject.prepare_manifest(authority, source.root, **dict(kwargs, key=b"x" * 32))
    assert not authority.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='science_registry_import_intent'"
    ).fetchone()
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_original_bootstrap_requires_real_source_owned_wait(import_case, tmp_path):
    authority, source, _, kwargs = import_case
    receipt = json.loads(kwargs["bootstrap_receipt_path"].read_bytes())
    receipt["frame_proof"]["source_owned_wait_exit"] = False
    bad = tmp_path / "false-owned-wait.json"
    bad.write_text(json.dumps(receipt))
    bad.chmod(0o400)
    changed = dict(
        kwargs,
        bootstrap_receipt_path=bad,
        bootstrap_receipt_sha256=hashlib.sha256(bad.read_bytes()).hexdigest(),
    )
    with pytest.raises(ArchiveBlocked, match="bootstrap owned-wait"):
        subject.prepare_manifest(authority, source.root, **changed)
    assert authority.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "current",
    )


def test_source_scope_history_is_preserved_without_writer_promotion(import_case, tmp_path):
    authority, source, _, _ = import_case
    with source.db:
        source.db.execute(
            "INSERT INTO science_v3_scope_history VALUES(?,?,?,?,?)",
            ("a" * 32, "3" * 32, "b" * 64, "c" * 32, AT.isoformat()),
        )
        source.db.execute("UPDATE science_v3_scope_epoch SET epoch=? WHERE id=1", ("a" * 32,))
    path, sha = manifest(import_case, tmp_path)
    assert apply(import_case, path, sha)["status"] == "IMPORTED_AND_READBACK_VERIFIED"
    assert authority.db.execute("SELECT epoch FROM science_v3_scope_epoch").fetchone() == (
        "a" * 32,
    )
    assert authority.db.execute(
        "SELECT previous_epoch FROM science_v3_scope_history"
    ).fetchone() == ("3" * 32,)
    assert authority.db.execute("SELECT state FROM science_v3_scope").fetchone() == (
        "needs_reconcile",
    )


def test_original_epoch_frame_change_blocks_before_intent(import_case):
    authority, source, _, kwargs = import_case
    path = kwargs["source_epoch_frame_path"]
    path.chmod(0o600)
    path.write_text("{}")
    path.chmod(0o400)
    with pytest.raises(ArchiveBlocked, match="Pinned immutable root evidence"):
        subject.prepare_manifest(authority, source.root, **kwargs)
    assert not authority.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='science_registry_import_intent'"
    ).fetchone()


def test_imported_v5_provenance_rejects_later_inventory_tampering(import_case, tmp_path):
    authority, _, box, _ = import_case
    path, sha = manifest(import_case, tmp_path)
    apply(import_case, path, sha)
    with authority.db:
        authority.db.execute(
            "INSERT INTO science_v3_scope_history VALUES(?,?,?,?,?)",
            ("a" * 32, "3" * 32, "b" * 64, "c" * 32, AT.isoformat()),
        )
    with pytest.raises(ArchiveBlocked, match="frozen inventory"):
        registry_backup.backup(authority, box, checkpoint=lambda: None)


def test_evidence_is_encrypted_and_authentication_rejects_tamper(import_case, tmp_path):
    import base64

    authority, _, _, kwargs = import_case
    path, sha = manifest(import_case, tmp_path)
    apply(import_case, path, sha)
    record = json.loads(
        authority.db.execute("SELECT record FROM science_v3_import_provenance").fetchone()[0]
    )
    assert subject.verify_provenance_evidence(record, kwargs["key"])
    assert b"source-test" not in base64.b64decode(record["evidence"]["ciphertext_base64"])
    with pytest.raises(ArchiveBlocked):
        subject.verify_provenance_evidence(record, b"x" * 32)
    sealed = bytearray(base64.b64decode(record["evidence"]["ciphertext_base64"]))
    sealed[-1] ^= 1
    record["evidence"]["ciphertext_base64"] = base64.b64encode(sealed).decode()
    record["evidence"]["sha256"] = hashlib.sha256(sealed).hexdigest()
    with pytest.raises(ArchiveBlocked):
        subject.verify_provenance_evidence(record, kwargs["key"])
