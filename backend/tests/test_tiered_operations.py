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


def test_cache_peak_and_high_events_do_not_pause_successful_job(tmp_path, monkeypatch):
    record = load("service-outcome.py")["record"]
    original = Path.read_text

    def read(path, *args, **kwargs):
        if str(path) == "/proc/self/cgroup":
            return "0::/test-group\n"
        if str(path) == "/sys/fs/cgroup/test-group/memory.peak":
            return str(161 * 1024**2)
        if str(path) == "/sys/fs/cgroup/test-group/memory.events":
            return "high 12\nmax 0\noom 0\noom_kill 0\n"
        if str(path) == "/sys/fs/cgroup/test-group/memory.stat":
            return "anon 67108864\nfile 110000000\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    value = record(tmp_path, "copy", "success", "0")
    assert not value["paused"] and value["cgroup_memory_peak_bytes"] == 161 * 1024**2
    assert value["cgroup_events"]["high"] == 12


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
    for unit in (copy, reader, backup):
        assert "LoadCredential=sftp-key:" in unit
        assert "ExecStartPre=+/usr/bin/python3 " in unit
        assert "TIERED_ARCHIVE_CGROUP_MONITOR=true" in unit


@pytest.mark.parametrize("event", ["max", "oom", "oom_kill", "anon"])
def test_event_or_anonymous_memory_limit_stops_inflight_work(tmp_path, event):
    from app.tiered_archive.resources import ANON_LIMIT, Sampler

    samples = iter(
        [
            {
                "available": True,
                "events": dict(
                    high=10,
                    max=int(event == "max"),
                    oom=int(event == "oom"),
                    oom_kill=int(event == "oom_kill"),
                ),
                "anon_bytes": ANON_LIMIT + int(event == "anon"),
                "peak_bytes": 190 * 1024**2,
            },
            {
                "available": True,
                "events": dict(high=10, max=0, oom=0, oom_kill=0),
                "anon_bytes": 32 * 1024**2,
                "peak_bytes": 190 * 1024**2,
            },
        ]
    )
    stopped = []
    sampler = Sampler(
        tmp_path, "copy", tmp_path, read=lambda: next(samples), stop=lambda: stopped.append(True)
    )
    sampler.sample()
    sampler.sample()
    assert stopped == [True] and (tmp_path / "PAUSE").exists()
    assert sampler.value["reasons"] and sampler.value["samples"] == 2


def test_unreadable_cgroup_cannot_be_an_accepted_measurement(tmp_path, monkeypatch):
    from app.tiered_archive import resources

    assert resources.read_cgroup(tmp_path / "missing")["available"] is False
    stopped = []
    sampler = resources.Sampler(
        tmp_path,
        "copy",
        tmp_path,
        read=lambda: {"available": False},
        stop=lambda: stopped.append(True),
    )
    sampler.sample()
    assert stopped and sampler.value["sample_failures"] == 1
    monkeypatch.setenv("TIERED_ARCHIVE_CGROUP_MONITOR", "true")
    monkeypatch.setenv("INVOCATION_ID", "fresh")
    value = load("service-outcome.py")["record"](tmp_path, "copy", "success", "0")
    assert value["paused"] and not value["inflight_measurement_complete"]


@pytest.mark.parametrize(
    "name,kind,link",
    [
        ("/absolute/escape", tarfile.REGTYPE, ""),
        ("release/../escape", tarfile.REGTYPE, ""),
        ("release/link", tarfile.SYMTYPE, "/absolute/escape"),
        ("release/link", tarfile.LNKTYPE, "release/file"),
        ("release/fifo", tarfile.FIFOTYPE, ""),
    ],
)
def test_bootstrap_rejects_unsafe_headers_before_creating_output(tmp_path, name, kind, link):
    archive = tmp_path / "hostile.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.type, info.linkname = kind, link
        info.size = 1 if kind == tarfile.REGTYPE else 0
        bundle.addfile(info, io.BytesIO(b"x") if info.size else None)
    with pytest.raises(ValueError, match="Unsafe"):
        load("verify-package.py")["extract"](
            archive, hashlib.sha256(archive.read_bytes()).hexdigest(), tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


def test_bootstrap_and_stop_helper_parse_with_older_system_python():
    import ast

    for name in (
        "verify-package.py",
        "verify-start.py",
        "service-outcome.py",
        "install-staging.py",
    ):
        ast.parse((DEPLOY / name).read_text(), feature_version=(3, 10))


def test_installation_respects_disk_reserve_before_copying(tmp_path, monkeypatch):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "shutil.disk_usage", lambda _: usage(40 * 1024**3, 38 * 1024**3, 2 * 1024**3)
    )
    (tmp_path / "wheel.whl").write_bytes(b"x")
    with pytest.raises(OSError, match="disk reserve"):
        load("install-staging.py")["disk_budget"](
            tmp_path, {"offline_unpacked_bytes": 512 * 1024**2}, tmp_path / "new"
        )
    assert not (tmp_path / "new").exists()


def test_staging_buckets_are_not_accepted_as_production_sources(monkeypatch):
    from app.raw_archive.runner import metadata_source_settings

    monkeypatch.setenv(
        "RAW_ARCHIVE_GATEWAY_DATABASE_URL", "postgresql://reader@127.0.0.1/gateway_staging"
    )
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_S3_BUCKET", "greenmind-gateway-staging-20261026")
    assert metadata_source_settings("gateway", "staging")["bucket"].startswith(
        "greenmind-gateway-staging-"
    )
    with pytest.raises(Exception, match="environment"):
        metadata_source_settings("gateway", "production")


def test_recovery_receipt_must_not_be_group_readable(tmp_path):
    receipt = tmp_path / "proof.json"
    receipt.write_text("{}")
    receipt.chmod(0o640)
    with pytest.raises(ValueError, match="protected"):
        load("verify-start.py")["verify"](receipt, "a" * 64)
