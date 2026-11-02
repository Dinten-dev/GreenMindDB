"""Archive-only V5 journal and budget recovery tests; no server calls."""

import hashlib
import json
from datetime import timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.scientific_v3 import _archive_scope
from app.tiered_archive import codec, registry_backup, registry_v5, sql_deletion
from app.tiered_archive.ledger import Ledger
from tests.test_tiered_archive import AT, bundle, verified  # noqa: F401


def add_journals(ledger, candidate):
    for ddl in registry_v5.DDL.values():
        ledger.db.execute(ddl)
    part = {
        "table": "wav_feature",
        "sequence": 0,
        "first_row": 0,
        "rows": 1,
        "start": 19,
        "end": 20,
        "bytes": 22,
        "sha256": "a" * 64,
    }
    prefix = "staging/tiered/11111111-1111-4111-8111-111111111111/science/2026-10-01/"
    binding = {
        "capture_sha256": "b" * 64,
        "index_sha256": "c" * 64,
        "capture_id": "d" * 32,
        "position": 0,
        "part": part,
        "destination": "sftp://u123@u123.your-storagebox.de:23/archive",
        "key_prefix": prefix,
    }
    b = registry_v5.canonical(binding)
    ref = {
        "format": "bin",
        "sha256": "e" * 64,
        "bytes": 100,
        "plaintext_sha256": part["sha256"],
        "key": prefix + "e" * 64 + ".bin",
    }
    ledger.db.execute(
        "INSERT INTO science_v3_part VALUES (?,?,?,?)",
        (hashlib.sha256(b.encode()).hexdigest(), b, "verified", registry_v5.canonical(ref)),
    )
    whole_sha = "f" * 64
    whole_ref = {
        **ref,
        "plaintext_sha256": whole_sha,
        "sha256": "1" * 64,
        "key": prefix + "1" * 64 + ".bin",
    }
    ledger.db.execute(
        "INSERT INTO science_v3_whole VALUES (?,?,?,?)",
        (
            hashlib.sha256(("whole:" + whole_sha).encode()).hexdigest(),
            whole_sha,
            "verified",
            registry_v5.canonical(whole_ref),
        ),
    )
    audit = {
        "format": "greenmind.science-part-restore.v3",
        "whole_sha256": whole_sha,
        "capture_id": "d" * 32,
        "index_sha256": "c" * 64,
        "position": 0,
        "part_sha256": "a" * 64,
        "rows": 1,
        "target": {
            "database": "science_v3_" + "4" * 32,
            "database_oid": 42,
            "role": "science_v3_" + "4" * 32,
            "role_oid": 43,
            "port": 15432,
            "server_address": "127.0.0.1/32",
            "postmaster_start": "pinned",
        },
        "native_row_multiset_equal": True,
        "parent_fk_closure_proven": False,
        "writer_allowed": False,
    }
    ledger.db.execute(
        "INSERT INTO science_v3_restore VALUES (?,?,?)",
        (whole_sha, 0, registry_v5.canonical(audit)),
    )
    ledger.db.execute(
        "INSERT INTO sql_deletion_pilot VALUES (?,?,?)", ("2" * 32, candidate.identity, "3" * 64)
    )
    genesis = {
        "format": "greenmind.sql-scope-genesis.v1",
        "epoch": "4" * 32,
        "source_identity_sha256": "5" * 64,
        "audit_schema_sha256": "6" * 64,
        "origin_inventory_sha256": "7" * 64,
        "registry_reference_sha256": "b" * 64,
        "runtime_sha256": "8" * 64,
        "bindings_sha256": "9" * 64,
        "proof_sha256": "a" * 64,
    }
    ledger.db.execute(
        "INSERT INTO sql_deletion_epoch VALUES(1,?,?)",
        (genesis["epoch"], registry_v5.canonical(genesis)),
    )
    ledger.db.execute(
        "INSERT INTO science_v3_scope_epoch VALUES (1,?,?,?,?,?,?,?)",
        ("1" * 32, "2" * 32, "d" * 32, "b" * 64, "c" * 64, "3" * 64, AT.isoformat()),
    )
    ledger.db.commit()


def test_v5_roundtrip_keeps_all_journals_and_requires_reconciliation(bundle, tmp_path):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    index = json.loads((box.root / reference["key"]).read_bytes())
    assert index["schema"] == 5
    assert all(
        value["rows"] == (0 if name in {"science_v3_scope_history", "science_v3_import_provenance", "sql_audit_v3_backup", "sql_audit_v3_backup_history", "sql_audit_v3_scope", "sql_audit_v3_scope_proof"} else 1)
        for name, value in index["journal_v5"]["tables"].items()
    )
    recovered = Ledger(tmp_path / "new")
    registry_backup.restore(
        reference, recovered, box, checkpoint=lambda: None, expected_environment="staging"
    )
    for table in registry_v5.TABLES:
        assert (
            recovered.db.execute("SELECT * FROM " + table).fetchall()
            == ledger.db.execute("SELECT * FROM " + table).fetchall()
        )
    assert recovered.db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone() == (
        "needs_reconcile",
    )
    assert recovered.db.execute("SELECT state FROM science_v3_scope WHERE id=1").fetchone() == (
        "needs_reconcile",
    )
    with pytest.raises(ArchiveBlocked, match="reconciliation"):
        _archive_scope(recovered)
    with pytest.raises(ArchiveBlocked, match="reconciliation"):
        sql_deletion._budget(recovered, {"pilot_id": "2" * 32}, candidate, "3" * 64)
    recovered.close()


