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
