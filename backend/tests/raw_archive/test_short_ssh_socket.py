"""Control sockets stay private, bounded and independent of archive state paths."""

import os
import tempfile
from pathlib import Path

import pytest

from app.raw_archive import storage
from app.raw_archive.policy import ArchiveBlocked


@pytest.fixture
def transport(tmp_path, monkeypatch):
    key = tmp_path / "key"
    hosts = tmp_path / "hosts"
    key.touch()
    hosts.touch()
    box = storage.StorageBox(host="archive.example", user="archive", key=key, known_hosts=hosts)
    calls = []

    class Child:
        def __init__(self, command, **options):
            self.command, self.options = command, options
            self.returncode = None
            self.pid = 123456
            self.is_master = command[0] == "/usr/bin/ssh"
            calls.append(self)
            if self.is_master:
                Path(command[command.index("-S") + 1]).touch()

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.returncode = 0
            return 0

        def communicate(self, batch, timeout):
            self.returncode = 0
            return "", ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(storage.subprocess, "Popen", Child)
    monkeypatch.setattr(storage.os, "killpg", lambda pid, signal: None)
    yield box, calls
    box.close()


def test_real_long_state_path_reuses_private_short_socket_and_only_cleans_own_directory(
    transport, tmp_path
):
    box, calls = transport
    state = Path(
        "/var/lib/greenmind/archive-copy-20261010/"
        "production-copy-pilot-1a2c78ea7dc64c1285d6a03a3a6fc42c/"
        "cache/wav-copy-reference-0123456789abcdef"
    )
    assert len(os.fsencode(state / "ssh-12345678/ctl")) > 100
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserve")
    with box.session(state):
        first = box._transport_command()
        socket = Path(box._socket)
        directory = socket.parent
        master = box._master
        assert directory.parent == Path("/tmp")  # noqa: S108 - assert the short private parent.
        assert directory.stat().st_uid == os.getuid()
        assert directory.stat().st_mode & 0o777 == 0o700
        assert len(os.fsencode(socket)) <= 100
        assert calls[0].command[calls[0].command.index("-S") + 1] == str(socket)
        assert box._transport_command() == first
        assert box._master is master
        box.run("df greenmind-raw\n", output=tmp_path / "download", max_bytes=64)
        assert calls[-1].command[1:3] == ["-I", "-B"]
        assert calls[-1].command[3:5] == [str(Path(storage.__file__).resolve().with_name("sftp_limit.py")), "64"]
        assert "-oControlPath=" + str(socket) in calls[-1].command
        assert "-oProxyCommand=false" in calls[-1].command
        assert len([child for child in calls if child.is_master]) == 1
    assert master.returncode == 0
    assert not directory.exists()
    assert unrelated.read_bytes() == b"preserve"
    assert box._socket_dir is box._socket is box._session_root is None


def test_failed_master_cleans_only_its_new_private_directory(transport):
    box, calls = transport
    directory = None
    with pytest.raises(ArchiveBlocked, match="transport lost"):
        with box.session(Path("/not-created-or-modified/state")):
            box._transport_command()
            directory = Path(box._socket).parent
            calls[0].returncode = 1
            box._transport_command()
    assert directory is not None and not directory.exists()
    assert box._master is None


def test_overlong_socket_still_blocks_before_ssh_and_cleans_own_directory(transport, monkeypatch):
    box, calls = transport
    original = tempfile.TemporaryDirectory
    with original(prefix="gm-length-check-", dir="/tmp") as parent:  # noqa: S108 - private mkdtemp.
        long_parent = Path(parent) / ("x" * 90)
        long_parent.mkdir()

        def too_long(**options):
            return original(prefix=options["prefix"], dir=long_parent)

        monkeypatch.setattr(storage.tempfile, "TemporaryDirectory", too_long)
        with pytest.raises(ArchiveBlocked, match="socket path too long"):
            with box.session(Path("/unused-state")):
                box._transport_command()
        assert list(long_parent.iterdir()) == []
        assert calls == []


def test_short_socket_does_not_change_remote_paths_or_trust_options(transport):
    box, calls = transport
    key = "production/gateway/" + "a" * 64 + "/" + "b" * 64 + ".wav"
    remote = box.path(key)
    original = list(box.command)
    with box.session(Path("/long/state/" + "x" * 150)):
        command = box._transport_command()
        assert box.path(key) == remote == "greenmind-raw/" + key
        assert box.command == original
        assert "-oStrictHostKeyChecking=yes" in command
        assert "-oForwardAgent=no" in calls[0].command
        assert command[-1] == "archive@archive.example"
