"""Exercise the complete preparation without root, service changes or real keys."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    script = (
        Path(__file__).resolve().parents[3]
        / "deploy/raw-archive/acceptance/prepare-readonly-broker.py"
    )
    spec = importlib.util.spec_from_file_location("broker_preparation", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    revision = "a" * 12
    package = tmp_path / "package"
    package.mkdir()
    (package / "bundle.json").write_text(json.dumps({"revision": "a" * 40}))
    state = tmp_path / "state"
    (state / revision).mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for name in ("id_ed25519", "known_hosts"):
        (readonly / name).write_text("isolated fixture")
        (readonly / name).chmod(0o640)
    (readonly / "broker.env").write_text(
        "".join(
            "RAW_ARCHIVE_READ_ONLY_SFTP_" + key + "=" + str(value) + "\n"
            for key, value in {
                "USER": "u676312-sub1",
                "HOST": "u676312-sub1.your-storagebox.de",
                "KEY": readonly / "id_ed25519",
                "KNOWN_HOSTS": readonly / "known_hosts",
            }.items()
        )
    )
    (live / "broker.env").write_text(
        "RAW_ARCHIVE_STATE_DIR=/var/lib/greenmind-raw-copy\nRAW_ARCHIVE_ENVIRONMENT=production\nRAW_ARCHIVE_READ_BROKER_SOCKET=/run/greenmind-archive-read/read.sock\nRAW_ARCHIVE_READ_SCRATCH_DIR="
        + str(scratch)
        + "\nRAW_ARCHIVE_SFTP_KEY=/private/operator-only\n"
    )
    monkeypatch.setattr(module, "PACKAGE", package)
    monkeypatch.setattr(module, "LIVE", live)
    monkeypatch.setattr(module, "READONLY", readonly)

    def mapped(value):
        return {
            "/opt/greenmind/archive-acceptance": tmp_path / "runtime",
            "/mnt/HC_Volume_106755700/greenmind-archive-acceptance": state,
        }.get(str(value), Path(value))

    monkeypatch.setattr(module, "Path", mapped)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *_args: None)
    monkeypatch.setattr(
        module.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=996, pw_gid=986)
    )
    original = Path.stat

    def owned(path, *args, **kwargs):
        values = list(original(path, *args, **kwargs))
        values[4] = 0
        values[5] = 986
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", owned)
    return module, state / revision / "readonly-broker"


def test_prepared_paths_and_repeat_exclude_upload_access(preparation):
    module, target = preparation
    module.main()
    manifest = json.loads((target / "manifest.json").read_text())
    assert Path(manifest["override"]).name == "zz-readonly-" + ("a" * 12) + ".conf"
    assert not manifest["activated"] and not manifest["upload_credentials_present"]
    env = (target / "broker.env").read_text()
    assert "RAW_ARCHIVE_SFTP_KEY=" not in env
    assert "RAW_ARCHIVE_DELETE_ENABLED=false" in env
    assert "RAW_ARCHIVE_READ_ONLY_SFTP_USER=u676312-sub1" in env
    saved = (target / "manifest.json").read_bytes()
    module.main()
    assert (target / "manifest.json").read_bytes() == saved


def test_changed_preparation_is_not_overwritten(preparation):
    module, target = preparation
    module.main()
    (target / "broker.env").write_text("other operator configuration")
    with pytest.raises(AssertionError):
        module.main()
    assert (target / "broker.env").read_text() == "other operator configuration"


def test_unknown_evidence_is_retained_and_blocks_resume(preparation):
    module, target = preparation
    module.main()
    (target / "unexpected").write_text("retain")
    with pytest.raises(AssertionError):
        module.main()
    assert (target / "unexpected").read_text() == "retain"
