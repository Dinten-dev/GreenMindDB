"""Review corrections: source scope, key recovery, offline install and stop rules."""

import hashlib
import io
import json
import os
import runpy
import signal
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.raw_archive.health import SafetyPause
from app.tiered_archive.execution import bounded_job

DEPLOY = Path(__file__).resolve().parents[2] / "deploy/tiered-archive"


def load(name):
    return runpy.run_path(str(DEPLOY / name))


def test_global_deadline_persists_pause_and_restores_signal_handler(tmp_path):
    before = signal.getsignal(signal.SIGALRM)
    with pytest.raises(SafetyPause, match="runtime_budget"):
        with bounded_job(tmp_path, seconds=0.03):
            time.sleep(2)
    value = json.loads((tmp_path / "copy-last-run.json").read_text())
    assert value["state"] == "paused" and value["reason"] == "runtime_budget"
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_paused_job_does_not_begin_work(tmp_path):
    (tmp_path / "control").mkdir()
    (tmp_path / "control/PAUSE").touch()
    with pytest.raises(SafetyPause, match="manual_pause"):
        with bounded_job(tmp_path):
            pytest.fail("Must remain paused")
    assert not (tmp_path / "copy-last-run.json").exists()


def test_oom_or_timeout_blocks_later_cycles_and_exports_private_metrics(tmp_path):
    record = load("service-outcome.py")["record"]
    control = tmp_path / "control"
    control.mkdir()
    value = record(tmp_path, "copy", "oom-kill", "9", control)
    assert value["paused"] and (control / "PAUSE").exists()
    assert (
        'greenmind_tiered_staging_job_failed{job="copy"} 1'
        in (tmp_path / "copy-service-result.prom").read_text()
    )
    assert (control / "PAUSE").stat().st_mode & 0o027 == 0


def test_recovered_key_roundtrip_and_pinned_receipt(tmp_path):
    prove = load("prove-key-recovery.py")["prove"]
    verify = load("verify-start.py")["verify"]
    primary = tmp_path / "primary"
    recovered = tmp_path / "recovered"
    receipt = tmp_path / "receipt.json"
    key = os.urandom(32)
    for path in (primary, recovered):
        path.write_bytes(key)
        path.chmod(0o600)
    result = prove(primary, recovered, "test-vault/independent-retrieval", receipt)
    assert result["key_sha256"] == hashlib.sha256(key).hexdigest()
    assert verify(receipt, result["key_sha256"])["key_recovery_verified"]
    with pytest.raises(ValueError):
        verify(receipt, "0" * 64)
    with pytest.raises(ValueError, match="Independently recovered"):
        prove(primary, primary, "test-vault", tmp_path / "bad.json")
    recovered.write_bytes(os.urandom(32))
    with pytest.raises(ValueError, match="differs"):
        prove(primary, recovered, "test-vault", tmp_path / "bad.json")
    data = json.loads(receipt.read_text())
    data["verified_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Recent key recovery"):
        verify(receipt, result["key_sha256"])


def test_group_peak_alone_pauses_even_after_successful_exit(tmp_path, monkeypatch):
    record = load("service-outcome.py")["record"]
    original = Path.read_text

    def read(path, *args, **kwargs):
        if str(path) == "/proc/self/cgroup":
            return "0::/test-group\n"
        if str(path) == "/sys/fs/cgroup/test-group/memory.peak":
            return str(161 * 1024**2)
        if str(path) == "/sys/fs/cgroup/test-group/memory.events":
            return "oom_kill 0\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    value = record(tmp_path, "copy", "success", "0")
    assert value["paused"] and value["cgroup_memory_peak_bytes"] == 161 * 1024**2


def test_offline_installer_never_uses_package_index(tmp_path):
    command = load("install-staging.py")["offline_pip"](
        Path("/review/python"), tmp_path, "requirements.lock"
    )
    assert "--no-index" in command and "--require-hashes" in command and "--isolated" in command
    assert command[command.index("--find-links") + 1] == str(tmp_path / "wheelhouse")


def test_tar_hash_is_checked_before_extraction_and_paths_are_bounded(tmp_path):
    extract = load("verify-package.py")["extract"]
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("release/file")
        info.size = 2
        bundle.addfile(info, io.BytesIO(b"ok"))
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="nothing extracted"):
        extract(archive, "0" * 64, tmp_path / "wrong")
    assert not (tmp_path / "wrong").exists()
    assert extract(archive, expected, tmp_path / "good")["extracted_files"] == 1
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("release/../../escape")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        extract(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), tmp_path / "unsafe")
    assert not (tmp_path / "unsafe").exists()


def test_units_separate_dump_budget_and_remain_manual_until_review():
    copy = (DEPLOY / "greenmind-tiered-archive.service").read_text()
    backup = (DEPLOY / "greenmind-tiered-backup@.service").read_text()
    reader = (DEPLOY / "greenmind-tiered-read.service").read_text()
    for unit in (copy, backup):
        assert "flock -n " in unit and "MemoryMax=192M" in unit
        assert "TimeoutStartSec=330" in unit and "TimeoutStopSec=30" in unit
        assert "OOMPolicy=stop" in unit and "ExecStopPost=" in unit
    assert (
        "LoadCredential=backup-key:" in backup and "LoadCredential=backup-key:" not in copy + reader
    )
    assert "history.sock" not in copy
    assert "ReadWritePaths=/var/lib/greenmind/tiered-archive/staging" in copy
