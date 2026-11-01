"""Source-owned WAV reader leases, paired with the deletion writer's xact lock.

Activation requires independently bound reader coverage, not merely this flag.
Existing deployments remain unchanged until that contract is enabled.
"""

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .policy import ArchiveBlocked

_active = ContextVar("greenmind_wav_work_leases", default=frozenset())


class WavWorkLeaseBusy(ArchiveBlocked):
    """A live source writer owns the WAV; retry without blaming the recording."""


def enabled():
    return os.environ.get("WAV_SOURCE_WORK_LEASES_ENABLED", "false").lower() == "true"


def object_lock_key(kind, bucket, key):
    if (
        kind not in {"gateway", "direct"}
        or not isinstance(bucket, str)
        or not bucket
        or len(bucket) > 255
        or not isinstance(key, str)
        or not key.endswith(".wav")
        or len(key) > 1024
    ):
        raise ArchiveBlocked("Exact source WAV identity required for work lease")
    raw = json.dumps([kind, bucket, key], separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


@lru_cache(maxsize=2)
def _source_engine(kind):
    # On the archive host these existing bindings must point upstream to the
    # actual source DB. A SQL mirror cannot participate in its advisory locks.
    url = os.environ.get("RAW_ARCHIVE_" + kind.upper() + "_DATABASE_URL")
    if not url:
        if kind == "gateway":
            from app.database import DATABASE_URL

            url = DATABASE_URL
        else:
            from app.direct.config import DirectSettings

            url = DirectSettings().database_url.get_secret_value()
    if not url or make_url(url).get_backend_name() != "postgresql":
        raise ArchiveBlocked("WAV work leases require the authoritative PostgreSQL source")
    return create_engine(
        url,
        pool_size=2,
        max_overflow=6,
        pool_timeout=3,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 3,
            "options": "-c default_transaction_read_only=on -c statement_timeout=2000 "
            "-c lock_timeout=150 -c idle_in_transaction_session_timeout=0",
        },
    )


@contextmanager
def shared_wav_lease(kind, bucket, key, *, track_nested=True):
    if not enabled() or not key.endswith(".wav"):
        yield
        return
    lock = object_lock_key(kind, bucket, key)
    identity = (kind, bucket, key)
    if identity in _active.get():
        yield
        return
    token = None
    # One dedicated bounded read-only connection holds the source-owned lock.
    # Only these lease connections disable idle-xact expiry: expiry must not
    # silently release an active stream. Cancellation/close releases the xact.
    with _source_engine(kind).connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        acquired = connection.execute(
            text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": lock}
        ).scalar_one()
        if acquired is not True:
            raise WavWorkLeaseBusy("WAV source is busy; retry after active maintenance")
        if track_nested:
            token = _active.set(_active.get() | {identity})
        try:
            yield
        finally:
            if token is not None:
                _active.reset(token)


class LeasedBody:
    """Close source bytes before releasing their source transaction, exactly once."""

    def __init__(self, body, resources):
        self.body, self.resources, self.closed = body, resources, False

    def __getattr__(self, name):
        return getattr(self.body, name)

    def read(self, *args, **kwargs):
        return self.body.read(*args, **kwargs)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.body.close()
            finally:
                self.resources.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@contextmanager
def exclusive_wav_lease(kind, bucket, key, *, source_engine):
    """Fence all shared source readers across final verification and deletion."""
    if not enabled():
        raise ArchiveBlocked("Source-owned WAV work leases are required for deletion")
    lock = object_lock_key(kind, bucket, key)
    authority = _source_engine(kind)
    actual = getattr(source_engine, "url", None)
    bound = getattr(authority, "url", None)
    if (
        actual is None
        or bound is None
        or (actual.get_backend_name(), actual.host, actual.port, actual.database)
        != (bound.get_backend_name(), bound.host, bound.port, bound.database)
    ):
        raise ArchiveBlocked("WAV lease is not bound to the authoritative source database")
    with authority.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        if (
            connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}
            ).scalar_one()
            is not True
        ):
            raise WavWorkLeaseBusy("WAV source has active readers")
        yield


@contextmanager
def shared_wav_leases(kind, bucket, keys):
    """Hold a sorted bounded set of shared WAV locks on one source connection."""
    keys = sorted(set(keys))
    if not keys or len(keys) > 64:
        raise ArchiveBlocked("Bounded nonempty WAV lease scope required")
    if not enabled():
        yield
        return
    identities = frozenset((kind, bucket, key) for key in keys)
    existing = _active.get()
    if identities <= existing:
        yield
        return
    if identities & existing:
        raise ArchiveBlocked("Overlapping nested WAV lease scopes are unsupported")
    with _source_engine(kind).connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for key in keys:
            lock = object_lock_key(kind, bucket, key)
            if (
                connection.execute(
                    text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": lock}
                ).scalar_one()
                is not True
            ):
                raise WavWorkLeaseBusy("WAV source is busy; retry after active maintenance")
        token = _active.set(existing | identities)
        try:
            yield
        finally:
            _active.reset(token)
