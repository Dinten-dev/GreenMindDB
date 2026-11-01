"""Recovery verifies each fake remote object independently; no real service access."""

from pathlib import Path

import pytest

from app.raw_archive import coordination, scientific_v3
from app.raw_archive import scientific_reconcile as recovery
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import ReadOnlyBox
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.sql_runtime import ScopedReadStore
from tests.raw_archive.test_scientific_v3_archive import run, setup_job, whole


def make_restored(tmp_path, monkeypatch):
    args, objects = setup_job(tmp_path)
    identity = "sftp://u123@u123.your-storagebox.de:23/greenmind-raw"
    args["publisher"].identity = identity
    args["verifier"].identity = identity
    run(args)
    args["position"] = 1
    run(args)
    whole(args)
    target = Ledger(tmp_path / "restored-science")
    source_db = args["ledger"].db
    for table in (
        "science_v3_scope",
        "science_v3_scope_epoch",
        "science_v3_part",
        "science_v3_whole",
    ):
        ddl = source_db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        target.db.execute(ddl)
        rows = source_db.execute("SELECT * FROM " + table).fetchall()
        for row in rows:
            target.db.execute(
                "INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in row) + ")", row
            )
    target.db.execute("UPDATE science_v3_scope SET state='needs_reconcile'")
    target.db.execute("CREATE TABLE sql_deletion_scope (id INTEGER PRIMARY KEY, state TEXT)")
    target.db.execute("INSERT INTO sql_deletion_scope VALUES (1,'needs_reconcile')")
    target.db.commit()
    authority = Ledger(tmp_path / "authority")
    transport = object.__new__(ReadOnlyBox)
    transport.identity = identity
    verifier = ScopedReadStore(transport)

    def download(key, path, size):
        data = objects[key]
        Path(path).write_bytes(data)
        Path(path).chmod(0o600)

    monkeypatch.setattr(verifier, "download", download)
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0)
    monkeypatch.setattr(scientific_v3, "_directory", lambda p: Path(p))
    monkeypatch.setattr(recovery.control, "_authority", lambda *_args: ("8" * 32, {}))
    monkeypatch.setattr(recovery.control, "validate_lease_handoff", lambda *_args: None)
    monkeypatch.setattr(coordination, "bound_job_lock", lambda: None)
    from app.tiered_archive import storage

    monkeypatch.setattr(storage, "disk_reserve", lambda *_: None)
    work = tmp_path / "reconcile-work"
    work.mkdir(mode=0o700)
    kwargs = dict(
        ledger=target,
        authority_ledger=authority,
        verifier=verifier,
        key=args["key"],
        workdir=work,
        source_binding_path="/source-binding",
        source_binding_sha256="d" * 64,
        lease_frame_path="/lease-frame",
        lease_frame_sha256="e" * 64,
        checkpoint=lambda: None,
    )
    return kwargs, objects


def test_each_remote_part_must_pass_before_new_scope_epoch(tmp_path, monkeypatch):
    kwargs, _ = make_restored(tmp_path, monkeypatch)
    first = recovery.reconcile_step(position=0, **kwargs)
    assert first["status"] == "SCIENCE_RECONCILING" and first["verified_parts"] == 1
    assert kwargs["ledger"].db.execute("SELECT state FROM science_v3_scope").fetchone() == (
        "needs_reconcile",
    )
    assert recovery.reconcile_step(position=0, **kwargs)["verified_parts"] == 1
    last = recovery.reconcile_step(position=1, **kwargs)
    assert last["status"] == "SCIENCE_SCOPE_CURRENT" and last["verified_parts"] == 2
    assert kwargs["ledger"].db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    assert kwargs["ledger"].db.execute(
        "SELECT count(*) FROM science_v3_scope_history"
    ).fetchone() == (1,)
    scientific_v3._archive_scope(kwargs["ledger"])
    assert recovery.reconcile_step(position=1, **kwargs)["status"] == "SCIENCE_SCOPE_CURRENT"
    kwargs["ledger"].close()
    kwargs["authority_ledger"].close()


def test_corrupted_remote_part_never_advances_scope(tmp_path, monkeypatch):
    kwargs, objects = make_restored(tmp_path, monkeypatch)
    row = (
        kwargs["ledger"]
        .db.execute(
            "SELECT reference FROM science_v3_part ORDER BY json_extract(binding,'$.position') LIMIT 1"
        )
        .fetchone()
    )
    import json

    ref = json.loads(row[0])
    objects[ref["key"]] = b"bad"
    with pytest.raises(ArchiveBlocked, match="readback differs"):
        recovery.reconcile_step(position=0, **kwargs)
    assert kwargs["ledger"].db.execute("SELECT state FROM science_v3_scope").fetchone() == (
        "needs_reconcile",
    )
    kwargs["ledger"].close()
    kwargs["authority_ledger"].close()
