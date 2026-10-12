"""Hash-pinned patch preserves receivers, continuity protection and overlays."""

import hashlib

import pytest

from app.raw_archive.compat_proxy import (
    ANCHOR,
    archive_host_draft,
    archive_host_rollback,
    draft,
    rollback,
)
from app.raw_archive.policy import ArchiveBlocked


def test_exact_reversal_preserves_all_existing_bytes():
    original = (
        "# admin/static/continuity unchanged\n"
        + ANCHOR
        + "        proxy_pass http://127.0.0.1:8003;\n    }\n"
    )
    candidate = draft(original, hashlib.sha256(original.encode()).hexdigest())
    assert rollback(candidate) == original
    assert "proxy_pass http://127.0.0.1:8003;" in candidate
    assert "location ~ ^/api/v1/direct-ingest/segments/" in candidate
    with pytest.raises(ArchiveBlocked, match="changed"):
        draft(original + "# drift", hashlib.sha256(original.encode()).hexdigest())
    with pytest.raises(ArchiveBlocked, match="structure"):
        draft(candidate, hashlib.sha256(candidate.encode()).hexdigest())


def test_new_reader_port_preserves_current_dashboard_reader():
    original = "location /dashboard { proxy_pass http://127.0.0.1:8140; }\n" + ANCHOR + "    }\n"
    sha = hashlib.sha256(original.encode()).hexdigest()
    candidate = draft(original, sha, reader_port=8141)
    assert "location /dashboard { proxy_pass http://127.0.0.1:8140; }" in candidate
    assert rollback(candidate, reader_port=8141) == original


def test_exact_archive_reverse_port_preserves_receiver_routes():
    original = "location / { proxy_pass http://127.0.0.1:8152; }\n" + ANCHOR + "    }\n"
    candidate = draft(original, hashlib.sha256(original.encode()).hexdigest(), reader_port=28141)
    assert rollback(candidate, reader_port=28141) == original
    assert "http://127.0.0.1:28141" in candidate
    assert "http://127.0.0.1:8152" in candidate
    with pytest.raises(ArchiveBlocked):
        draft(original, hashlib.sha256(original.encode()).hexdigest(), reader_port=28142)


def test_archive_host_overlay_is_byte_exact_and_rejects_unreviewed_readers():
    original = (
        "location / { proxy_pass http://127.0.0.1:8152; }\n"
        "location /health { proxy_pass http://127.0.0.1:8140/health; }\n"
        "location /data { proxy_pass http://127.0.0.1:8140; }\n"
        "location /export { proxy_pass http://127.0.0.1:8140; }\n" + ANCHOR + "    }\n"
    )
    candidate = archive_host_draft(original, hashlib.sha256(original.encode()).hexdigest())
    assert archive_host_rollback(candidate) == original
    assert "http://127.0.0.1:8152" in candidate
    assert "http://127.0.0.1:8003" not in candidate  # No invented ingestion routes.
    changed = original.replace(":8140/health", ":8142/health")
    with pytest.raises(ArchiveBlocked):
        archive_host_draft(changed, hashlib.sha256(changed.encode()).hexdigest())
