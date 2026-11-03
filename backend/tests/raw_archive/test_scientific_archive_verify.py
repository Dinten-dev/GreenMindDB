"""Bounded offline Science readback against actual AES and native row parsing."""

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.raw_archive import scientific_archive_verify as verify
from app.raw_archive import scientific_v3 as science
from app.raw_archive.policy import ArchiveBlocked
from tests.raw_archive import test_scientific_v3_archive as fixtures
from tests.raw_archive.test_scientific_v3 import manifest, row


class ReadOnly:
    identity = "test-destination"

    def __init__(self, objects):
        self.objects, self.reads = objects, 0

    def download_tiered(self, key, path, limit):
        self.reads += 1
        raw = self.objects[key]
        assert len(raw) <= limit
        path.write_bytes(raw)
        path.chmod(0o600)


def setup(tmp_path, monkeypatch):
    with patch.object(
        fixtures,
        "manifest",
        lambda path, _: manifest(path, {"wav_feature": [row(62, first=bytes(range(16)))]}),
    ):
        source, objects = fixtures.setup_job(tmp_path)
    source["stage"] = "prepare"
    with (
        patch.object(science.storage, "disk_reserve"),
        patch.object(science, "_stage_authority"),
        patch.object(
            science.parts.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=20 * 1024**3, total=100 * 1024**3),
        ),
    ):
        prepared = science.publish_part(**source)
        source["stage"] = "transfer"
        science.publish_part(**source)
    # No Source data is available after publication.
    for path in source["source"].glob("*.pgcopy"):
        path.unlink()
    source["index_file"].chmod(0o400)
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    db = sqlite3.connect(root / "tiered.sqlite3")
    source["ledger"].db.backup(db)
    db.commit()
    box = ReadOnly(objects)
    monkeypatch.setattr(verify, "CatalogReadOnlyBox", ReadOnly)
    monkeypatch.setattr(verify.coordination, "bound_job_lock", lambda: None)
    monkeypatch.setattr(verify.storage, "disk_reserve", lambda *_: None)
    monkeypatch.setattr(verify, "_resources", lambda *_: None)
    monkeypatch.setattr(verify, "_root_directory", lambda p: Path(p))
    monkeypatch.setattr(verify, "_root_file", lambda p, _modes: Path(p).lstat())
    monkeypatch.setattr(
        verify.parts, "materialize_part", lambda *_a, **_k: pytest.fail("Source accessed")
    )
    kwargs = dict(
        metadata=source["source"],
        capture_sha256=source["capture_sha256"],
        index_file=source["index_file"],
        index_sha256=source["index_sha256"],
        position=0,
        workdir=source["workdir"],
        key=source["key"],
        box=box,
        ledger=SimpleNamespace(root=root, db=db, read_only=False),
        key_prefix=source["key_prefix"],
        expected_identity="test-destination",
        expected_epoch="1" * 32,
        expected_frame_sha256="3" * 64,
        archive_machine_sha256="4" * 64,
        authority_root=root,
        enable_archive_only=True,
    )
    return kwargs, prepared, objects


def test_exact_typed_readback_is_idempotent_and_source_independent(tmp_path, monkeypatch):
    args, old, objects = setup(tmp_path, monkeypatch)
    before = dict(objects)
    result = verify.verify(**args)
    assert result["reference"] == old["reference"] and result["typed_final_verified"] is True
    assert result["archive_accepted"] is result["writer_allowed"] is False
    assert result["native_restore_verified"] is result["whole_inventory_authenticated"] is False
    assert (
        result["archive_only_verify"] is True
        and result["source_access"] is result["fresh_source_proof"] is False
    )
    assert args["box"].reads == 1 and objects == before
    assert verify.verify(**args)["reference"] == result["reference"]
    with pytest.raises(ArchiveBlocked):
        science.writer_gate(result)


def test_archive_only_requires_explicit_switch(tmp_path, monkeypatch):
    args, _, _ = setup(tmp_path, monkeypatch)
    del args["enable_archive_only"]
    with pytest.raises(ArchiveBlocked, match="disabled"):
        verify.verify(**args)
    assert args["box"].reads == 0


@pytest.mark.parametrize(
    "case",
    [
        "frame",
        "epoch",
        "destination",
        "scope",
        "lock",
        "cipher",
        "key",
        "metadata",
        "provider",
        "pause",
    ],
)
def test_evidence_and_resource_fail_closed(tmp_path, monkeypatch, case):
    args, old, objects = setup(tmp_path, monkeypatch)
    if case == "frame":
        args["expected_frame_sha256"] = "f" * 64
    elif case == "epoch":
        args["expected_epoch"] = "f" * 32
    elif case == "destination":
        args["expected_identity"] = "another"
    elif case == "scope":
        args["ledger"].db.execute("UPDATE science_v3_scope SET state='needs_reconcile'")
        args["ledger"].db.commit()
    elif case == "lock":
        monkeypatch.setattr(
            verify.coordination,
            "bound_job_lock",
            lambda: (_ for _ in ()).throw(ArchiveBlocked("lock")),
        )
    elif case == "cipher":
        key = old["reference"]["key"]
        objects[key] = b"x" * len(objects[key])
    elif case == "key":
        args["key"] = b"x" * 32
    elif case == "metadata":
        args["index_sha256"] = "0" * 64
    elif case == "pause":
        (args["authority_root"] / "PAUSE").touch()
    else:
        args["box"] = object()
    with pytest.raises((ArchiveBlocked, ValueError)):
        verify.verify(**args)
    assert args["ledger"].db.execute("SELECT state FROM science_v3_part").fetchone() == (
        "prepared",
    )


