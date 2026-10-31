import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive import observation as o
from app.raw_archive.policy import ArchiveBlocked


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fake_container(tmp_path):
    root = tmp_path / "mounted"
    root.mkdir()
    (root / "__init__.py").write_text("VERSION=1\n")
    (root / "ignore.bin").write_bytes(b"not program or credential input")
    (root / "nested").mkdir()
    (root / "nested" / "queries.sql").write_text("SELECT 1;\n")
    mount = {
        "Type": "bind",
        "Source": str(root),
        "Destination": "/app/app/direct",
        "RW": False,
    }
    value = {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "Config": {"Env": ["EXAMPLE_PRIVATE_VALUE=never-export"]},
        "HostConfig": {"Memory": 192 * 1024**2},
        "Mounts": [mount],
        "State": {
            "Running": True,
            "Restarting": False,
            "OOMKilled": False,
            "Pid": 123,
            "StartedAt": "2026-10-31T00:00:00Z",
        },
        "RestartCount": 0,
    }
    config = {k: value[k] for k in ("Image", "Config", "HostConfig")}
    config["Mounts"] = value["Mounts"]
    target = {
        "id": "a" * 64,
        "configuration_sha256": sha(json.dumps(config, sort_keys=True).encode()),
        "mounted_program_sha256": {
            "/app/app/direct/__init__.py": sha((root / "__init__.py").read_bytes()),
            "/app/app/direct/nested/queries.sql": sha(
                (root / "nested" / "queries.sql").read_bytes()
            ),
        },
    }
    return value, target, root


def scope_file(tmp_path, monkeypatch):
    scope = {
        "schema": "greenmind.production-receiver-observation.v1",
        "environment": "production",
        "machine_sha256": sha(Path("/etc/machine-id").read_bytes())
        if Path("/etc/machine-id").exists()
        else sha(b"test-machine"),
        "receivers": {
            k: {
                "id": c * 64,
                "configuration_sha256": "d" * 64,
                "mounted_program_sha256": {},
            }
            for k, c in [("gateway", "a"), ("direct", "b"), ("legacy_gateway", "c")]
        },
    }
    # Mac has no /etc/machine-id; intercept only that single read.
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda p: b"test-machine" if str(p) == "/etc/machine-id" else original(p),
    )
    scope["machine_sha256"] = sha(b"test-machine")
    path = tmp_path / "scope.json"

    def publish():
        raw = json.dumps(scope, sort_keys=True).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        monkeypatch.setenv("RAW_ARCHIVE_OBSERVATION_SCOPE_FILE", str(path))
        monkeypatch.setenv("RAW_ARCHIVE_OBSERVATION_SCOPE_SHA256", sha(raw))

    publish()
    return scope, path, publish


def test_complete_current_scope_binds_existing_three_roles(tmp_path, monkeypatch):
    scope, _, _ = scope_file(tmp_path, monkeypatch)
    assert o.scoped_receivers() == scope


@pytest.mark.parametrize("change", ["missing_direct", "duplicate_id", "staging", "other_machine"])
def test_scope_cannot_change_roles_environment_or_host(tmp_path, monkeypatch, change):
    scope, _, publish = scope_file(tmp_path, monkeypatch)
    if change == "missing_direct":
        scope["receivers"].pop("direct")
    elif change == "duplicate_id":
        scope["receivers"]["direct"]["id"] = scope["receivers"]["gateway"]["id"]
    elif change == "staging":
        scope["environment"] = "staging"
    else:
        scope["machine_sha256"] = "e" * 64
    publish()
    with pytest.raises(ArchiveBlocked):
        o.scoped_receivers()


def test_scope_hash_permissions_and_symlink_cannot_bypass_pin(tmp_path, monkeypatch):
    _, path, publish = scope_file(tmp_path, monkeypatch)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ArchiveBlocked):
        o.scoped_receivers()
    publish()
    path.chmod(0o644)
    with pytest.raises(ArchiveBlocked):
        o.scoped_receivers()
    publish()
    link = tmp_path / "link"
    link.symlink_to(path)
    monkeypatch.setenv("RAW_ARCHIVE_OBSERVATION_SCOPE_FILE", str(link))
    with pytest.raises(OSError):
        o.scoped_receivers()


