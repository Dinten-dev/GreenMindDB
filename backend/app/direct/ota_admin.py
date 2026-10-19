"""Local administrator CLI; no network credentials and no public control API.

Run beside the root-controlled sidecar catalog. Every assignment requires an
exact device, source version and image. Nothing follows GitHub's latest alias.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

PROFILE = "biolingo-v22-n16r8-direct-ota-v1"
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def atomic(path, content):
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # Root controls writes; only the deployed service group can read.
        os.fchown(fd, -1, path.parent.stat().st_gid)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--image", required=True, type=Path)
    stage.add_argument("--version", required=True)
    stage.add_argument("--sha256", required=True)
    assign = commands.add_parser("assign")
    assign.add_argument("--device", required=True, type=uuid.UUID)
    assign.add_argument("--from-version", required=True)
    assign.add_argument("--sha256", required=True)
    assign.add_argument("--expires-hours", type=int, default=24, choices=range(1, 169))
    clear = commands.add_parser("clear")
    clear.add_argument("--device", required=True, type=uuid.UUID)
    args = parser.parse_args()
    args.root.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock = (args.root / ".admin.lock").open("a")
    os.chmod(args.root / ".admin.lock", 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    path = args.root / "policy.json"
    policy = json.loads(path.read_text()) if path.exists() else {"devices": {}, "releases": {}}
    history = policy.setdefault("commands", {})
    # Preserve pre-upgrade assignments before replacing/clearing their active map.
    for device, command in policy["devices"].items():
        history.setdefault(command["command_id"], {**command, "device_id": device})
    if args.action == "stage":
        data = args.image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if (
            digest != args.sha256
            or not IDENT.fullmatch(args.version)
            or not 1024 <= len(data) <= 0x300000
            or data[0] != 0xE9
            or int.from_bytes(data[12:14], "little") != 9
        ):
            raise ValueError("Wrong hash, chip, size or version")
        directory = args.root / "images"
        directory.mkdir(mode=0o750, exist_ok=True)
        image = directory / (digest + ".bin")
        if image.exists():
            if image.read_bytes() != data:
                raise ValueError("Immutable image differs")
        else:
            atomic(image, data)
        release = {
            "file": image.name,
            "version": args.version,
            "profile": PROFILE,
            "size": len(data),
            "sha256": digest,
        }
        if digest in policy["releases"] and policy["releases"][digest] != release:
            raise ValueError("Immutable release differs")
        policy["releases"][digest] = release
        result = release
    elif args.action == "assign":
        release = policy["releases"][args.sha256]
        if not IDENT.fullmatch(args.from_version) or args.from_version == release["version"]:
            raise ValueError("Exact, different source version required")
        result = {
            "command_id": str(uuid.uuid4()),
            "from_version": args.from_version,
            "release_id": args.sha256,
            "expires_at": time.time() + args.expires_hours * 3600,
        }
        policy["devices"][str(args.device)] = result
        policy.setdefault("commands", {})[result["command_id"]] = {
            **result,
            "device_id": str(args.device),
        }
    else:
        policy["devices"].pop(str(args.device), None)
        result = {"device": str(args.device), "update": "disabled"}
    content = (json.dumps(policy, indent=2) + "\n").encode()
    if len(content) > 1024 * 1024:
        raise ValueError("Catalog exceeds bounded policy capacity; retain audit history separately")
    atomic(path, content)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
