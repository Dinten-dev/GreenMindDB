"""Explicit operator Storage Box API access and journal-guarded managed rotation."""

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


def managed_catalog_labels(reference):
    if type(reference) is not dict or set(reference) != {"key", "sha256", "bytes"}:
        raise ArchiveBlocked("Managed snapshot recovery catalogue reference required")
    sha, size = reference["sha256"], reference["bytes"]
    match = re.fullmatch(
        r"(production|staging)/tiered/00000000-0000-0000-0000-000000000000/"
        r"archive_catalog/([0-9]{4}-[0-9]{2}-[0-9]{2})/([a-f0-9]{64})\.json",
        reference["key"] if type(reference["key"]) is str else "",
    )
    if (
        not match
        or type(sha) is not str
        or sha != match[3]
        or type(size) is not int
        or not 0 < size <= 1024 * 1024
    ):
        raise ArchiveBlocked("Invalid managed snapshot recovery catalogue reference")
    return {
        "catalog-env": match[1],
        "catalog-day": match[2],
        "catalog-a": sha[:32],
        "catalog-b": sha[32:],
        "catalog-size": str(size),
    }


def managed_catalog_reference(snapshot):
    labels = snapshot.get("labels", {}) if type(snapshot) is dict else {}
    if type(labels) is not dict:
        raise ArchiveBlocked("Managed snapshot recovery labels are missing")
    sha = labels.get("catalog-a", "") + labels.get("catalog-b", "")
    try:
        size = int(labels.get("catalog-size", ""))
    except (TypeError, ValueError):
        raise ArchiveBlocked("Managed snapshot recovery size is invalid") from None
    reference = {
        "key": f"{labels.get('catalog-env')}/tiered/00000000-0000-0000-0000-000000000000/"
        f"archive_catalog/{labels.get('catalog-day')}/{sha}.json",
        "sha256": sha,
        "bytes": size,
    }
    if any(labels.get(key) != value for key, value in managed_catalog_labels(reference).items()):
        raise ArchiveBlocked("Managed snapshot recovery labels differ")
    return reference


