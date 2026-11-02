"""Prepare typed Legacy pairing without changing existing Alembic heads.

Stop all Legacy pairing issuers/redeemers before prepare. Activate only matching
new workers afterwards; old redeemers do not understand purpose separation.
This command never runs automatically during API startup.
"""

import argparse

from sqlalchemy import inspect, text

CONSTRAINT = "ck_pairing_code_purpose_v1"


def prepare_pairing(engine):
    """Atomically invalidate untyped codes and enforce purpose on future writes."""
    if engine.dialect.name != "postgresql":
        raise ValueError("Pairing upgrade requires PostgreSQL")
    with engine.begin() as connection:
        connection.execute(text("LOCK TABLE pairing_code IN ACCESS EXCLUSIVE MODE"))
        connection.execute(
            text("ALTER TABLE pairing_code ADD COLUMN IF NOT EXISTS purpose varchar(16)")
        )
        invalid = connection.execute(
            text(
                "SELECT count(*) FROM pairing_code WHERE purpose NOT IN ('gateway', 'sensor', 'legacy')"
            )
        ).scalar_one()
        if invalid:
            raise ValueError("Unknown pairing purposes; inspect before retrying")
        connection.execute(
            text(
                "UPDATE pairing_code SET purpose = 'legacy', used_at = COALESCE(used_at, clock_timestamp()) "
                "WHERE purpose IS NULL OR purpose = 'legacy'"
            )
        )
        connection.execute(text("ALTER TABLE pairing_code ALTER COLUMN purpose DROP DEFAULT"))
        connection.execute(text("ALTER TABLE pairing_code ALTER COLUMN purpose SET NOT NULL"))
        # Rebuild our own versioned constraint, avoiding trust in its name alone.
        connection.execute(text(f"ALTER TABLE pairing_code DROP CONSTRAINT IF EXISTS {CONSTRAINT}"))
        connection.execute(
            text(
                f"ALTER TABLE pairing_code ADD CONSTRAINT {CONSTRAINT} CHECK "
                "(purpose IN ('gateway', 'sensor', 'legacy') AND "
                "(purpose != 'legacy' OR used_at IS NOT NULL))"
            )
        )


def pairing_status(engine):
    with engine.connect() as connection:
        columns = inspect(connection).get_columns("pairing_code")
        purpose = next((c for c in columns if c["name"] == "purpose"), None)
        if not purpose or purpose["nullable"] or purpose["default"] is not None:
            raise ValueError("Prepare typed pairing before release")
        checks = inspect(connection).get_check_constraints("pairing_code")
        if not any(c["name"] == CONSTRAINT for c in checks):
            raise ValueError("Prepare typed pairing before release")
        return connection.execute(
            text("SELECT purpose, count(*) FROM pairing_code GROUP BY purpose ORDER BY purpose")
        ).all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "status"])
    args = parser.parse_args()
    from app.database import engine

    if args.operation == "prepare":
        prepare_pairing(engine)
        print("Typed pairing prepared; untyped codes invalidated")
    else:
        for purpose, count in pairing_status(engine):
            print(f"{purpose}: {count}")


if __name__ == "__main__":
    main()
