import hashlib
import os
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import parent_snapshot_source as source


@pytest.fixture
def root_profile(tmp_path, monkeypatch):
    path = tmp_path / "reader.env"
    raw = b"synthetic-profile\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    original_lstat = Path.lstat

    class Owned:
        def __init__(self, actual):
            self.actual = actual

        def __getattr__(self, name):
            return 0 if name == "st_uid" else getattr(self.actual, name)

    class RootOS:
        def __getattr__(self, name):
            return getattr(os, name)

        def fstat(self, descriptor):
            return Owned(os.fstat(descriptor))

    def lstat(current, *args, **kwargs):
        actual = original_lstat(current, *args, **kwargs)
        return Owned(actual) if current == path or current in path.parents else actual

    monkeypatch.setattr(source, "os", RootOS())
    monkeypatch.setattr(Path, "lstat", lstat)
    return path, raw, hashlib.sha256(raw).hexdigest()


def test_existing_reader_stays_0600(root_profile):
    path, raw, digest = root_profile
    assert source._reader_profile_bytes(path, digest) == raw
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o666])
def test_other_profile_permissions_rejected(root_profile, mode):
    path, _, digest = root_profile
    path.chmod(mode)
    with pytest.raises(ArchiveBlocked):
        source._reader_profile_bytes(path, digest)


def test_profile_content_pin_required(root_profile):
    path, _, _ = root_profile
    with pytest.raises(ArchiveBlocked):
        source._reader_profile_bytes(path, "0" * 64)


def test_profile_hardlink_rejected(root_profile):
    path, _, digest = root_profile
    os.link(path, path.with_name("linked"))
    with pytest.raises(ArchiveBlocked):
        source._reader_profile_bytes(path, digest)


def test_replacement_with_same_content_rejected(root_profile, monkeypatch):
    path, raw, digest = root_profile
    replacement = path.with_name("replacement")
    replacement.write_bytes(raw)
    replacement.chmod(0o600)
    actual_fdopen = os.fdopen

    class ReplacedAfterRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, bound):
            value = self.stream.read(bound)
            os.replace(replacement, path)
            return value

    monkeypatch.setattr(
        source.os, "fdopen", lambda fd, mode: ReplacedAfterRead(actual_fdopen(fd, mode))
    )
    with pytest.raises(ArchiveBlocked):
        source._reader_profile_bytes(path, digest)
