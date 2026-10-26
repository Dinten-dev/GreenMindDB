"""Verify the pinned compressed hash before inspecting or extracting a release."""

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as body:
        while chunk := body.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def extract(archive, expected, output):
    minimum = (3, 10)
    if sys.version_info[:2] < minimum:
        raise RuntimeError(
            "Bootstrap requires CPython 3.10 or newer, independently of the runtime venv"
        )
    if digest(archive) != expected:
        raise ValueError("Compressed package hash differs; nothing extracted")
    if output.exists() or output.is_symlink():
        raise FileExistsError("New extraction directory required")
    with tarfile.open(archive, "r:gz") as bundle:
        members, seen, total = [], set(), 0
        for member in bundle:
            name = Path(member.name)
            if (
                not member.isfile()
                or name.is_absolute()
                or ".." in name.parts
                or "\\" in member.name
                or len(name.parts) < 2
                or not 0 <= member.size <= 128 * 1024**2
                or name in seen
            ):
                raise ValueError("Unsafe archive member")
            if hasattr(tarfile, "data_filter"):
                try:
                    tarfile.data_filter(member, str(output))
                except tarfile.FilterError as error:
                    raise ValueError("Unsafe archive member") from error
            members.append(member)
            seen.add(name)
            total += member.size
            if len(members) > 2048 or total > 512 * 1024**2:
                raise ValueError("Release exceeds extraction budget")
        top = {Path(m.name).parts[0] for m in members}
        if len(top) != 1 or any(
            parent in seen for name in seen for parent in name.parents
        ):
            raise ValueError("One conflict-free release root required")
        output.mkdir(mode=0o700)
        for member in members:
            target = output / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("xb") as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
            target.chmod(0o600)
    return {
        "archive_sha256": expected,
        "release": str(output / next(iter(top))),
        "extracted_files": len(members),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.archive, args.sha256, args.output), sort_keys=True))
