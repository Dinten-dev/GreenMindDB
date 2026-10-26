"""Explicit operator Storage Box API access. No snapshot deletion or rollback methods."""

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, checksum
from .recovery import private_directory

API = "https://api.hetzner.com/v1"


def private_token(path):
    if not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Explicit private operator API token file required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as body:
            info = os.fstat(body.fileno())
            if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o077 or info.st_size > 4096:
                raise ArchiveBlocked("Operator token file must be private and bounded")
            token = body.read(4097).decode().strip()
    except (OSError, ValueError) as error:
        raise ArchiveBlocked("Operator token file is unavailable") from error
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ArchiveBlocked("Invalid operator API token")
    return token


class SnapshotAPI:
    def __init__(self, client, box_id, username, host):
        if type(box_id) is not int or box_id <= 0:
            raise ArchiveBlocked("Explicit Storage Box ID required")
        if not re.fullmatch(r"u[0-9]+", username) or host != username + ".your-storagebox.de":
            raise ArchiveBlocked("Expected primary Storage Box account required")
        self.client, self.box_id, self.username, self.host = client, box_id, username, host

    def request(self, method, suffix, payload=None):
        with self.client.stream(method, API + suffix, json=payload) as response:
            if response.status_code not in {200, 201}:
                raise ArchiveBlocked("Storage Box operator request refused")
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 1024**2:
                    raise ArchiveBlocked("Storage Box metadata exceeds its budget")
        return json.loads(raw)

    def validate_box(self):
        box = self.request("GET", f"/storage_boxes/{self.box_id}")["storage_box"]
        if (
            box.get("id") != self.box_id
            or box.get("username") != self.username
            or box.get("server") != self.host
        ):
            raise ArchiveBlocked("Operator token points to another Storage Box")
        if not box.get("access_settings", {}).get("zfs_enabled"):
            raise ArchiveBlocked("Snapshot directory visibility must be enabled by the operator")

    def create(self, description):
        self.validate_box()
        # Exactly one POST. Unknown network outcome is not retried automatically.
        return self.request(
            "POST",
            f"/storage_boxes/{self.box_id}/snapshots",
            {"description": description, "labels": {"purpose": "greenmind-delete-preflight"}},
        )

    def read(self, snapshot_id):
        if type(snapshot_id) is not int or snapshot_id <= 0:
            raise ArchiveBlocked("Explicit snapshot ID required")
        self.validate_box()
        snapshot = self.request("GET", f"/storage_boxes/{self.box_id}/snapshots/{snapshot_id}")[
            "snapshot"
        ]
        if snapshot.get("storage_box") != self.box_id or snapshot.get("id") != snapshot_id:
            raise ArchiveBlocked("Snapshot identity mismatch")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", snapshot.get("name", "")):
            raise ArchiveBlocked("Invalid snapshot name")
        return snapshot


def readback(destination, snapshot, reference, output):
    """Independent SFTP full catalog-manifest read; API success alone proves nothing."""
    private_directory(output)
    path = output / "snapshot-catalog-manifest.json"
    destination.download_catalog(
        reference["key"], path, reference["size"], snapshot=snapshot["name"]
    )
    if path.stat().st_size != reference["size"] or checksum(path) != reference["sha256"]:
        raise ArchiveBlocked("Snapshot catalog readback mismatch")
    return {
        "snapshot": snapshot,
        "catalog_manifest": reference,
        "readback_verified": True,
        "deleted_files": 0,
        "full_restore_required": True,
    }


def main():
    import httpx

    from .runner import destination_from_environment, health_probe

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--box-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--snapshot-id", type=int)
    parser.add_argument("--catalog-reference", type=Path)
    args = parser.parse_args()
    probe = health_probe()
    try:
        if not probe():
            raise ArchiveBlocked("Operator snapshot check paused on receiver headroom")
        private_directory(args.output)
        with httpx.Client(
            headers={"Authorization": "Bearer " + private_token(args.token_file)},
            timeout=httpx.Timeout(8, connect=3),
            trust_env=False,
        ) as client:
            api = SnapshotAPI(
                client,
                args.box_id,
                os.environ["RAW_ARCHIVE_SFTP_USER"],
                os.environ["RAW_ARCHIVE_SFTP_HOST"],
            )
            if args.create:
                result = {
                    "created_at": datetime.now(UTC).isoformat(),
                    "api_response": api.create("GreenMind reviewed archive acceptance"),
                    "readback_verified": False,
                    "deleted_files": 0,
                }
            else:
                if args.catalog_reference is None:
                    parser.error("Snapshot readback requires --catalog-reference")
                snapshot = api.read(args.snapshot_id)
                result = readback(
                    destination_from_environment(),
                    snapshot,
                    json.loads(args.catalog_reference.read_text()),
                    args.output,
                )
        raw = json.dumps(result, sort_keys=True).encode()
        path = args.output / (hashlib.sha256(raw).hexdigest() + ".json")
        with path.open("xb") as body:
            os.chmod(path, 0o600)
            body.write(raw)
        print(
            json.dumps(
                {
                    "report": path.name,
                    "readback_verified": result["readback_verified"],
                    "deleted_files": 0,
                }
            )
        )
    finally:
        probe.close()


if __name__ == "__main__":
    main()
