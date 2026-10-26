"""Create a deterministic review bundle without private captures or credentials."""

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from app.tiered_archive.policy import POLICY_SHA256
    from app.tiered_archive.runtime import runtime_sha256

    revision = runtime_sha256()
    name = "GreenMind_Tiered_Archive_" + revision[:12]
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / name
    if destination.exists():
        raise FileExistsError("Existing immutable bundle retained")
    destination.mkdir()
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
    manifest = {
        "schema": 1,
        "runtime_sha256": revision,
        "policy_sha256": POLICY_SHA256,
        "base_revision": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "files": files,
        "production_deletion_enabled": False,
        "native_acceptance_complete": False,
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (destination / "release.json").write_bytes(raw)
    archive = args.output / (destination.name + ".tar.gz")
    with (
        archive.open("xb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
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
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
