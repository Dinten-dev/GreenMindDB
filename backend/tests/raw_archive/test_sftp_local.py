"""Real OpenSSH SFTP protocol roundtrips; only temporary local files, no network."""

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.raw_archive import storage
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.storage import RemoteMissing, StorageBox

from .test_daily import wav_bytes


@pytest.fixture
def local_box(tmp_path, monkeypatch):
    binary = next(
        (
            p
            for p in ("/usr/libexec/sftp-server", "/usr/lib/openssh/sftp-server")
            if Path(p).is_file()
        ),
        None,
    )
    if binary is None or not Path("/usr/bin/sftp").is_file():
        pytest.skip("Real local OpenSSH SFTP server is not installed")
    backend = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", str(backend))
    monkeypatch.chdir(tmp_path)
    key, hosts = tmp_path / "key", tmp_path / "known_hosts"
    key.touch()
    hosts.touch()
    box = StorageBox(host="local.test", user="isolated", key=key, known_hosts=hosts)
    box.command = ["/usr/bin/sftp", "-D", binary, "-f", "-b", "-"]
    return box


def archive_key(payload):
    return (
        "staging/2026-09-25/mac-14-c1-9f-d9-42-a4/091500000000_gateway_"
        + "a" * 64
        + "_"
        + hashlib.sha256(payload).hexdigest()
        + ".wav"
    )


def test_real_sftp_missing_upload_readback_retry_and_restore(local_box, tmp_path):
    payload = wav_bytes(42)
    key = archive_key(payload)
    source = tmp_path / "source.wav"
    source.write_bytes(payload)
    with pytest.raises(RemoteMissing):
        local_box.download(key, tmp_path / "absent.wav", len(payload))
    local_box.publish(source, key)
    remote = Path(local_box.path(key))
    before = remote.stat().st_mtime_ns
    local_box.publish(source, key)
    assert remote.stat().st_mtime_ns == before
    returned = tmp_path / "returned.wav"
    local_box.download(key, returned, len(payload))
    assert returned.read_bytes() == payload
    restored = tmp_path / "restored.wav"
    local_box.restore(key, hashlib.sha256(payload).hexdigest(), len(payload), restored)
    assert restored.read_bytes() == payload
    with pytest.raises(ArchiveBlocked, match="new path"):
        local_box.restore(key, hashlib.sha256(payload).hexdigest(), len(payload), restored)


@pytest.mark.parametrize("damage", ["short", "oversize", "same-size"])
def test_existing_damaged_archive_is_never_overwritten(local_box, tmp_path, damage):
    payload = wav_bytes(42)
    key = archive_key(payload)
    source = tmp_path / "source.wav"
    source.write_bytes(payload)
    local_box.publish(source, key)
    remote = Path(local_box.path(key))
    broken = {"short": b"bad", "oversize": payload * 2, "same-size": b"X" * len(payload)}[damage]
    remote.write_bytes(broken)
    with pytest.raises(ArchiveBlocked):
        local_box.publish(source, key)
    assert remote.read_bytes() == broken and source.read_bytes() == payload


def test_ambiguous_network_failure_is_not_missing(local_box, tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.write_bytes(wav_bytes(1))

    def fail(*args):
        raise ArchiveBlocked("connection refused")

    monkeypatch.setattr(local_box, "download", fail)
    with pytest.raises(ArchiveBlocked, match="connection refused"):
        local_box.publish(source, archive_key(source.read_bytes()))
    assert not (tmp_path / local_box.root).exists()


def test_timeout_kills_transfer_descendants(local_box, tmp_path, monkeypatch):
    sentinel = tmp_path / "must-not-be-written"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(sentinel)!r}).touch()"
    parent = (
        f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]);'
        "time.sleep(30)"
    )
    local_box.command = [sys.executable, "-c", parent]
    monkeypatch.setattr(storage, "SFTP_TIMEOUT_SECONDS", 0.25)
    with pytest.raises(subprocess.TimeoutExpired):
        local_box.run("ignored")
    time.sleep(1.2)
    assert not sentinel.exists()


@pytest.mark.parametrize("unsafe", ["name\nput other", "file\x00.wav", "*.wav", "file[1].wav"])
def test_sftp_paths_reject_control_and_glob_characters(unsafe):
    with pytest.raises(ArchiveBlocked, match="Unsafe"):
        StorageBox.quote(unsafe)


def test_download_does_not_overwrite_local_file(local_box, tmp_path):
    existing = tmp_path / "important"
    existing.write_bytes(b"original")
    with pytest.raises(ArchiveBlocked, match="unused path"):
        local_box.download(archive_key(b"anything"), existing, 20)
    assert existing.read_bytes() == b"original"


def test_sftp_configuration_does_not_read_user_ssh_config(tmp_path):
    key = tmp_path / "key"
    key.touch()
    box = StorageBox(host="example.test", user="isolated", key=key, known_hosts=key)
    assert box.command[:3] == ["/usr/bin/sftp", "-F", "/dev/null"]
    assert "-oStrictHostKeyChecking=yes" in box.command
    assert os.path.isabs(box.command[0])
