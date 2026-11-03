"""Broker-side export restore copies with independent bounded provenance."""

import json
import os
import time
import uuid
from contextlib import contextmanager

from app.visualization.archive_export_storage import ExportStore
from app.visualization.archive_jobs import connect

BROKER_BYTES = 320 * 1024**2


def reap_broker_exports(scratch):
    path = scratch / "export-copies"
    if not path.exists():
        return
    with (
        ExportStore(path, total_limit=BROKER_BYTES, user_limit=BROKER_BYTES) as store,
        store.locked(),
        connect(path) as db,
    ):
        for row in db.execute("SELECT * FROM jobs WHERE status IN ('working','queued')").fetchall():
            lock = store.acquire_job(row["id"])
            if lock is not None:
                try:
                    with db:
                        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (row["id"],))
                finally:
                    os.close(lock)
        store.reap(db)


@contextmanager
def managed_export_scratch(scratch):
    path = scratch / "export-copies"
    path.mkdir(mode=0o700, exist_ok=True)
    reap_broker_exports(scratch)
    identity = str(uuid.uuid4())
    items = [{"size": 64 * 1024**2}]
    with (
        ExportStore(path, total_limit=BROKER_BYTES, user_limit=BROKER_BYTES) as store,
        connect(path) as db,
    ):
        lock = None
        try:
            with store.locked():
                store.budget(db, incoming=("broker-export", items))
                lock = store.acquire_job(identity)
                if lock is None:
                    raise RuntimeError("Broker export busy")
                with db:
                    db.execute(
                        "INSERT INTO jobs(id,user_id,kind,sensor_id,created,status,items) VALUES (?,?,?,?,?,'working',?)",
                        (
                            identity,
                            "broker-export",
                            "broker",
                            identity,
                            time.time(),
                            json.dumps(items),
                        ),
                    )
                folder = store.scratch_path(identity, db)
            yield folder
        finally:
            if lock is not None:
                try:
                    with store.locked(), db:
                        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (identity,))
                        store.clean_job({"id": identity}, db)
                finally:
                    os.close(lock)
