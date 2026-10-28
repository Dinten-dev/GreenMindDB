"""Private API clients require both filesystem access and exact peer identity."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_broker import Handler
from app.tiered_archive.read_service import ReadHandler, peer_uids, socket_permissions


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "+10001", "10001,", "1 2", "*", "١", "4294967295", "9" * 11, ",".join(["1"] * 9)],
)
def test_invalid_peer_list_cannot_relax_socket_access(monkeypatch, value):
    monkeypatch.setenv("TIERED_ARCHIVE_READ_PEER_UIDS", value)
    with pytest.raises(ArchiveBlocked, match="Explicit non-root"):
        peer_uids()


def test_defaults_remain_private_and_legacy_brokers_ignore_extra_peer_identities(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TIERED_ARCHIVE_READ_PEER_UIDS", raising=False)
    assert peer_uids() == frozenset()
    path = tmp_path / "history.sock"
    path.touch()
    socket_permissions(path, frozenset())
    assert path.stat().st_mode & 0o777 == 0o600
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(read_peer_uids={os.getuid() + 1})
    assert handler.authorized_peer(os.getuid())
    assert not handler.authorized_peer(os.getuid() + 1)


def test_only_explicit_api_uid_is_added_to_historical_handler(monkeypatch):
    monkeypatch.setenv("TIERED_ARCHIVE_READ_PEER_UIDS", "10001")
    monkeypatch.setattr(os, "getuid", lambda: 995)
    handler = object.__new__(ReadHandler)
    handler.server = SimpleNamespace(read_peer_uids=peer_uids())
    assert handler.authorized_peer(995) and handler.authorized_peer(10001)
    assert not handler.authorized_peer(0)
    assert not handler.authorized_peer(10002)


def test_shared_api_socket_requires_kernel_peer_credentials_before_loading_keys(monkeypatch):
    from app.tiered_archive import read_service

    for name in list(os.environ):
        if name.startswith("RAW_ARCHIVE_SFTP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    monkeypatch.setenv("TIERED_ARCHIVE_READ_PEER_UIDS", "10001")
    monkeypatch.delattr(read_service.socket, "SO_PEERCRED", raising=False)
    monkeypatch.setattr(read_service, "configured_read_only_box", lambda: pytest.fail("No keys"))
    with pytest.raises(ArchiveBlocked, match="Linux process credentials"):
        read_service.main()


def test_group_access_requires_owned_directory_with_exact_private_group_mode(tmp_path):
    path = tmp_path / "history.sock"
    path.touch()
    tmp_path.chmod(0o700)
    with pytest.raises(ArchiveBlocked, match="private owner/group"):
        socket_permissions(path, {10001})
    assert path.stat().st_mode & 0o777 != 0o660
    tmp_path.chmod(0o750)
    socket_permissions(path, {10001})
    assert path.stat().st_mode & 0o777 == 0o660


def test_shared_directory_symlink_cannot_relax_access(tmp_path):
    parent = tmp_path / "real"
    parent.mkdir(mode=0o750)
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    path = alias / "history.sock"
    path.touch()
    with pytest.raises(ArchiveBlocked, match="private owner/group"):
        socket_permissions(path, {10001})


@pytest.mark.parametrize("field", ["st_uid", "st_gid"])
def test_foreign_directory_ownership_blocks_group_access(tmp_path, monkeypatch, field):
    path = tmp_path / "history.sock"
    path.touch()
    tmp_path.chmod(0o750)
    original = Path.lstat

    def lstat(target, *args, **kwargs):
        info = original(target, *args, **kwargs)
        if target == tmp_path:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_uid=info.st_uid + int(field == "st_uid"),
                st_gid=info.st_gid + int(field == "st_gid"),
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ArchiveBlocked, match="private owner/group"):
        socket_permissions(path, {10001})
