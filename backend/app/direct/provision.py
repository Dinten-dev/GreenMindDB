"""Operator CLI; schema/device creation is explicit and never part of Legacy boot."""

import argparse
import os
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

from app.direct.auth import issue_token
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.models import Base, Budget, Device, PayloadReference


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-schema")
    sub.add_parser("upgrade-spool-indexes")
    upgrade = sub.add_parser("upgrade-payload-references")
    upgrade.add_argument("--read-role", action="append", default=[])
    add = sub.add_parser("add-device")
    add.add_argument("--organization-id", type=uuid.UUID, required=True)
    add.add_argument("--zone-id", type=uuid.UUID, required=True)
    add.add_argument("--mode", choices=["DIRECT", "DUAL"], required=True)
    add.add_argument("--legacy-sensor-id", type=uuid.UUID)
    add.add_argument("--token-file", type=Path, required=True)
    disable = sub.add_parser("disable-device")
    disable.add_argument("device_id", type=uuid.UUID)
    args = parser.parse_args()
    cfg = DirectSettings()
    engine = make_engine(cfg)
    if args.command == "upgrade-spool-indexes":
        from app.direct.spool_indexes import upgrade

        upgrade(engine)
        print("Direct spool indexes verified; existing recordings unchanged")
        return
    if args.command == "upgrade-payload-references":
        from sqlalchemy import inspect

        names = set(inspect(engine).get_table_names())
        if "direct_chunk" not in names or any(not name.startswith("direct_") for name in names):
            raise SystemExit("Refusing upgrade outside the existing Direct database")
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL lock_timeout='100ms'"))
                connection.execute(text("SET LOCAL statement_timeout='2000ms'"))
                for role in args.read_role:
                    permitted = connection.execute(
                        text("SELECT has_table_privilege(:role, 'direct_chunk', 'SELECT')"),
                        {"role": role},
                    ).scalar()
                    if not permitted:
                        raise SystemExit("Reference reader must already have Direct chunk access")
            elif args.read_role:
                raise SystemExit("Read grants require PostgreSQL")
            PayloadReference.__table__.create(connection, checkfirst=True)
            for role in args.read_role:
                quoted = connection.dialect.identifier_preparer.quote(role)
                connection.execute(text("GRANT SELECT ON direct_chunk_payload_ref TO " + quoted))
        print("Direct payload references initialized; no existing rows changed")
        return
    if args.command == "init-schema":
        # The separate database must be empty or contain only Direct tables.
        from sqlalchemy import inspect

        if any(not name.startswith("direct_") for name in inspect(engine).get_table_names()):
            raise SystemExit("Refusing schema creation in a database containing Legacy tables")
        Base.metadata.create_all(engine)
        with transaction(engine) as db:
            if db.get(Budget, 1) is None:
                db.add(Budget(id=1, used_bytes=0))
        print("Direct schema initialized; Legacy migrations untouched")
        return
    if args.command == "disable-device":
        with transaction(engine) as db:
            device = db.get(Device, str(args.device_id))
            if device is None:
                raise SystemExit("Unknown Direct device")
            device.active = False
        print("Direct device disabled")
        return
    if (args.mode == "DUAL") != bool(args.legacy_sensor_id):
        raise SystemExit("DUAL requires a Legacy sensor; DIRECT must not reuse one")
    # Validate real ownership using an operator-provided read-only Legacy DSN.
    legacy_url = os.environ.get("DIRECT_LEGACY_READ_DATABASE_URL", "")
    if not legacy_url:
        raise SystemExit("DIRECT_LEGACY_READ_DATABASE_URL is required to verify ownership")
    legacy_engine = create_engine(legacy_url, pool_size=1, max_overflow=0)
    try:
        with legacy_engine.connect() as connection:
            zone = connection.execute(
                text("SELECT id FROM zone WHERE id=:zone AND organization_id=:org"),
                {"zone": args.zone_id, "org": args.organization_id},
            ).first()
            if not zone:
                raise SystemExit("Zone does not belong to this organization")
            if args.legacy_sensor_id:
                sensor = connection.execute(
                    text(
                        "SELECT s.id FROM sensor s JOIN gateway g ON s.gateway_id=g.id WHERE s.id=:sensor AND g.zone_id=:zone"
                    ),
                    {"sensor": args.legacy_sensor_id, "zone": args.zone_id},
                ).first()
                if not sensor:
                    raise SystemExit("Legacy sensor does not belong to this zone")
    finally:
        legacy_engine.dispose()
    device_id = str(uuid.uuid4())
    token, digest = issue_token(device_id)
    # O_EXCL avoids overwriting a provisioning secret. Token is never printed.
    descriptor = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(token + "\n")
        target.flush()
        os.fsync(target.fileno())
    with transaction(engine) as db:
        db.add(
            Device(
                id=device_id,
                organization_id=str(args.organization_id),
                zone_id=str(args.zone_id),
                mode=args.mode,
                legacy_sensor_id=str(args.legacy_sensor_id) if args.legacy_sensor_id else None,
                key_hash=digest,
                active=True,
                spool_bytes=0,
            )
        )
    print(f"Direct device created: {device_id}; credential written to the requested file")


if __name__ == "__main__":
    main()
