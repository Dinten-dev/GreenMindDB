"""Relay permissions change only an expected private socket, never ordinary files."""

import importlib.util
import os
import socket
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/archive-server/direct-relay-permissions.py"
spec = importlib.util.spec_from_file_location("direct_relay_permissions", SCRIPT)
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)


def test_reconnect_socket_gets_only_expected_group_access():
    with TemporaryDirectory(prefix="gm-relay-", dir="/tmp") as directory:
        path = Path(directory) / "broker.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            path.chmod(0o600)
            info = path.lstat()
            assert permissions.normalize(path, info.st_uid, info.st_gid) is True
            assert stat.S_IMODE(path.stat().st_mode) == 0o660
            assert permissions.normalize(path, info.st_uid, info.st_gid) is False
            with pytest.raises(RuntimeError):
                permissions.normalize(path, info.st_uid + 1, info.st_gid)
            assert stat.S_IMODE(path.stat().st_mode) == 0o660
            path.chmod(0o666)
            with pytest.raises(RuntimeError):
                permissions.normalize(path, info.st_uid, info.st_gid)


def test_regular_file_and_symlink_are_untouched(tmp_path):
    original = tmp_path / "original.wav"
    original.write_bytes(b"original")
    original.chmod(0o600)
    link = tmp_path / "broker.sock"
    link.symlink_to(original)
    for path in (original, link):
        with pytest.raises(RuntimeError):
            permissions.normalize(path, os.getuid(), path.lstat().st_gid)
    assert original.read_bytes() == b"original"
    assert stat.S_IMODE(original.stat().st_mode) == 0o600
