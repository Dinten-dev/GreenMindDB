import json

import httpx
import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import SnapshotAPI, private_token, readback


def api(handler):
    return SnapshotAPI(
        httpx.Client(transport=httpx.MockTransport(handler)),
        12,
        "u676312",
        "u676312.your-storagebox.de",
    )


def test_wrong_box_never_posts():
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(200, json={"storage_box": {"id": 12, "username": "u999"}})

    with pytest.raises(ArchiveBlocked, match="another"):
        api(handler).create("reviewed")
    assert requests == ["GET"]


def test_creation_is_not_readback_acceptance():
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "storage_box": {
                        "id": 12,
                        "username": "u676312",
                        "server": "u676312.your-storagebox.de",
                        "access_settings": {"zfs_enabled": True},
                    }
                },
            )
        assert json.loads(request.content)["labels"]["purpose"] == "greenmind-delete-preflight"
        return httpx.Response(201, json={"snapshot": {"id": 42}, "action": {"status": "running"}})

    assert api(handler).create("reviewed")["action"]["status"] == "running"
    assert requests == ["GET", "POST"]


def test_snapshot_catalog_corruption_is_not_accepted(tmp_path):
    class Box:
        def download_catalog(self, key, path, limit, *, snapshot):
            assert snapshot == "daily-20261003"
            path.write_bytes(b"wrong")

    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        readback(
            Box(), {"name": "daily-20261003"}, {"key": "k", "size": 5, "sha256": "a" * 64}, tmp_path
        )


def test_private_token_rejects_public_files_and_symlinks(tmp_path):
    path = tmp_path / "token"
    path.write_text("a" * 64)
    path.chmod(0o644)
    with pytest.raises(ArchiveBlocked, match="private"):
        private_token(path)
    path.chmod(0o600)
    assert private_token(path) == "a" * 64
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ArchiveBlocked, match="private"):
        private_token(link)


def test_inventory_uses_manual_capacity_and_requires_complete_pagination():
    details = {"id": 42, "storage_box": 12, "name": "manual-42", "created": "2026-01-01T00:00:00Z"}

    def handler(request):
        if request.url.path.endswith("/storage_boxes/12"):
            return httpx.Response(
                200,
                json={
                    "storage_box": {
                        "id": 12,
                        "username": "u676312",
                        "server": "u676312.your-storagebox.de",
                        "access_settings": {"zfs_enabled": True},
                        "storage_box_type": {"snapshot_limit": 10},
                    }
                },
            )
        if request.url.path.endswith("/snapshots/42"):
            return httpx.Response(200, json={"snapshot": details})
        return httpx.Response(
            200,
            json={
                "snapshots": [{"id": 42, "is_automatic": False}, {"id": 43, "is_automatic": True}],
                "meta": {"pagination": {"next_page": None}},
            },
        )

    assert api(handler).inventory() == ([details], 10)

    def incomplete(request):
        response = handler(request)
        if request.url.path.endswith("/snapshots"):
            return httpx.Response(
                200, json={"snapshots": [], "meta": {"pagination": {"next_page": 2}}}
            )
        return response

    with pytest.raises(ArchiveBlocked, match="Complete provider snapshot inventory"):
        api(incomplete).inventory()


def test_delete_requires_own_verified_journal_intent_before_provider_access():
    import sqlite3
    from types import SimpleNamespace

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE managed_snapshot(intent TEXT,state TEXT,snapshot TEXT,proof TEXT)")
    client = api(lambda _: pytest.fail("Unexpected provider request"))
    with pytest.raises(ArchiveBlocked, match="delete intent"):
        client.delete_managed(42, ledger=SimpleNamespace(db=db), intent="a" * 32)
    db.close()


def test_creation_action_must_succeed_and_match_both_provider_resources():
    action = {
        "id": 100,
        "status": "success",
        "command": "create_snapshot",
        "started": "2026-01-01T00:00:00Z",
        "finished": "2026-01-01T00:01:00Z",
        "resources": [
            {"type": "storage_box", "id": 12},
            {"type": "storage_box_snapshot", "id": 42},
        ],
    }
    seen = []

    class FakeAPI(SnapshotAPI):
        def read(self, snapshot_id):
            return {"id": snapshot_id, "created": "2026-01-01T00:01:00Z"}

        def request(self, method, suffix, payload=None):
            seen.append((method, suffix))
            if suffix == "/storage_boxes/12/actions?per_page=100&page=1":
                return {"actions": [action], "meta": {"pagination": {"next_page": None}}}
            assert suffix == "/storage_boxes/actions/100"
            return {"action": action}

    provider = FakeAPI(None, 12, "u676312", "u676312.your-storagebox.de")
    assert provider.require_action(None, 42, "create") == 100
    assert seen == [
        ("GET", "/storage_boxes/12/actions?per_page=100&page=1"),
        ("GET", "/storage_boxes/actions/100"),
    ]
    action["status"] = "running"
    with pytest.raises(ArchiveBlocked, match="not succeeded"):
        provider.require_action(100, 42, "create")
    action["status"] = "success"
    action["resources"].pop()
    with pytest.raises(ArchiveBlocked, match="resource differs"):
        provider.require_action(100, 42, "create")


def test_ambiguous_creation_action_never_confirms():
    action = {
        "id": 100,
        "status": "success",
        "command": "create_snapshot",
        "started": "2026-01-01T00:00:00Z",
        "finished": "2026-01-01T00:01:00Z",
        "resources": [
            {"type": "storage_box", "id": 12},
            {"type": "storage_box_snapshot", "id": 42},
        ],
    }

    class FakeAPI(SnapshotAPI):
        def read(self, snapshot_id):
            return {"id": snapshot_id, "created": "2026-01-01T00:01:00Z"}

        def request(self, method, suffix, payload=None):
            return {
                "actions": [action, dict(action, id=101)],
                "meta": {"pagination": {"next_page": None}},
            }

    provider = FakeAPI(None, 12, "u676312", "u676312.your-storagebox.de")
    with pytest.raises(ArchiveBlocked, match="uniquely reconciled"):
        provider.require_action(None, 42, "create")


def test_running_or_unpaginated_provider_actions_block_rotation():
    class FakeAPI(SnapshotAPI):
        def validate_box(self):
            return {}

        def request(self, method, suffix, payload=None):
            return {"actions": [{"status": "running"}], "meta": {"pagination": {"next_page": None}}}

    provider = FakeAPI(None, 12, "u676312", "u676312.your-storagebox.de")
    with pytest.raises(ArchiveBlocked, match="action is running"):
        provider.require_idle_actions()
    provider.request = lambda *_args: {"actions": []}
    with pytest.raises(ArchiveBlocked, match="Complete provider action inventory"):
        provider.require_idle_actions()
