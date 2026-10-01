"""Explicit, bounded archive batch. Deliberately has no executable main/scheduler."""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .policy import ArchiveBlocked, Config, Ledger, archive_one


def configuration() -> Config:
    """Independent settings; old deployments acquire no new required variables."""
    def enabled(name):
        value = os.environ.get(name, 'false').lower()
        if value not in {'true', 'false'}:
            raise ArchiveBlocked(f'{name} must be true or false')
        return value == 'true'
    return Config(
        enabled=enabled('RAW_ARCHIVE_ENABLED'),
        delete_enabled=enabled('RAW_ARCHIVE_DELETE_ENABLED'),
        reads_accepted=enabled('RAW_ARCHIVE_READS_ACCEPTED'),
        root=Path(os.environ.get('RAW_ARCHIVE_STATE_DIR', '/var/lib/greenmind-raw-archive')),
        namespace=os.environ.get('RAW_ARCHIVE_ENVIRONMENT', 'production'),
        max_file_bytes=int(os.environ.get('RAW_ARCHIVE_MAX_FILE_BYTES', '8388608')),
        max_files=int(os.environ.get('RAW_ARCHIVE_MAX_FILES', '5000')),
    )


@contextmanager
def exclusive_worker(config):
    config.guard()
    config.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (config.root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArchiveBlocked('Another archive batch is running') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_batch(config, recordings, source, destination, catalog, *, healthy):
    """An explicitly supplied reviewed batch; no unbounded full-bucket scans.

    healthy is a mandatory operator-provided read-only service/headroom probe.
    No candidate generation or feature extraction occurs implicitly.
    The returned outcomes are suitable for resumable, reviewed batch manifests.
    """
    config.guard()
    if len(recordings) > config.max_files:
        raise ArchiveBlocked('Batch exceeds configured maximum')
    def checkpoint():
        if (config.root / 'PAUSE').exists() or not healthy():
            raise ArchiveBlocked('Archive paused: preserve receiver headroom')

    outcomes = []
    with exclusive_worker(config):
        journal = Ledger(config.root)
        try:
            for recording in recordings:
                if (config.root / 'PAUSE').exists() or not healthy():
                    raise ArchiveBlocked('Archive paused: preserve receiver headroom')
                try:
                    status = archive_one(config, recording, source, destination,
                                         journal, catalog.revalidate, checkpoint=checkpoint)
                    outcomes.append({'id': recording.archive_id, 'status': status})
                except Exception:
                    # Stop the batch instead of repeatedly consuming resources during failure.
                    outcomes.append({'id': recording.archive_id, 'status': 'blocked'})
                    raise
                time.sleep(1)
        finally:
            journal.close()
    return outcomes