def managed_snapshot_plan(snapshots, records, *, box_id, capacity, now):
    """Compute a conservative plan; this function never creates or deletes anything.

    Records must come from a durable, trusted creation and full-readback journal.
    Provider labels alone are not ownership or readback evidence. This helper is
    deliberately not wired to the scheduler until that journal and verifier exist.
    """
    if (
        type(box_id) is not int
        or box_id <= 0
        or type(capacity) is not int
        or capacity < 3
        or type(snapshots) is not list
        or type(records) is not list
        or len(snapshots) > capacity
        or len(records) > 3
        or not isinstance(now, datetime)
        or now.utcoffset() is None
    ):
        raise ArchiveBlocked("Snapshot capacity or inventory is unknown")

    def identity(row):
        if type(row) is not dict or type(row.get("id")) is not int or row["id"] <= 0:
            raise ArchiveBlocked("Snapshot identity is unknown")
        if row.get("storage_box") != box_id or type(row.get("storage_box")) is not int:
            raise ArchiveBlocked("Snapshot belongs to another Storage Box")
        if type(row.get("name")) is not str or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", row["name"]
        ):
            raise ArchiveBlocked("Snapshot name is unknown")
        try:
            created = datetime.fromisoformat(row["created"].replace("Z", "+00:00"))
            if created.utcoffset() is None or created > now:
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Snapshot creation time is unknown") from None
        return row["id"], row["storage_box"], row["name"], created

    provider = {}
    for snapshot in snapshots:
        key = identity(snapshot)
        if key[0] in provider or any(item[2] == key[2] for item in provider.values()):
            raise ArchiveBlocked("Duplicate provider snapshot identity")
        provider[key[0]] = key

    owned = []
    for record in records:
        if type(record) is not dict or set(record) != {"snapshot", "proof"}:
            raise ArchiveBlocked("Managed snapshot journal is incomplete")
        key = identity(record["snapshot"])
        if provider.get(key[0]) != key or any(item[0] == key[0] for item in owned):
            raise ArchiveBlocked("Managed snapshot ownership or provider identity differs")
        proof = record["proof"]
        if (
            type(proof) is not dict
            or set(proof)
            not in (
                {"snapshot_id", "inventory_sha256", "objects", "bytes", "checked_at"},
                {
                    "snapshot_id",
                    "inventory_sha256",
                    "objects",
                    "bytes",
                    "checked_at",
                    "journal_reference",
                },
            )
            or type(proof["snapshot_id"]) is not int
            or proof["snapshot_id"] != key[0]
            or type(proof["inventory_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", proof["inventory_sha256"])
            or type(proof["objects"]) is not int
            or proof["objects"] <= 0
            or type(proof["bytes"]) is not int
            or proof["bytes"] <= 0
        ):
            raise ArchiveBlocked("Complete independent snapshot readback evidence required")
        if "journal_reference" in proof:
            managed_catalog_labels(proof["journal_reference"])
        try:
            checked = datetime.fromisoformat(proof["checked_at"].replace("Z", "+00:00"))
            if checked.utcoffset() is None or not key[3] <= checked <= now:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Snapshot readback time is invalid") from None
        owned.append(key)

    if len(owned) < 3:
        if capacity - len(snapshots) < 3 - len(owned):
            raise ArchiveBlocked("Three managed snapshot positions are not available")
        return {"action": "create", "snapshot_id": None}
    oldest = min(owned, key=lambda item: (item[3], item[0]))
    return {"action": "rotate", "snapshot_id": oldest[0]}


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
        return box

    def inventory(self):
        """Return a complete manual-snapshot inventory and its provider limit."""
        box = self.validate_box()
        limit = box.get("storage_box_type", {}).get("snapshot_limit")
        if type(limit) is not int or not 3 <= limit <= 100:
            raise ArchiveBlocked("Provider manual snapshot capacity is unknown")
        document = self.request("GET", f"/storage_boxes/{self.box_id}/snapshots")
        rows = document.get("snapshots")
        pagination = document.get("meta", {}).get("pagination", {})
        if (
            type(rows) is not list
            or len(rows) > 100
            or type(pagination) is not dict
            or pagination.get("next_page") not in (None, 0)
        ):
            raise ArchiveBlocked("Complete provider snapshot inventory required")
        if any(type(row) is not dict or type(row.get("is_automatic")) is not bool for row in rows):
            raise ArchiveBlocked("Provider snapshot type is unknown")
        manual = [self.read(row.get("id")) for row in rows if not row["is_automatic"]]
        if len(manual) > limit or len({row["id"] for row in manual}) != len(manual):
            raise ArchiveBlocked("Provider snapshot inventory differs from capacity")
        return manual, limit

    def create_managed(self, marker, journal_reference):
        if not re.fullmatch(r"[a-f0-9]{32}", marker):
            raise ArchiveBlocked("Unique managed snapshot intent required")
        self.validate_box()
        labels = {
            "purpose": "greenmind-managed",
            "intent": marker,
            **managed_catalog_labels(journal_reference),
        }
        return self.request(
            "POST",
            f"/storage_boxes/{self.box_id}/snapshots",
            {
                "description": "greenmind-managed-" + marker,
                "labels": labels,
            },
        )

    def delete_managed(self, snapshot_id, *, ledger, intent):
        if (
            type(snapshot_id) is not int
            or snapshot_id <= 0
            or type(intent) is not str
            or not re.fullmatch(r"[a-f0-9]{32}", intent)
        ):
            raise ArchiveBlocked("Explicit managed snapshot ID required")
        row = ledger.db.execute(
            "SELECT state,snapshot,proof FROM managed_snapshot WHERE intent=?", (intent,)
        ).fetchone()
        if not row or row[0] != "delete_intent" or not row[1] or not row[2]:
            raise ArchiveBlocked("Durable managed snapshot delete intent required")
        recorded = json.loads(row[1])
        proof = json.loads(row[2])
        if recorded.get("id") != snapshot_id or proof.get("snapshot_id") != snapshot_id:
            raise ArchiveBlocked("Managed snapshot journal identity differs")
        if (
            ledger.db.execute(
                "SELECT count(*) FROM managed_snapshot WHERE state='verified' AND proof IS NOT NULL"
            ).fetchone()[0]
            != 2
        ):
            raise ArchiveBlocked("Two verified managed snapshots must remain")
        self.validate_box()
        live = self.read(snapshot_id)
        if (
            any(
                live.get(key) != recorded.get(key)
                for key in ("id", "storage_box", "name", "created")
            )
            or live.get("description") != "greenmind-managed-" + intent
            or live.get("labels", {}).get("purpose") != "greenmind-managed"
            or live.get("labels", {}).get("intent") != intent
        ):
            raise ArchiveBlocked("Managed snapshot ownership differs before deletion")
        # The caller journals ownership and delete intent before this one request.
        with self.client.stream(
            "DELETE", API + f"/storage_boxes/{self.box_id}/snapshots/{snapshot_id}"
        ) as response:
            if response.status_code not in {200, 202, 204}:
                raise ArchiveBlocked("Managed snapshot delete was not acknowledged")
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 1024**2:
                    raise ArchiveBlocked("Managed snapshot action exceeds its budget")
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise ArchiveBlocked("Managed snapshot deletion action is unknown") from None

    def require_action(self, action_id, snapshot_id, operation):
        if (
            type(snapshot_id) is not int
            or snapshot_id <= 0
            or operation not in {"create", "delete"}
        ):
            raise ArchiveBlocked("Provider snapshot action identity is unknown")
        if action_id is None and operation == "create":
            # No second POST: reconcile only a complete bounded action listing.
            snapshot = self.read(snapshot_id)
            try:
                created = datetime.fromisoformat(snapshot["created"].replace("Z", "+00:00"))
                if created.utcoffset() is None:
                    raise ValueError
            except (KeyError, AttributeError, TypeError, ValueError):
                raise ArchiveBlocked("Provider snapshot time is unknown") from None
            matches = []
            page = 1
            for _ in range(10):
                data = self.request(
                    "GET", f"/storage_boxes/{self.box_id}/actions?per_page=100&page={page}"
                )
                rows = data.get("actions")
                pagination = data.get("meta", {}).get("pagination")
                if (
                    type(rows) is not list
                    or len(rows) > 100
                    or type(pagination) is not dict
                    or "next_page" not in pagination
                ):
                    raise ArchiveBlocked("Complete provider action inventory required")
                for item in rows:
                    if type(item) is not dict or item.get("command") != "create_snapshot":
                        continue
                    resources = item.get("resources")
                    if type(resources) is not list or not any(
                        type(r) is dict
                        and r.get("type") in {"snapshot", "storage_box_snapshot"}
                        and r.get("id") == snapshot_id
                        for r in resources
                    ):
                        continue
                    try:
                        started = datetime.fromisoformat(item["started"].replace("Z", "+00:00"))
                        if (
                            started.utcoffset() is None
                            or abs((started - created).total_seconds()) > 600
                        ):
                            raise ValueError
                    except (KeyError, AttributeError, TypeError, ValueError):
                        raise ArchiveBlocked("Provider snapshot action time differs") from None
                    matches.append(item)
                following = pagination["next_page"]
                if following in (None, 0):
                    break
                if type(following) is not int or following != page + 1:
                    raise ArchiveBlocked("Provider action pagination differs")
                page = following
            else:
                raise ArchiveBlocked("Provider action scan exceeds its budget")
            if len(matches) != 1 or matches[0].get("status") != "success":
                raise ArchiveBlocked("Snapshot creation action cannot be uniquely reconciled")
            action_id = matches[0].get("id")
        if type(action_id) is not int or action_id <= 0:
            raise ArchiveBlocked("Provider snapshot action identity is unknown")
        action = self.request("GET", f"/storage_boxes/actions/{action_id}").get("action")
        if type(action) is not dict or action.get("id") != action_id:
            raise ArchiveBlocked("Provider snapshot action identity differs")
        resources = action.get("resources")
        if type(resources) is not list:
            raise ArchiveBlocked("Provider snapshot action resources are unknown")
        if not any(
            r.get("type") == "storage_box" and r.get("id") == self.box_id
            for r in resources
            if type(r) is dict
        ):
            raise ArchiveBlocked("Provider snapshot action Storage Box differs")
        if not any(
            r.get("type") in {"snapshot", "storage_box_snapshot"} and r.get("id") == snapshot_id
            for r in resources
            if type(r) is dict
        ):
            raise ArchiveBlocked("Provider snapshot action resource differs")
        if action.get("status") != "success":
            raise ArchiveBlocked("Provider snapshot action has not succeeded")
        if action.get("command") != operation + "_snapshot":
            raise ArchiveBlocked("Provider snapshot action command differs")
        try:
            started = datetime.fromisoformat(action["started"].replace("Z", "+00:00"))
            finished = datetime.fromisoformat(action["finished"].replace("Z", "+00:00"))
            if started.utcoffset() is None or finished.utcoffset() is None or finished < started:
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Provider snapshot action completion time is unknown") from None
        return action_id

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
