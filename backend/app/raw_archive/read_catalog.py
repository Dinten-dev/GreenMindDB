"""An atomic read-only view keeps broker processes outside the writer journal."""

import os
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .policy import ArchiveBlocked


def publish(db, state, group, *, checkpoint=lambda: None):
    state = Path(state)
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise ArchiveBlocked("Private absolute WAV catalogue state required")
    if type(group) is not int or group < 1:
        raise ArchiveBlocked("Explicit WAV catalogue reader group required")
    size = (
        db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
    )
    if shutil.disk_usage(state).free < 2 * 1024**3 + size:
        raise ArchiveBlocked("WAV catalogue disk safety reserve exhausted")
    target = state / "raw-read.sqlite3"
    if target.is_symlink():
        raise ArchiveBlocked("Unexpected WAV catalogue symlink")
    path = state / ("raw-read-view-" + uuid.uuid4().hex + ".sqlite3")
    checkpoint()
    try:
        with closing(sqlite3.connect(path)) as copied:
            db.backup(copied, pages=128, progress=lambda *args: checkpoint())
            copied.execute("PRAGMA journal_mode=DELETE")
            if copied.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ArchiveBlocked("WAV catalogue integrity check failed")
        os.chown(path, -1, group)
        path.chmod(0o640)
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        checkpoint()
        os.replace(path, target)
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def connect(config):
    explicit = os.environ.get("RAW_ARCHIVE_READ_CATALOG_FILE")
    path = Path(explicit) if explicit else config.root / "archive.sqlite3"
    if not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Private absolute WAV read catalogue required")
    uri = path.as_uri() + ("?mode=ro&immutable=1" if explicit else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        yield db