def test_v5_missing_budget_coverage_rejected_atomically(bundle, tmp_path):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    path = box.root / reference["key"]
    index = json.loads(path.read_bytes())
    index["journal_v5"]["tables"]["sql_deletion_pilot"]["rows"] += 1
    path.write_text(json.dumps(index, sort_keys=True))
    new_sha = checksum(path)
    new_key = reference["key"].rsplit("/", 1)[0] + "/" + new_sha + ".json"
    path.replace(box.root / new_key)
    reference = {"key": new_key, "sha256": new_sha, "bytes": (box.root / new_key).stat().st_size}
    recovered = Ledger(tmp_path / "new")
    with pytest.raises(ArchiveBlocked, match="coverage"):
        registry_backup.restore(
            reference, recovered, box, checkpoint=lambda: None, expected_environment="staging"
        )
    assert recovered.status() == {}
    assert recovered.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    recovered.close()


def test_v5_tampered_sql_budget_rejected_before_backup(bundle):  # noqa: F811
    candidate, _, _, ledger, _, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    ledger.db.execute("UPDATE sql_deletion_pilot SET candidate=?", ("4" * 64,))
    ledger.db.commit()
    with pytest.raises(ArchiveBlocked, match="pilot budget"):
        registry_backup.backup(ledger, bundle[4], checkpoint=lambda: None)


def test_legacy_restore_never_treats_unknown_budget_as_free(bundle, tmp_path):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    recovered = Ledger(tmp_path / "new")
    registry_backup.restore(
        reference, recovered, box, checkpoint=lambda: None, expected_environment="staging"
    )
    with pytest.raises(ArchiveBlocked, match="reconciliation"):
        sql_deletion._budget(recovered, {"pilot_id": "2" * 32}, candidate, "3" * 64)
    recovered.close()


def test_v5_roundtrip_with_managed_snapshot_pin(subject, bundle, tmp_path):  # noqa: F811
    from app.raw_archive.coordination import lease
    from app.tiered_archive.pipeline import publish
    from tests.test_managed_snapshot_pins import PIN, acquire

    ledger, _, _, _ = subject
    candidate, path, metadata, _, box, _ = bundle
    publish(candidate, [(path, metadata)], box, ledger, checkpoint=lambda: None, now=AT)
    with lease("copy"):
        acquire(subject)
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    index = json.loads((box.root / reference["key"]).read_bytes())
    assert index["schema"] == 5 and index["journal_v5"]["managed_pin_coverage"] is True
    recovered = Ledger(tmp_path / "new-v5-pin")
    registry_backup.restore(
        reference, recovered, box, checkpoint=lambda: None, expected_environment="staging"
    )
    assert recovered.db.execute(
        "SELECT state FROM managed_snapshot_pin WHERE id=?", (PIN,)
    ).fetchone() == ("active",)
    assert recovered.db.execute("SELECT recovery_state FROM managed_snapshot_scope").fetchone() == (
        "needs_reconcile",
    )
    assert recovered.db.execute("SELECT count(*) FROM sql_deletion_pilot").fetchone() == (1,)
    recovered.close()


from tests.test_managed_snapshot_pins import subject  # noqa: E402, F401


def test_v5_sent_journal_never_hides_missing_remote_part(bundle):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    index = json.loads((box.root / reference["key"]).read_bytes())
    (box.root / index["parts"][0]["key"]).unlink()
    with pytest.raises(ArchiveBlocked):
        registry_backup.backup(ledger, box, checkpoint=lambda: None)


def test_v5_hash_valid_but_tampered_budget_record_is_rejected_on_restore(bundle, tmp_path):  # noqa: F811
    import gzip

    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    root_path = box.root / reference["key"]
    index = json.loads(root_path.read_bytes())
    found = False
    for part in index["parts"]:
        old_path = box.root / part["key"]
        rows = [codec.decode(line) for line in gzip.decompress(old_path.read_bytes()).splitlines()]
        for row in rows:
            if row["type"] == "sql_deletion_pilot":
                row["values"][1] = "9" * 64
                found = True
        if found:
            new_body = gzip.compress(b"\n".join(codec.encode(row) for row in rows) + b"\n", mtime=0)
            new_sha = hashlib.sha256(new_body).hexdigest()
            new_key = part["key"].rsplit("/", 1)[0] + "/" + new_sha + ".gz"
            (box.root / new_key).write_bytes(new_body)
            part.update(key=new_key, sha256=new_sha, bytes=len(new_body))
            break
    assert found
    root_body = json.dumps(index, sort_keys=True).encode()
    root_sha = hashlib.sha256(root_body).hexdigest()
    root_key = reference["key"].rsplit("/", 1)[0] + "/" + root_sha + ".json"
    (box.root / root_key).write_bytes(root_body)
    recovered = Ledger(tmp_path / "tamper-new")
    with pytest.raises(ArchiveBlocked, match="pilot budget"):
        registry_backup.restore(
            {"key": root_key, "sha256": root_sha, "bytes": len(root_body)},
            recovered,
            box,
            checkpoint=lambda: None,
            expected_environment="staging",
        )
    assert recovered.status() == {}
    recovered.close()


