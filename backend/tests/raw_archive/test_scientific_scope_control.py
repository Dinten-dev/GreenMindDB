"""Local-only scope bootstrap tests; no real root or Source actions."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.raw_archive import scientific_capture as capture
from app.raw_archive import scientific_parts as parts
from app.raw_archive import scientific_scope_control as scope
from app.raw_archive import scientific_v3
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive.ledger import Ledger
from tests.raw_archive.test_scientific_v3 import manifest


def frame_fixture(tmp_path, monkeypatch):
    source, proof, _ = manifest(tmp_path, {})
    proof["finished_at"] = proof["started_at"]
    raw = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    capture_file = source / "capture.json"
    capture_file.chmod(0o600)
    capture_file.write_bytes(raw)
    capture_file.chmod(0o400)
    capsha = hashlib.sha256(raw).hexdigest()
    idx = tmp_path / "index.json"
    result = parts.prepare_index(source, capsha, idx, checkpoint=lambda: None)
    binding = {
        "hostname": "source-host",
        "machine_sha256": "1" * 64,
        "boot_id": "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12,
        "lock_device": 2049,
        "lock_inode": 393144,
        "lease_uid": 994,
        "lease_command_sha256": "5" * 64,
        "lease_executable_sha256": "6" * 64,
    }
    frame = {
        "format": "greenmind.science-source-handoff.v1",
        "source": "gateway",
        "capture_sha256": capsha,
        "index_sha256": result["index_sha256"],
        "capture_id": proof["capture_id"],
        "scope_sha256": proof["scope_sha256"],
        "lease_session": "8" * 32,
        "native_files": [
            {"table": x["table"], "rows": x["rows"], "bytes": x["bytes"], "sha256": x["sha256"]}
            for x in proof["files"]
        ],
        "part_count": len(result["index"]["parts"]),
        "hostname": binding["hostname"],
        "machine_sha256": binding["machine_sha256"],
        "boot_id": binding["boot_id"],
        "uid": 0,
        "source_lock": {
            "device": 2049,
            "inode": 393144,
            "pid": 1234,
            "uid": 994,
            "start_ticks": 5678,
            "command_sha256": "5" * 64,
            "executable_sha256": "6" * 64,
        },
        "producer_sha256": scope._code_sha(scope),
        "parts_code_sha256": scope._code_sha(parts),
        "capture_code_sha256": scope._code_sha(capture),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(scope, "_private_json", lambda *_args: frame)
    return source, proof, capsha, idx, result["index_sha256"], binding, frame


def test_frame_binds_full_index_capture_machine_code_and_live_lease(tmp_path, monkeypatch):
    source, proof, capsha, idx, idxsha, binding, frame = frame_fixture(tmp_path, monkeypatch)
    checked, _ = scope._frame(
        source, capsha, idx, idxsha, "/frame", "f" * 64, binding, "8" * 32, lambda: None
    )
    assert checked["capture_id"] == proof["capture_id"]
    for key, wrong in (
        ("lease_session", "7" * 32),
        ("machine_sha256", "9" * 64),
        ("parts_code_sha256", "0" * 64),
    ):
        before = frame[key]
        frame[key] = wrong
        with pytest.raises(ArchiveBlocked, match="handoff differs"):
            scope._frame(
                source, capsha, idx, idxsha, "/frame", "f" * 64, binding, "8" * 32, lambda: None
            )
        frame[key] = before
    frame["source_lock"]["uid"] = 992
    with pytest.raises(ArchiveBlocked, match="handoff differs"):
        scope._frame(
            source, capsha, idx, idxsha, "/frame", "f" * 64, binding, "8" * 32, lambda: None
        )


def test_fresh_bootstrap_creates_exact_epoch_and_keeps_sql_hold(tmp_path, monkeypatch):
    source, proof, capsha, idx, idxsha, binding, _ = frame_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(scope, "_authority", lambda *_args: ("8" * 32, binding))

    def create_private(path):
        path = Path(path)
        if path.exists():
            raise ArchiveBlocked("Entirely new science ledger directory required")
        path.mkdir(mode=0o700)
        return path

    monkeypatch.setattr(scope, "_new_science_dir", create_private)
    root = tmp_path / "new-science"
    result = scope.bootstrap_science(
        root,
        object(),
        source_binding_path="/binding",
        source_binding_sha256="1" * 64,
        metadata_dir=source,
        capture_sha256=capsha,
        index_file=idx,
        index_sha256=idxsha,
        frame_path="/frame",
        frame_sha256="f" * 64,
        checkpoint=lambda: None,
    )
    assert result["status"] == "SCIENCE_SCOPE_CURRENT" and result["sql_scope"] == "HOLD"
    ledger = Ledger(root)
    scientific_v3._archive_scope(ledger, capsha, idxsha, proof["capture_id"])
    assert ledger.db.execute("SELECT state FROM sql_deletion_scope").fetchone() == (
        "needs_reconcile",
    )
    with pytest.raises(ArchiveBlocked, match="binding"):
        scientific_v3._archive_scope(ledger, "0" * 64, idxsha, proof["capture_id"])
    ledger.close()
    with pytest.raises(ArchiveBlocked, match="new science ledger"):
        scope.bootstrap_science(
            root,
            object(),
            source_binding_path="/binding",
            source_binding_sha256="1" * 64,
            metadata_dir=source,
            capture_sha256=capsha,
            index_file=idx,
            index_sha256=idxsha,
            frame_path="/frame",
            frame_sha256="f" * 64,
            checkpoint=lambda: None,
        )


def test_fresh_source_lease_frame_checks_nonce_and_holder(tmp_path, monkeypatch):
    _, _, _, _, _, binding, source_frame = frame_fixture(tmp_path, monkeypatch)
    frame = {
        "format": "greenmind.science-lease-handoff.v1",
        "lease_session": source_frame["lease_session"],
        "source_lock": source_frame["source_lock"],
        "hostname": source_frame["hostname"],
        "machine_sha256": source_frame["machine_sha256"],
        "boot_id": source_frame["boot_id"],
        "uid": 0,
        "producer_sha256": scope._code_sha(scope),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(scope, "_private_json", lambda *_args: frame)
    scope.validate_lease_handoff("/frame", "a" * 64, binding, "8" * 32)
    with pytest.raises(ArchiveBlocked, match="lock frame"):
        scope.validate_lease_handoff("/frame", "a" * 64, binding, "9" * 32)
    frame["source_lock"]["inode"] = 1
    with pytest.raises(ArchiveBlocked, match="lock frame"):
        scope.validate_lease_handoff("/frame", "a" * 64, binding, "8" * 32)


def test_challenge_dispatches_only_exact_nonce_and_hashes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scope,
        "source_handoff",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"frame_sha256": "a" * 64},
    )
    payload = {
        "format": "greenmind.science-source-challenge.v1",
        "action": "capture-index",
        "lease_session": "8" * 32,
        "payload": {"capture_sha256": "b" * 64, "index_sha256": "c" * 64},
    }
    scope.produce_challenge_frame(
        json.dumps(payload).encode(),
        "/out",
        checkpoint=lambda: None,
        source="/source",
        index_file="/index",
    )
    assert calls[0][1]["lease_session"] == "8" * 32
    payload["payload"]["capture_sha256"] = "broken"
    with pytest.raises(ArchiveBlocked):
        scope.produce_challenge_frame(
            json.dumps(payload).encode(),
            "/out",
            checkpoint=lambda: None,
            source="/source",
            index_file="/index",
        )


def test_source_lock_uses_actual_flock_holder_uid_and_generation(monkeypatch):
    import io
    import pwd
    import stat
    from types import SimpleNamespace

    original_lstat, original_read_text = Path.lstat, Path.read_text
    original_read_bytes, original_open = Path.read_bytes, Path.open
    lock_path = "/var/lib/greenmind-archive-coordination/jobs.lock"
    process_stat = "1234 (lease) " + " ".join(["S"] + ["0"] * 18 + ["5678"] + ["0"] * 5)
    current_locks = ["1: FLOCK ADVISORY WRITE 1234 08:01:393144 0 EOF"]

    def fake_lstat(path, *args, **kwargs):
        if str(path) == lock_path:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o660, st_uid=0, st_nlink=1, st_dev=2049, st_ino=393144
            )
        return original_lstat(path, *args, **kwargs)

    def fake_text(path, *args, **kwargs):
        key = str(path)
        if key == "/proc/locks":
            return "\n".join(current_locks)
        if key == "/proc/1234/status":
            return "Name:\tlease\nUid:\t994\t994\t994\t994\n"
        if key == "/proc/1234/stat":
            return process_stat
        return original_read_text(path, *args, **kwargs)

    def fake_bytes(path):
        if str(path) == "/proc/1234/cmdline":
            return b"python\0lease\0"
        return original_read_bytes(path)

    def fake_open(path, *args, **kwargs):
        if str(path) == "/proc/1234/exe":
            return io.BytesIO(b"pinned executable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "read_text", fake_text)
    monkeypatch.setattr(Path, "read_bytes", fake_bytes)
    monkeypatch.setattr(Path, "open", fake_open)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=994))
    monkeypatch.setattr(scope.os, "major", lambda value: 8)
    monkeypatch.setattr(scope.os, "minor", lambda value: 1)
    observed = scope._source_lock(full=True)
    assert observed["pid"] == 1234 and observed["uid"] == 994 and observed["start_ticks"] == 5678
    current_locks.clear()
    with pytest.raises(ArchiveBlocked, match="unique live holder"):
        scope._source_lock(full=False)


def test_frame_publication_rechecks_and_preserves_existing(tmp_path):
    path = tmp_path / "frame"

    def expired():
        raise scope.ArchiveBlocked("lease expired")

    with pytest.raises(scope.ArchiveBlocked, match="expired"):
        scope._publish_checked_frame(path, b"private frame", expired)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        scope._publish_checked_frame(path, b"replacement", lambda: None)
    assert path.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [path]
