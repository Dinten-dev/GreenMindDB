"""Restore a bounded staging snapshot into a temporary table, never into live readings."""

import json
import os
import re

from app.visualization.storage import read_archive
from app.visualization.worker import engine_for_worker
from sqlalchemy import text

assert os.environ.get("VISUAL_TEST_ALLOWED") == "staging-20260915"
engine = engine_for_worker()
assert engine.url.username != "admin"
with engine.begin() as db:
    saved = db.execute(
        text(
            "SELECT archive_manifest FROM visual_chunk WHERE status='verified' AND source_rows BETWEEN 1 AND 5000 ORDER BY source_rows DESC LIMIT 1"
        )
    ).scalar_one()
    manifest = json.loads(saved)
    rows = list(read_archive(manifest))
    columns = sorted(rows[0])
    assert all(re.fullmatch("[a-z_][a-z0-9_]*", column) for column in columns)
    db.execute(
        text(
            "CREATE TEMP TABLE visual_restore_probe (LIKE sensor_reading) ON COMMIT DROP"
        )
    )
    db.execute(
        text(
            f"INSERT INTO visual_restore_probe ({','.join(columns)}) VALUES ({','.join(':' + column for column in columns)})"
        ),
        rows,
    )
    restored = db.execute(text("SELECT * FROM visual_restore_probe")).mappings().all()

    def canonical(items):
        return sorted(
            json.dumps(dict(item), sort_keys=True, default=str, separators=(",", ":"))
            for item in items
        )

    assert canonical(restored) == canonical(rows), (
        "Restored values differ from the verified source snapshot"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "restored_rows": len(restored),
                "source_rows": manifest["rows"],
                "live_readings_modified": False,
            }
        )
    )
