"""Validate root-transferred restore inputs and materialize an archive-local manifest."""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .ledger import _private_file_digest, _private_read
from .runtime import runtime_sha256


def materialize(
    *, source_manifest, source_sha256, operation_path, operation_sha256, files_dir, output
):
    if os.geteuid() != 0:
        raise ArchiveBlocked("Root-owned frozen restore materialization required")

    def root_file(path, expected):
        path = Path(path)
        for parent in path.parents:
            info = parent.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ArchiveBlocked("Frozen restore parent is untrusted")
        raw = _private_read(path, 1024 * 1024)
        info = path.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(info.st_mode) != 0o400
            or info.st_uid != 0
            or info.st_nlink != 1
            or not re.fullmatch(r"[a-f0-9]{64}", str(expected))
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise ArchiveBlocked("Frozen restore source binding differs")
        return json.loads(raw)

    operation = root_file(operation_path, operation_sha256)
    source = root_file(source_manifest, source_sha256)
    fields = {
        "schema",
        "pin_id",
        "snapshot_id",
        "operation_sha256",
        "local_only",
        "files",
        "target_sha256",
        "restore_plan_sha256",
        "local_runtime_sha256",
        "local_executor",
    }
    executors = {
        "raw-restore": "app.raw_archive.recovery.restore_bundle",
        "wav-catalog": "app.raw_archive.wav_catalog.restore_bundle",
    }
    if (
        type(operation) is not dict
        or type(source) is not dict
        or set(source) != fields
        or source["schema"] != 1
        or source["local_only"] is not True
        or operation.get("kind") != "restoreinput"
        or source["local_executor"] != executors.get(operation.get("consumer_entrypoint"), "")
        or source["pin_id"] != operation.get("pin_id")
        or source["snapshot_id"] != operation.get("snapshot_id")
        or source["operation_sha256"] != operation_sha256
        or source["target_sha256"] != operation.get("target_sha256")
        or source["restore_plan_sha256"] != operation.get("restore_plan_sha256")
        or type(source["files"]) is not list
        or not 1 <= len(source["files"]) <= 4096
    ):
        raise ArchiveBlocked("Frozen restore source manifest differs")
    inventory = root_file(operation["input_manifest_path"], operation["input_sha256"])
    if (
        type(inventory) is not dict
        or set(inventory) != {"schema", "files"}
        or inventory["schema"] != 1
        or type(inventory["files"]) is not list
    ):
        raise ArchiveBlocked("Frozen restore inventory differs")
    directory = Path(files_dir)
    if (
        not directory.is_absolute()
        or directory.is_symlink()
        or directory.resolve(strict=False) != directory
    ):
        raise ArchiveBlocked("Canonical transferred restore directory required")
    for parent in (directory, *directory.parents):
        info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ArchiveBlocked("Transferred restore directory is untrusted")
    local = []
    for index, item in enumerate(source["files"]):
        if (
            type(item) is not dict
            or set(item) != {"path", "sha256", "bytes"}
            or Path(item["path"]).name != f"input-{index:04d}.bin"
            or type(item["bytes"]) is not int
            or not 0 < item["bytes"] <= 64 * 1024**2
            or not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"]))
        ):
            raise ArchiveBlocked("Transferred restore input identity differs")
        path = directory / f"input-{index:04d}.bin"
        info = path.stat(follow_symlinks=False)
        if info.st_uid != 0 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o400:
            raise ArchiveBlocked("Transferred restore input is not sealed")
        if _private_file_digest(path, item["bytes"]) != item["sha256"]:
            raise ArchiveBlocked("Transferred restore input digest differs")
        local.append({"path": str(path), "sha256": item["sha256"], "bytes": item["bytes"]})
    if any(type(row) is not dict or set(row) != {"sha256", "bytes"} for row in inventory["files"]):
        raise ArchiveBlocked("Frozen restore inventory entries differ")
    if sorted((item["sha256"], item["bytes"]) for item in local) != sorted(
        (item["sha256"], item["bytes"]) for item in inventory["files"]
    ):
        raise ArchiveBlocked("Transferred restore inputs are incomplete")
    result = dict(source, files=local, local_runtime_sha256=runtime_sha256())
    path = Path(output)
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=False) != path:
        raise ArchiveBlocked("Canonical output path required")
    for parent in path.parents:
        info = parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ArchiveBlocked("Frozen manifest parent is untrusted")
    raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as body:
        os.fchmod(body.fileno(), 0o400)
        body.write(raw)
        body.flush()
        os.fsync(body.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source-manifest",
        "source-sha256",
        "operation-path",
        "operation-sha256",
        "files-dir",
        "output",
    ):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(materialize(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
