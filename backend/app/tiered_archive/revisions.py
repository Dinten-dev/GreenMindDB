"""Original Direct manifests remain retrievable after feature compaction."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked


def relation():
    if os.getenv("TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED", "false") != "true":
        return "direct_revision"
    return """(SELECT segment_id,revision,manifest,manifest_sha256 FROM direct_revision
        UNION ALL SELECT l.segment_id,l.revision,l.manifest,l.manifest_sha256
        FROM direct_recording_locator l WHERE NOT EXISTS
          (SELECT 1 FROM direct_revision r WHERE r.segment_id=l.segment_id AND r.revision=l.revision))"""


def fetch(db, segment_id, revision):
    from app.direct.models import Revision

    actual = db.get(Revision, (segment_id, revision))
    if actual is not None:
        return actual
    if os.getenv("TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED", "false") != "true":
        return None
    locator = (
        db.execute(
            text(
                "SELECT archive_candidate,manifest_sha256 FROM direct_recording_locator WHERE segment_id=:id AND revision=:revision"
            ),
            {"id": segment_id, "revision": revision},
        )
        .mappings()
        .first()
    )
    if locator is None:
        return None
    from .client import request
    from .codec import decode

    connection, response = request(
        "/tiered-revision",
        {"identity": locator["archive_candidate"], "segment": segment_id, "revision": revision},
        timeout=30,
    )
    try:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ArchiveBlocked("Scientific revision exceeds budget")
        row = decode(raw)
    finally:
        connection.close()
    canonical = json.dumps(row["manifest"], sort_keys=True, separators=(",", ":")).encode()
    if (
        row["segment_id"] != segment_id
        or row["revision"] != revision
        or row["manifest_sha256"] != locator["manifest_sha256"]
        or hashlib.sha256(canonical).hexdigest() != locator["manifest_sha256"]
    ):
        raise ArchiveBlocked("Scientific Direct revision identity mismatch")
    return SimpleNamespace(**row)


def private_revision(data, destination):
    import re
    import uuid

    from . import parquet
    from .ledger import Ledger
    from .runtime import state_root
    from .storage import Store

    if (
        not isinstance(data, dict)
        or set(data) != {"identity", "segment", "revision"}
        or not re.fullmatch(r"[a-f0-9]{64}", data["identity"])
        or type(data["revision"]) is not int
        or data["revision"] < 1
    ):
        raise ArchiveBlocked("Exact Direct revision required")
    segment = str(uuid.UUID(data["segment"]))
    ledger = Ledger(state_root(), read_only=True)
    try:
        receipt = ledger.get(data["identity"])
        if (
            not receipt
            or receipt["state"] != "evicted"
            or receipt["source"]["dataset"] != "direct_revision"
        ):
            raise ArchiveBlocked("No evicted scientific Direct revision")
        with tempfile.TemporaryDirectory(dir=state_root() / "cache") as folder:
            reference = receipt["files"][0]
            path = Path(folder) / "revision.parquet"
            Store(destination).download(reference["key"], path, reference["bytes"])
            found = []
            for envelope in parquet.read(path, reference):
                row = envelope["body"]["row"]
                if row["segment_id"] == segment and row["revision"] == data["revision"]:
                    found.append(row)
            if len(found) != 1:
                raise ArchiveBlocked("Scientific revision missing or ambiguous")
            return found[0]
    finally:
        ledger.close()
