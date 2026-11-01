"""Synthetic UID trust and real local copies/SQL; no native root or PostgreSQL proof."""

import os

import pytest
from sqlalchemy import MetaData, Table, create_engine, text

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import (
    restore_baseline as baseline,
)
from app.tiered_archive import (
    restore_inputs as inputs,
)
from app.tiered_archive import (
    restore_session as rs,
)
from tests.test_tiered_restore_session import log, plan


@pytest.fixture
def synthetic_operator(monkeypatch):
    # Explicit synthetic operator UID; production default remains root UID0.
    monkeypatch.setattr(inputs, "ROOT_UID", os.geteuid())


def test_private_freeze_only_new_copies_preserves_originals(tmp_path, synthetic_operator):
    _, _, descriptors = plan(tmp_path, [[[log("one")]]])
    source = tmp_path / "page-0"
    original = {path.name: (path.read_bytes(), path.stat().st_mode) for path in source.iterdir()}
    output = tmp_path / "frozen"
    manifest_sha = checksum(source / "manifest.json")
    result = inputs.freeze_restore_page(source, output, manifest_sha, checkpoint=lambda: None)
    assert result["originals_changed"] is False
    assert {
        path.name: (path.read_bytes(), path.stat().st_mode) for path in source.iterdir()
    } == original
    assert oct(output.stat().st_mode & 0o777) == "0o500"
    for file in output.iterdir():
        assert oct(file.stat().st_mode & 0o777) == "0o400"
        assert inputs.frozen(file)
        assert file.read_bytes() == original[file.name][0]
    descriptor, sha = descriptors[0]
    copied = tmp_path / "frozen-descriptor.json"
    inputs.freeze_restore_descriptor(descriptor, copied, sha, checkpoint=lambda: None)
    assert inputs.frozen(copied) and copied.read_bytes() == descriptor.read_bytes()
    with pytest.raises(ArchiveBlocked, match="new isolated"):
        inputs.freeze_restore_page(source, output, manifest_sha, checkpoint=lambda: None)


def test_private_audit_rejects_group_readable_writable_or_symlink_input(
    tmp_path, synthetic_operator
):
    file = tmp_path / "input"
    file.write_bytes(b"private")
    for mode in (0o444, 0o600, 0o640):
        file.chmod(mode)
        with pytest.raises(ArchiveBlocked, match="0400"):
            inputs.frozen(file)
    file.chmod(0o400)
    link = tmp_path / "alias"
    link.symlink_to(file)
    with pytest.raises(ArchiveBlocked):
        inputs.frozen(link)


def test_wrong_pinned_copy_leaves_original_and_only_new_intent(tmp_path, synthetic_operator):
    plan(tmp_path, [[[log("one")]]])
    source = tmp_path / "page-0"
    original = (source / "manifest.json").read_bytes()
    with pytest.raises(ArchiveBlocked, match="Pinned"):
        inputs.freeze_restore_page(source, tmp_path / "new", "f" * 64, checkpoint=lambda: None)
    assert (source / "manifest.json").read_bytes() == original
    assert not (tmp_path / "new").exists()


def test_bounded_keyset_master_baseline_covers_every_row_without_full_count(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "baseline.sqlite"))
    with engine.begin() as db:
        db.execute(text("CREATE TABLE direct_device(id TEXT PRIMARY KEY, key_hash TEXT)"))
        for number in range(13):
            db.execute(
                text("INSERT INTO direct_device VALUES(:id,:private)"),
                dict(id=f"{number:03d}", private="PRIVATE"),
            )
        table = Table("direct_device", MetaData(), autoload_with=db)
        masters = {table.name: table}
        snapshot = dict(master_epoch=0)
        baseline.ensure(db, masters, snapshot, rs.schema_fingerprint)
        assert baseline.anchor(db, masters, snapshot) == "0" * 64
    counts = []
    while True:
        with engine.begin() as db:
            result = baseline.advance(
                db, masters, snapshot, checkpoint=lambda: None, maximum_rows=2, maximum_bytes=1024
            )
            counts.append(result["rows"])
            assert result["rows"] <= 2 and result["decoded_bytes"] <= 1024
            if baseline.anchor(db, masters, snapshot) != "0" * 64:
                records = baseline.journal(db)
                assert (
                    db.execute(text("SELECT rows FROM greenmind_restore_baseline")).scalar_one()
                    == 13
                )
                assert "PRIVATE" not in str(db.execute(records.select()).fetchall())
                break
    assert sum(counts) == 13 and len(counts) >= 7
    with engine.begin() as db, pytest.raises(ArchiveBlocked, match="epoch"):
        baseline.anchor(db, masters, dict(master_epoch=1))


def test_baseline_checkpoint_failure_does_not_advance_cursor(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "baseline.sqlite"))
    with engine.begin() as db:
        db.execute(text("CREATE TABLE sensor(id TEXT PRIMARY KEY)"))
        db.execute(text("INSERT INTO sensor VALUES('one')"))
        masters = {"sensor": Table("sensor", MetaData(), autoload_with=db)}
        baseline.ensure(db, masters, dict(master_epoch=0), rs.schema_fingerprint)

    def stop():
        raise RuntimeError("synthetic checkpoint")

    with pytest.raises(RuntimeError), engine.begin() as db:
        baseline.advance(db, masters, dict(master_epoch=0), checkpoint=stop)
    with engine.connect() as db:
        assert db.execute(text("SELECT rows FROM greenmind_restore_baseline")).scalar_one() == 0
        assert (
            db.execute(text("SELECT cursor FROM greenmind_restore_baseline")).scalar_one() is None
        )


def test_secret_master_pk_is_never_copied_into_baseline_cursor(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "private-baseline.sqlite"))
    with engine.begin() as db:
        db.execute(text("CREATE TABLE direct_pairing(code_hash TEXT PRIMARY KEY)"))
        db.execute(text("INSERT INTO direct_pairing VALUES('PRIVATE-TOKEN-HASH')"))
        masters = {"direct_pairing": Table("direct_pairing", MetaData(), autoload_with=db)}
        baseline.ensure(db, masters, dict(master_epoch=0), rs.schema_fingerprint)
    with pytest.raises(ArchiveBlocked, match="Private master PK"), engine.begin() as db:
        baseline.advance(db, masters, dict(master_epoch=0), checkpoint=lambda: None)
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT cursor FROM greenmind_restore_baseline")).scalar_one() is None
        )
        assert "PRIVATE-TOKEN-HASH" not in str(
            db.execute(text("SELECT * FROM greenmind_restore_baseline")).fetchall()
        )
