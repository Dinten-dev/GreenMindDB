import hashlib
import json
import os
from pathlib import Path

import pytest

from app.raw_archive import snapshot_restore_launcher as launcher
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import snapshot_frozen_materialize as archive


def test_restore_launcher_seals_every_snapshot_input(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    for name, body in (("manifest.json", b"manifest"), ("part-000000.gz", b"part")):
        path = output / name
        path.write_bytes(body)
        path.chmod(0o600)
    inventory = {"schema": 1, "files": [
        {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for body in (b"manifest", b"part")
    ]}
    real_stat, real_fstat = Path.stat, os.fstat

    class RootStat:
        def __init__(self, info):
            self.info = info
            self.st_uid = 0
            self.st_mode = info.st_mode & ~0o022 if (info.st_mode & 0o170000) == 0o040000 else info.st_mode

        def __getattr__(self, name):
            return getattr(self.info, name)

    monkeypatch.setattr(Path, "stat", lambda path, *a, **k: RootStat(real_stat(path, *a, **k)))
    monkeypatch.setattr(os, "fstat", lambda fd: RootStat(real_fstat(fd)))
    frozen = tmp_path / "sealed"
    sealed = launcher.seal_restore_inputs(output, frozen, inventory, "wav-catalog")
    assert [Path(item["path"]).read_bytes() for item in sealed] == [b"manifest", b"part"]
    assert all((Path(item["path"]).stat().st_mode & 0o777) == 0o400 for item in sealed)
    assert (frozen.stat().st_mode & 0o777) == 0o500
    inventory["files"][1]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="preimport_snapshot_closed"):
        launcher.seal_restore_inputs(output, tmp_path / "bad", inventory, "wav-catalog")


def test_archive_materialization_rechecks_transferred_bytes_and_inventory(tmp_path, monkeypatch):
    def write(path, value):
        path.write_text(json.dumps(value, sort_keys=True))
        path.chmod(0o400)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    content = b"sealed restore input"
    digest = hashlib.sha256(content).hexdigest()
    transferred = tmp_path / "transferred"
    transferred.mkdir(mode=0o700)
    part = transferred / "input-0000.bin"
    part.write_bytes(content)
    part.chmod(0o400)
    inventory = tmp_path / "inventory.json"
    inventory_sha = write(inventory, {"schema": 1, "files": [{"sha256": digest, "bytes": len(content)}]})
    operation = {"kind": "restoreinput", "consumer_entrypoint": "wav-catalog", "pin_id": "a" * 32,
                 "snapshot_id": 42, "target_sha256": "b" * 64, "restore_plan_sha256": "c" * 64,
                 "input_manifest_path": str(inventory), "input_sha256": inventory_sha}
    operation_path = tmp_path / "operation.json"
    operation_sha = write(operation_path, operation)
    source = {"schema": 1, "pin_id": operation["pin_id"], "snapshot_id": 42,
              "operation_sha256": operation_sha, "local_only": True,
              "files": [{"path": "/source/sealed/input-0000.bin", "sha256": digest, "bytes": len(content)}],
              "target_sha256": operation["target_sha256"], "restore_plan_sha256": operation["restore_plan_sha256"],
              "local_runtime_sha256": "d" * 64, "local_executor": "app.raw_archive.wav_catalog.restore_bundle"}
    source_path = tmp_path / "source.json"
    source_sha = write(source_path, source)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(archive, "runtime_sha256", lambda: "e" * 64)
    real_stat = Path.stat

    class RootStat:
        def __init__(self, info):
            self.info, self.st_uid = info, 0
            self.st_mode = info.st_mode & ~0o022 if (info.st_mode & 0o170000) == 0o040000 else info.st_mode

        def __getattr__(self, name):
            return getattr(self.info, name)

    monkeypatch.setattr(Path, "stat", lambda path, *a, **k: RootStat(real_stat(path, *a, **k)))
    result = archive.materialize(source_manifest=source_path, source_sha256=source_sha,
        operation_path=operation_path, operation_sha256=operation_sha, files_dir=transferred,
        output=tmp_path / "frozen.json")
    manifest = json.loads(Path(result["path"]).read_bytes())
    assert manifest["files"][0]["path"] == str(part)
    assert manifest["local_runtime_sha256"] == "e" * 64
    part.chmod(0o600)
    part.write_bytes(b"X" * len(content))
    part.chmod(0o400)
    with pytest.raises(ArchiveBlocked, match="digest differs"):
        archive.materialize(source_manifest=source_path, source_sha256=source_sha,
            operation_path=operation_path, operation_sha256=operation_sha, files_dir=transferred,
            output=tmp_path / "bad.json")
