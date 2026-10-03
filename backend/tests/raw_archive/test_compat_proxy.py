"""Hash-pinned patch preserves receivers, continuity protection and overlays."""

import hashlib

import pytest

from app.raw_archive.compat_proxy import ANCHOR, draft, rollback
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