def test_mount_directory_program_change_blocks_without_inspect_change(tmp_path, monkeypatch):
    value, target, root = fake_container(tmp_path)
    monkeypatch.setattr(
        o.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr=b"", stdout=json.dumps([value]).encode()
        ),
    )
    before = o.scoped_container(target)
    assert before["mounted_program_sha256"] == target["mounted_program_sha256"]
    assert "never-export" not in json.dumps(before)
    (root / "__init__.py").write_text("VERSION=2\n")
    with pytest.raises(ArchiveBlocked, match="program bytes changed"):
        o.scoped_container(target)


def test_wrong_generation_restart_or_configuration_blocks(tmp_path, monkeypatch):
    value, target, _ = fake_container(tmp_path)
    live = [copy.deepcopy(value)]
    monkeypatch.setattr(
        o.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr=b"", stdout=json.dumps(live).encode()),
    )
    for key, new in [("Id", "e" * 64), ("Config", {"Env": ["changed"]})]:
        live[0] = copy.deepcopy(value)
        live[0][key] = new
        with pytest.raises(ArchiveBlocked):
            o.scoped_container(target)
    for key, new in [
        ("Running", False),
        ("Restarting", True),
        ("OOMKilled", True),
        ("Pid", 0),
    ]:
        live[0] = copy.deepcopy(value)
        live[0]["State"][key] = new
        with pytest.raises(ArchiveBlocked):
            o.scoped_container(target)


def test_symlink_program_and_new_unpinned_program_fail(tmp_path, monkeypatch):
    value, target, root = fake_container(tmp_path)
    monkeypatch.setattr(
        o.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr=b"", stdout=json.dumps([value]).encode()
        ),
    )
    (root / "extra.py").write_text("CHANGED=1\n")
    with pytest.raises(ArchiveBlocked):
        o.scoped_container(target)
    (root / "extra.py").unlink()
    (root / "extra.py").symlink_to(root / "__init__.py")
    with pytest.raises(ArchiveBlocked):
        o.scoped_container(target)


@pytest.mark.parametrize("destination", ["/app/app", "/app"])
def test_root_and_covering_mounts_cannot_hide_program_changes(tmp_path, monkeypatch, destination):
    value, target, root = fake_container(tmp_path)
    if destination == "/app":
        new_root = tmp_path / "parent_mount"
        new_root.mkdir()
        root.rename(new_root / "app")
        root = new_root / "app"
        value["Mounts"][0]["Source"] = str(new_root)
    value["Mounts"][0]["Destination"] = destination
    config = {k: value[k] for k in ("Image", "Config", "HostConfig", "Mounts")}
    target["configuration_sha256"] = sha(json.dumps(config, sort_keys=True).encode())
    target["mounted_program_sha256"] = {
        k.replace("/app/app/direct/", "/app/app/"): v
        for k, v in target["mounted_program_sha256"].items()
    }
    monkeypatch.setattr(
        o.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr=b"", stdout=json.dumps([value]).encode()
        ),
    )
    assert o.scoped_container(target)["mounted_program_sha256"] == target["mounted_program_sha256"]
    (root / "__init__.py").write_text("VERSION=2\n")
    with pytest.raises(ArchiveBlocked):
        o.scoped_container(target)


def test_directory_alias_cannot_hide_changed_import_path(tmp_path, monkeypatch):
    value, target, root = fake_container(tmp_path)
    monkeypatch.setattr(
        o.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr=b"", stdout=json.dumps([value]).encode()
        ),
    )
    (root / "alias").symlink_to(root / "nested", target_is_directory=True)
    with pytest.raises(ArchiveBlocked, match="symlinks"):
        o.scoped_container(target)


def test_no_scope_keeps_existing_default_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("RAW_ARCHIVE_OBSERVATION_SCOPE_FILE", raising=False)
    monkeypatch.delenv("RAW_ARCHIVE_OBSERVATION_SCOPE_SHA256", raising=False)
    assert o.scoped_receivers() is None
    monkeypatch.setenv("RAW_ARCHIVE_OBSERVATION_SCOPE_SHA256", "a" * 64)
    with pytest.raises(ArchiveBlocked):
        o.scoped_receivers()
