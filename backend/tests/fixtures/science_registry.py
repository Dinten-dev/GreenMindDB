"""Synthetic 46-part metadata for registry lifecycle tests; never native evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.raw_archive import scientific_capture as capture
from app.raw_archive import scientific_parts as parts
from tests.raw_archive.test_scientific_v3 import manifest, row


def metadata(tmp_path):
    parent = tmp_path / "synthetic-science"
    parent.mkdir(mode=0o700)
    columns = capture.roster()
    native_rows = {
        table: [
            row(len(columns[table]), first=UUID(int=1 + table_index * 23 + i).bytes)
            for i in range(23)
        ]
        for table_index, table in enumerate(capture.TABLES)
    }
    _source, proof, _capture_sha = manifest(parent, native_rows)
    finished = datetime.now(UTC) - timedelta(seconds=2)
    proof["started_at"] = (finished - timedelta(seconds=1)).isoformat()
    proof["finished_at"] = finished.isoformat()
    capture_raw = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    capture_sha = hashlib.sha256(capture_raw).hexdigest()
    entries = []
    for table in capture.TABLES:
        offset = len(capture.HEADER)
        for sequence, native in enumerate(native_rows[table]):
            data = capture.HEADER + native + capture.TRAILER
            entries.append(
                {
                    "table": table,
                    "sequence": sequence,
                    "first_row": sequence,
                    "rows": 1,
                    "start": offset,
                    "end": offset + len(native),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            offset += len(native)
    index = {
        "format": parts.PART_FORMAT,
        "capture_id": proof["capture_id"],
        "capture_sha256": capture_sha,
        "scope_sha256": proof["scope_sha256"],
        "complete": True,
        "max_part_bytes": parts.MAX_PART_BYTES,
        "max_part_rows": parts.MAX_PART_ROWS,
        "parts": entries,
        "parent_fk_closure_proven": False,
        "authenticated": False,
    }
    index_raw = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return capture_raw, index_raw