def test_previous_v5_without_sql_epoch_stays_in_reconciliation(bundle, tmp_path):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    ledger.db.execute("DROP TABLE sql_deletion_epoch")
    ledger.db.commit()
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    path = box.root / reference["key"]
    index = json.loads(path.read_bytes())
    del index["journal_v5"]["tables"]["sql_deletion_epoch"]
    path.write_text(json.dumps(index, sort_keys=True))
    sha = checksum(path)
    key = reference["key"].rsplit("/", 1)[0] + "/" + sha + ".json"
    path.replace(box.root / key)
    recovered = Ledger(tmp_path / "legacy-v5")
    registry_backup.restore(
        {"key": key, "sha256": sha, "bytes": (box.root / key).stat().st_size},
        recovered,
        box,
        checkpoint=lambda: None,
        expected_environment="staging",
    )
    assert recovered.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    assert not recovered.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_deletion_epoch'"
    ).fetchone()
    recovered.close()


def test_v5_rejects_tampered_sql_genesis(bundle):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    row = ledger.db.execute("SELECT genesis FROM sql_deletion_epoch").fetchone()
    altered = json.loads(row[0])
    altered["epoch"] = "f" * 32
    ledger.db.execute("UPDATE sql_deletion_epoch SET genesis=?", (registry_v5.canonical(altered),))
    ledger.db.commit()
    with pytest.raises(ArchiveBlocked, match="genesis"):
        registry_backup.backup(ledger, box, checkpoint=lambda: None)


def test_old_v5_without_new_provenance_remains_readable(bundle, tmp_path):  # noqa: F811
    candidate, _, _, ledger, box, _ = bundle
    verified(bundle, AT + timedelta(days=2))
    add_journals(ledger, candidate)
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    index = json.loads((box.root / reference["key"]).read_bytes())
    index["journal_v5"]["tables"].pop("science_v3_import_provenance")
    raw = json.dumps(index, sort_keys=True).encode()
    path = tmp_path / "old-index.json"
    path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    key = reference["key"].rsplit("/", 1)[0] + "/" + sha + ".json"
    box.publish(key, path)
    recovered = Ledger(tmp_path / "old-v5-restored")
    try:
        registry_backup.restore({"key": key, "sha256": sha, "bytes": len(raw)}, recovered, box,
                                checkpoint=lambda: None, expected_environment="staging")
        assert recovered.db.execute("SELECT state FROM science_v3_scope").fetchone() == ("needs_reconcile",)
        assert not recovered.db.execute("SELECT 1 FROM sqlite_master WHERE name='science_v3_import_provenance'").fetchone()
    finally:
        recovered.close()


def test_v3_audit_scope_roundtrip_forces_reconciliation(bundle, tmp_path):  # noqa: F811
    from dataclasses import replace

    from app.tiered_archive.pipeline import candidate_json

    old, _, _, ledger, box, _ = bundle
    candidate = replace(old, dataset="database_backup", organization="00000000-0000-0000-0000-000000000000", kind="features")
    epoch, genesis, manifest, cipher, plain = "1" * 32, "2" * 64, "3" * 64, "4" * 64, "5" * 64
    files = [{"backup_format": "schema-master-provider-sql-audit-v3", "audit_v3_epoch": epoch, "audit_v3_genesis_sha256": genesis, "audit_v3_plaintext_sha256": plain}, {}, {}, {}, {}, {"sha256": cipher, "plaintext_sha256": plain}]
    ledger.save(candidate, "published", {"source": candidate_json(candidate), "manifest": {"sha256": manifest}, "files": files, "destination": box.identity}, AT)
    ledger.db.execute(registry_v5.DDL["sql_audit_v3_backup"])
    ledger.db.execute("INSERT INTO sql_audit_v3_backup VALUES(?,?,?,?,?)", (epoch, candidate.identity, manifest, cipher, plain))
    ledger.db.execute(registry_v5.DDL["sql_audit_v3_scope"])
    ledger.db.execute("INSERT INTO sql_audit_v3_scope VALUES(1,?,?,?)", (epoch, genesis, "current"))
    ledger.db.commit()
    reference = registry_backup.backup(ledger, box, checkpoint=lambda: None)
    recovered = Ledger(tmp_path / "v3-recovered")
    try:
        registry_backup.restore(reference, recovered, box, checkpoint=lambda: None, expected_environment="staging")
        assert recovered.db.execute("SELECT epoch,genesis_sha256,state FROM sql_audit_v3_scope").fetchone() == (epoch, genesis, "needs_reconcile")
        assert recovered.db.execute("SELECT epoch,candidate FROM sql_audit_v3_backup").fetchone() == (epoch, candidate.identity)
    finally:
        recovered.close()
