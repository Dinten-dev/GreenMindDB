"""Configuration retention across volumes and concurrent-update rejection."""

import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[3] / "deploy/raw-archive/acceptance/config_files.py"
    spec = importlib.util.spec_from_file_location("config_files", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_rename_and_link_stay_on_configuration_filesystem(tmp_path, monkeypatch):
    functions = module()
    configs = tmp_path / "etc"
    configs.mkdir()
    evidence = tmp_path / "separate-volume"
    evidence.mkdir()
    old = configs / "job.conf"
    old.write_bytes(b"old config")
    sha = functions.digest(old)
    rename = Path.rename
    links = functions.os.link

    def same_volume(source, target):
        assert source.parent == target.parent
        return rename(source, target)

    def link(source, target):
        assert source.parent == target.parent
        return links(source, target)

    monkeypatch.setattr(Path, "rename", same_volume)
    monkeypatch.setattr(functions.os, "link", link)
    inactive = functions.withdraw(old, evidence / "previous.conf", sha)
    assert not old.exists() and inactive.read_bytes() == b"old config"
    assert (evidence / "previous.conf").read_bytes() == b"old config"
    candidate = evidence / "next.conf"
    candidate.write_bytes(b"new config")
    functions.publish_new(candidate, old, functions.digest(candidate))
    assert old.read_bytes() == b"new config" and candidate.exists()
    functions.withdraw(old, evidence / "reverted.conf", functions.digest(old))
    inactive.rename(old)
    assert old.read_bytes() == b"old config"


def test_concurrent_configuration_and_bad_hash_are_preserved(tmp_path):
    functions = module()
    source = tmp_path / "candidate"
    source.write_bytes(b"candidate")
    target = tmp_path / "active.conf"
    target.write_bytes(b"other operator")
    with pytest.raises(FileExistsError):
        functions.publish_new(source, target, functions.digest(source))
    assert target.read_bytes() == b"other operator"
    with pytest.raises(AssertionError):
        functions.withdraw(target, tmp_path / "evidence", functions.digest(source))
    assert target.read_bytes() == b"other operator" and not (tmp_path / "evidence").exists()
