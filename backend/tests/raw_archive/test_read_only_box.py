"""Subaccount reads preserve copy receipts without exposing mutation operations."""

import os

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import PREFIX, ReadOnlyBox, configured_read_only_box
from app.raw_archive.restore import archived_file
from app.raw_archive.storage import StorageBox


def make_box(tmp_path, user="u676312-sub1", host="u676312-sub1.your-storagebox.de"):
    key, hosts = tmp_path / "read_key", tmp_path / "hosts"
    key.touch()
    hosts.touch()
    return ReadOnlyBox(user=user, host=host, key=key, known_hosts=hosts)


@pytest.mark.parametrize("kind", ["gateway", "direct"])
def test_subaccount_restore_uses_existing_primary_receipt(world, tmp_path, monkeypatch, kind):
    config, _, source, archive, add, copy = world
    record = add(kind, 1)
    box = make_box(tmp_path)
    archive.identity = box.identity
    copy()
    batches = []

    def get_only(_transport, batch, *, output, max_bytes, missing_path):
        assert batch.startswith("get ") and batch.count("\n") == 1
        assert not missing_path.startswith("greenmind-raw/")
        assert missing_path == record.remote_key(config.namespace)
        batches.append(batch)
        output.write_bytes(source.data[record.key])

    monkeypatch.setattr(StorageBox, "run", get_only)
    with archived_file(config, box, kind=record.kind, bucket=record.bucket, key=record.key) as path:
        assert path.read_bytes() == source.data[record.key]
    assert len(batches) == 1
    for method in ("publish", "publish_catalog", "evict", "run", "download_snapshot"):
        assert not hasattr(box, method)


def test_foreign_receipt_cannot_use_subaccount(world, tmp_path, monkeypatch):
    config, _, _, _, add, copy = world
    record = add("gateway", 1)
    copy()
    monkeypatch.setattr(StorageBox, "run", lambda *_a, **_k: pytest.fail("No SFTP allowed"))
    with pytest.raises(ArchiveBlocked, match="identity"):
        with archived_file(
            config, make_box(tmp_path), kind=record.kind, bucket=record.bucket, key=record.key
        ):
            pytest.fail("Foreign destination must not restore")


@pytest.mark.parametrize(
    "user,host",
    [
        ("u676312", "u676312.your-storagebox.de"),
        ("u676312-sub1", "u676312.your-storagebox.de"),
        ("u676312-sub1", "foreign.example"),
    ],
)
def test_primary_or_mismatched_accounts_rejected(tmp_path, user, host):
    with pytest.raises(ArchiveBlocked, match="subaccount"):
        make_box(tmp_path, user, host)


def test_partial_read_only_settings_never_fall_back(monkeypatch):
    for name in list(os.environ):
        if name.startswith(PREFIX):
            monkeypatch.delenv(name)
    assert configured_read_only_box() is None
    monkeypatch.setenv(PREFIX + "USER", "u676312-sub1")
    with pytest.raises(ArchiveBlocked, match="Complete separate"):
        configured_read_only_box()


def test_reusing_upload_key_is_rejected(tmp_path, monkeypatch):
    key = tmp_path / "key"
    hosts = tmp_path / "hosts"
    key.touch()
    hosts.touch()
    for name, value in {
        "USER": "u676312-sub1",
        "HOST": "u676312-sub1.your-storagebox.de",
        "KEY": str(key),
        "KNOWN_HOSTS": str(hosts),
    }.items():
        monkeypatch.setenv(PREFIX + name, value)
    monkeypatch.setenv("RAW_ARCHIVE_SFTP_KEY", str(key))
    with pytest.raises(ArchiveBlocked, match="reuse"):
        configured_read_only_box()


def test_unsafe_path_rejected_before_transport(tmp_path, monkeypatch):
    box = make_box(tmp_path)
    monkeypatch.setattr(StorageBox, "run", lambda *_a, **_k: pytest.fail("No SFTP allowed"))
    with pytest.raises(ArchiveBlocked, match="Unsafe"):
        box.download("../private.wav", tmp_path / "new.wav", 10)
