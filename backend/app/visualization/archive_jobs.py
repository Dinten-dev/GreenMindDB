"""Small job store shared with the worker, without API/model imports."""

import os
import sqlite3
from pathlib import Path

MAX_FILES = 5000
MAX_BYTES = 2 * 1024**3
PART_BYTES = 500 * 1024**2
EXPIRES = 24 * 3600


def unavailable(message):
    from fastapi import HTTPException

    raise HTTPException(503, message)


def root():
    if os.getenv("ARCHIVE_EXPORTS_ENABLED", "false").lower() != "true":
        unavailable("Archivexporte sind noch nicht aktiviert")
    path = Path(os.environ["ARCHIVE_EXPORT_STATE_DIR"])
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        unavailable("Exportbereich nicht verfügbar")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        unavailable("Exportbereich nicht privat")
    return path


def connect(path):
    db = sqlite3.connect(path / "jobs.sqlite3", timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
      sensor_id TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
      items TEXT NOT NULL, parts TEXT NOT NULL DEFAULT '[]',
      completed INTEGER NOT NULL DEFAULT 0, error TEXT)""")
    return db
