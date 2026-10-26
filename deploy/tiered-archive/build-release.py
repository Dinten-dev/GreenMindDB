"""Create a deterministic review bundle without private captures or credentials."""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def unpacked_bytes(wheel):
    with zipfile.ZipFile(wheel) as bundle:
        return sum(item.file_size for item in bundle.infolist())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from app.tiered_archive.policy import POLICY_SHA256
    from app.tiered_archive.runtime import runtime_sha256

    if subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("A committed, clean source tree is required")
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    revision = runtime_sha256()
    name = "GreenMind_Tiered_Archive_" + commit[:12]
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / name
    if destination.exists():
        raise FileExistsError("Existing immutable bundle retained")
    destination.mkdir()
    tracked = {
        os.fsdecode(name)
        for name in subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"]
        ).split(b"\0")
        if name
    }
    selected = []
    for directory in (
        "backend/app",
        "backend/alembic",
        "backend/tests",
        "deploy/tiered-archive",
    ):
        for path in (root / directory).rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.relative_to(root).as_posix() in tracked
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".db", ".sqlite3"}
            ):
                selected.append(path)
    frontend = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--", "frontend"]
    ).split(b"\0")
    for name in frontend:
        if name:
            path = root / os.fsdecode(name)
            if (
                path.is_file()
                and not path.is_symlink()
                and not path.name.startswith(".env")
            ):
                selected.append(path)
    for name in (
        "backend/requirements.lock",
        "backend/requirements.txt",
        "backend/requirements-dev.txt",
        "backend/requirements-archive.txt",
        "backend/requirements-archive.lock",
        "backend/pyproject.toml",
        "backend/pytest.ini",
        "backend/alembic.ini",
        "backend/Dockerfile",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/next.config.ts",
        "frontend/next.config.mjs",
        "frontend/jest.config.js",
        "frontend/jest.config.ts",
        "frontend/jest.setup.js",
        "frontend/jest.setup.ts",
        "frontend/tailwind.config.ts",
        "frontend/postcss.config.mjs",
    ):
        path = root / name
        if path.is_file():
            selected.append(path)
    files = {}
    for source in sorted(set(selected)):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    locked = {}
    for lock in (
        root / "backend/requirements.lock",
        root / "backend/requirements-archive.lock",
    ):
        lines = lock.read_text().replace("\\\n", " ").splitlines()
        for line in lines:
            match = re.match(r"([A-Za-z0-9_.-]+)==([^ \t]+)", line)
            if match:
                locked[match[1].lower().replace("_", "-")] = (
                    match[2],
                    set(re.findall(r"--hash=sha256:([a-f0-9]{64})", line)),
                )
    supplied = {}
    for wheel in sorted(args.wheelhouse.glob("*.whl")):
        name, version, *_ = wheel.name.split("-")
        name = name.lower().replace("_", "-")
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if (
            name not in locked
            or locked[name][0] != version
            or sha not in locked[name][1]
            or name in supplied
        ):
            raise ValueError("Wheel differs from target lock: " + wheel.name)
        supplied[name] = wheel
    if set(supplied) != set(locked):
        raise ValueError("Complete offline wheel closure required")
    for wheel in supplied.values():
        relative = Path("wheelhouse") / wheel.name
        target = destination / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(wheel, target)
        files[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "schema": 1,
        "runtime_sha256": revision,
        "policy_sha256": POLICY_SHA256,
        "base_revision": commit,
        "offline_target": "CPython 3.12 Linux x86_64 glibc>=2.28",
        "offline_wheels": len(supplied),
        "offline_unpacked_bytes": sum(
            unpacked_bytes(wheel) for wheel in supplied.values()
        ),
        "files": files,
        "production_deletion_enabled": False,
        "native_acceptance_complete": False,
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (destination / "release.json").write_bytes(raw)
    archive = args.output / (destination.name + ".tar.gz")
    with (
        archive.open("xb") as output,
        gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        for path in sorted(destination.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            info = tarfile.TarInfo(
                str(Path(destination.name) / path.relative_to(destination))
            )
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            bundle.addfile(info, io.BytesIO(data))
    result = {
        "runtime_sha256": revision,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive": str(archive.resolve()),
        "directory": str(destination.resolve()),
        "files": len(files),
    }
    (args.output / (destination.name + ".checksums.json")).write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    (args.output / (destination.name + ".sha256")).write_text(
        result["archive_sha256"] + "  " + archive.name + "\n"
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
