"""Freeze NEW private restore copies; never chmod, rewrite or remove originals."""

import hashlib
import json
import os
import stat

from app.raw_archive.policy import ArchiveBlocked

ROOT_UID = 0
MAX_JSON = 1024**2


def require(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def protected_parents(path):
    for parent in path.parents:
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and not parent.is_symlink() and info.st_uid in {0, ROOT_UID},
            "Restore input parent is not operator-owned",
        )
        writable = stat.S_IMODE(info.st_mode) & 0o022
        # A root-owned sticky temporary directory cannot rename another UID's
        # protected child. All other ancestor directories must be non-writable.
        require(
            not writable or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)),
            "Restore input parent permits replacement",
        )


def frozen(path):
    require(path.is_absolute(), "Absolute frozen restore input required")
    protected_parents(path)
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == ROOT_UID
        and stat.S_IMODE(info.st_mode) == 0o400
        and info.st_nlink == 1,
        "Audit input must be a private operator-owned 0400 copy",
    )
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def freeze_copy(source, destination, expected_sha256, expected_bytes, checkpoint):
    require(
        not destination.exists() and not destination.is_symlink(), "Freeze never replaces an input"
    )
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(source_fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size == expected_bytes,
            "Freeze source size or type mismatch",
        )
        target_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
        )
        value = hashlib.sha256()
        count = 0
        try:
            with os.fdopen(target_fd, "wb") as target:
                while chunk := os.read(source_fd, 256 * 1024):
                    checkpoint()
                    count += len(chunk)
                    require(count <= expected_bytes, "Freeze source grew")
                    value.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(source_fd)
            require(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and count == expected_bytes
                and value.hexdigest() == expected_sha256,
                "Freeze source identity or checksum changed",
            )
            frozen(destination)
        except BaseException:
            # Leave only the new incomplete copy and intent for inspection.
            raise
    finally:
        os.close(source_fd)


def freeze_restore_page(folder, output, manifest_sha256, *, checkpoint):
    """Root-only helper for an entirely NEW SQL restore-page directory."""
    require(os.geteuid() == ROOT_UID, "Only the trusted operator may freeze private inputs")
    require(
        output.is_absolute() and not output.exists() and not output.is_symlink(),
        "Freeze requires a new isolated output",
    )
    protected_parents(output)
    manifest_path = folder / "manifest.json"
    with manifest_path.open("rb") as source:
        raw = source.read(MAX_JSON + 1)
    require(
        0 < len(raw) <= MAX_JSON and hashlib.sha256(raw).hexdigest() == manifest_sha256,
        "Pinned freeze manifest required",
    )
    manifest = json.loads(raw)
    parts = manifest["files"]
    require(
        isinstance(parts, list)
        and 1 <= len(parts) <= 128
        and all(part["format"] == "parquet" and 0 < part["bytes"] <= 64 * 1024**2 for part in parts)
        and sum(part["bytes"] for part in parts) <= 64 * 1024**2,
        "Bounded SQL parts required for private freeze",
    )
    intent = output.parent / (output.name + ".freeze-intent.json")
    with intent.open("x") as journal:
        os.chmod(intent, 0o600)
        journal.write(
            json.dumps(
                {"manifest_sha256": manifest_sha256, "files": len(parts), "phase": "intent"},
                sort_keys=True,
            )
        )
        journal.flush()
        os.fsync(journal.fileno())
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    output.mkdir(mode=0o700)
    for number, part in enumerate(parts):
        freeze_copy(
            folder / f"{number:04d}.parquet",
            output / f"{number:04d}.parquet",
            part["sha256"],
            part["bytes"],
            checkpoint,
        )
    freeze_copy(manifest_path, output / "manifest.json", manifest_sha256, len(raw), checkpoint)
    os.chmod(output, 0o500)
    directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "manifest_sha256": manifest_sha256,
        "copied_parts": len(parts),
        "originals_changed": False,
        "private_mode": "0400",
    }


def freeze_restore_descriptor(source, output, expected_sha256, *, checkpoint):
    """Copy a new bounded descriptor privately; leave its source bytes unchanged."""
    require(
        os.geteuid() == ROOT_UID and 0 < source.stat().st_size <= MAX_JSON,
        "Trusted operator and bounded descriptor required",
    )
    require(output.is_absolute() and not output.exists(), "Descriptor freeze requires a new path")
    protected_parents(output)
    intent = output.parent / (output.name + ".freeze-intent.json")
    with intent.open("x") as journal:
        os.chmod(intent, 0o600)
        journal.write(json.dumps({"sha256": expected_sha256, "phase": "intent"}, sort_keys=True))
        journal.flush()
        os.fsync(journal.fileno())
    directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
        freeze_copy(source, output, expected_sha256, source.stat().st_size, checkpoint)
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"sha256": expected_sha256, "private_mode": "0400", "originals_changed": False}
