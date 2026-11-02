"""Controlled key enrollment without key rotation or fleet password scans.

Run prepare-index before enabling public digest lookup. Run enroll only with a
known Gateway UUID and its existing secret in an owner-only file. Nothing here
is exposed as an HTTP endpoint or runs automatically during server startup.
"""

import argparse
import os
import stat
import uuid
from pathlib import Path

from sqlalchemy import inspect, text, update
from sqlalchemy.orm import Session

from app.gateway_auth import _verify_gateway_key, gateway_key_digest, legacy_hash_filter
from app.models.master import Gateway


def prepare_index(engine):
    """Independent of Alembic head0023/0024/0025; no auth or archive migration."""
    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gateway_api_key_hash ON gateway (api_key_hash)"
                )
            )
            valid = connection.execute(
                text(
                    "SELECT indisvalid FROM pg_index WHERE indexrelid = 'ix_gateway_api_key_hash'::regclass"
                )
            ).scalar_one()
            if not valid:
                raise ValueError("Lookup index is invalid; repair before release")
    else:
        index = next(i for i in Gateway.__table__.indexes if i.name == "ix_gateway_api_key_hash")
        index.create(engine, checkfirst=True)


def enroll_key(db, gateway_id, key):
    """Stage one compare-and-swap; the caller owns commit/rollback."""
    if not key or len(key.encode()) > 128:
        raise ValueError("Invalid credential")
    gateway = db.get(Gateway, uuid.UUID(str(gateway_id)))
    if not gateway or not gateway.is_active or not gateway.api_key_hash:
        raise ValueError("Gateway not eligible")
    previous = gateway.api_key_hash
    # Plain bcrypt silently ignores bytes past 72. Never enroll one accepted
    # spelling and invalidate another spelling of a truncated credential.
    if previous.startswith(("$2a$", "$2b$", "$2y$")) and len(key.encode()) > 72:
        raise ValueError("Truncated bcrypt credential needs controlled rotation")
    if not _verify_gateway_key(gateway, key):
        raise ValueError("Invalid credential")
    digest = gateway_key_digest(key)
    if previous == digest:
        return False
    result = db.execute(
        update(Gateway)
        .where(
            Gateway.id == gateway.id, Gateway.api_key_hash == previous, Gateway.is_active.is_(True)
        )
        .values(api_key_hash=digest)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("Gateway changed; retry with current state")
    db.expire(gateway)
    return True


def read_secret(path):
    # No secret in process arguments, stdout, exceptions or symlink targets.
    fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Credential file must be owner-only and owned by this user")
        raw = source.read(131)
    if len(raw) > 130:
        raise ValueError("Invalid credential file")
    key = raw.rstrip(b"\r\n")
    if not key or len(key) > 128:
        raise ValueError("Invalid credential file")
    return key.decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare-index", "status", "enroll"])
    parser.add_argument("--gateway-id", type=uuid.UUID)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    from app.database import engine

    if args.operation == "prepare-index":
        prepare_index(engine)
        print("Lookup index prepared")
        return
    indexes = inspect(engine).get_indexes("gateway")
    if not any(
        i["name"] == "ix_gateway_api_key_hash" and i["column_names"] == ["api_key_hash"]
        for i in indexes
    ):
        parser.error("Prepare the lookup index before enrollment or release")
    with Session(engine) as db:
        if args.operation == "status":
            print(
                f"Legacy credentials awaiting enrollment: {db.query(Gateway).filter(legacy_hash_filter()).count()}"
            )
            return
        if not args.gateway_id or not args.key_file:
            parser.error("enroll requires --gateway-id and --key-file")
        try:
            changed = enroll_key(db, args.gateway_id, read_secret(args.key_file))
            db.commit()
        except (ValueError, OSError, UnicodeError):
            db.rollback()
            parser.error("Enrollment refused; check gateway state and private credential file")
        print("Credential enrolled" if changed else "Credential already enrolled")


if __name__ == "__main__":
    main()