def test_no_hardlink_or_symlink_derivative(tmp_path):
    source = tmp_path / "a"
    source.write_bytes(b"abc")
    source.chmod(0o600)
    os.link(source, tmp_path / "b")
    with pytest.raises(ArchiveBlocked):
        verify.checked_file(source, 3, "0" * 64, lambda: None)
    (tmp_path / "link").symlink_to(source)
    with pytest.raises(ArchiveBlocked):
        verify.checked_file(tmp_path / "link", 3, "0" * 64, lambda: None)


def test_loss_of_archive_lock_during_commit_rolls_back(tmp_path, monkeypatch):
    args, _, _ = setup(tmp_path, monkeypatch)
    calls = []

    def lock():
        calls.append(True)
        if len(calls) == 5:
            raise ArchiveBlocked("archive lock lost")

    monkeypatch.setattr(verify.coordination, "bound_job_lock", lock)
    with pytest.raises(ArchiveBlocked, match="archive lock lost"):
        verify.verify(**args)
    assert args["ledger"].db.execute("SELECT state FROM science_v3_part").fetchone() == (
        "prepared",
    )


def test_real_resource_policy_requires_root_production_cgroup(tmp_path, monkeypatch):
    import hashlib
    import time

    machine = b"archive-test-machine\n"

    def write(path, value):
        target = tmp_path / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else value.encode())
        return target

    write("/etc/machine-id", machine)
    unit = "gm-science-seal-123456789abc.service"
    write("/proc/self/cgroup", "0::/system.slice/" + unit)
    write("/proc/meminfo", "MemAvailable: 2097152 kB\n")
    prefix = "/sys/fs/cgroup/system.slice/" + unit + "/"
    write(prefix + "memory.max", str(192 * 1024**2))
    write(prefix + "memory.high", str(160 * 1024**2))
    write(prefix + "memory.swap.max", "0")
    cpu = write(prefix + "cpu.max", "25000 100000")
    events = write(prefix + "memory.events", "high 0\nmax 0\noom 0\noom_kill 0\n")
    monkeypatch.setattr(verify.sys, "platform", "linux")
    monkeypatch.setattr(verify.os, "geteuid", lambda: 0)
    monkeypatch.setattr(verify.os, "getloadavg", lambda: (1.0, 1.0, 1.0))
    monkeypatch.setattr(verify.shutil, "disk_usage", lambda _p: SimpleNamespace(free=4 * 1024**3))
    monkeypatch.setattr(verify, "Path", lambda path: tmp_path / str(path).lstrip("/"))
    monkeypatch.setenv("RAW_ARCHIVE_WORKER_ROLE", "archive")
    monkeypatch.setenv("RAW_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("INVOCATION_ID", "test")
    digest = hashlib.sha256(machine).hexdigest()
    verify._resources("/work", time.monotonic(), digest)
    cpu.write_text("25001 100000")
    with pytest.raises(ArchiveBlocked):
        verify._resources("/work", time.monotonic(), digest)
    cpu.write_text("25000 100000")
    events.write_text("high 1\nmax 0\noom 0\noom_kill 0\n")
    with pytest.raises(ArchiveBlocked):
        verify._resources("/work", time.monotonic(), digest)
    events.write_text("high 0\nmax 0\noom 0\noom_kill 0\n")
    monkeypatch.setenv("RETENTION_ENABLED", "true")
    with pytest.raises(ArchiveBlocked):
        verify._resources("/work", time.monotonic(), digest)


def test_equivalent_to_existing_science_verify_kernel(tmp_path, monkeypatch):
    import shutil

    args, _, objects = setup(tmp_path, monkeypatch)
    legacy_work = tmp_path / "legacy-work"
    shutil.copytree(args["workdir"], legacy_work)
    legacy_db = sqlite3.connect(":memory:")
    args["ledger"].db.backup(legacy_db)
    publisher, reader = fixtures.Store(objects), fixtures.Store(objects)
    legacy = dict(
        source=args["metadata"],
        capture_sha256=args["capture_sha256"],
        index_file=args["index_file"],
        index_sha256=args["index_sha256"],
        position=0,
        workdir=legacy_work,
        key=args["key"],
        publisher=publisher,
        verifier=reader,
        ledger=SimpleNamespace(db=legacy_db, read_only=False),
        key_prefix=args["key_prefix"],
        checkpoint=lambda: None,
        received_part=tmp_path / "absent-source",
        stage="verify",
    )
    with patch.object(science, "_stage_authority"):
        expected = science.publish_part(**legacy)
    actual = verify.verify(**args)
    extra = {
        "archive_only_verify",
        "source_access",
        "fresh_source_proof",
        "historical_handoff_epoch",
    }
    assert {key: value for key, value in actual.items() if key not in extra} == expected
