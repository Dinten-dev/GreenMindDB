"""Prepare public capability uniqueness without altering Alembic history.

Drain old public-access workers before preparation. Old validators ignore live
parent state. Run with the matching application environment, never on startup.
"""

import argparse

from sqlalchemy import text

from app.models.observation import PlantObservationAccess

INDEX = "uq_plant_observation_access_active_plant"


def prepare_observation_access(engine):
    if engine.dialect.name != "postgresql":
        raise ValueError("Public access upgrade requires PostgreSQL")
    with engine.begin() as connection:
        connection.execute(
            text(
                "LOCK TABLE plant_observation_access, plant_observation_session IN ACCESS EXCLUSIVE MODE"
            )
        )
        # No arbitrary winner: every active grant of an ambiguous plant expires.
        connection.execute(
            text(
                "UPDATE plant_observation_access SET is_active = false, "
                "revoked_at = COALESCE(revoked_at, clock_timestamp()) "
                "WHERE is_active AND (revoked_at IS NOT NULL OR plant_id IN "
                "(SELECT plant_id FROM plant_observation_access WHERE is_active "
                "GROUP BY plant_id HAVING count(*) > 1))"
            )
        )
        connection.execute(
            text(
                "UPDATE plant_observation_session s SET is_active = false "
                "WHERE s.is_active AND NOT EXISTS "
                "(SELECT 1 FROM plant_observation_access a WHERE a.id = s.access_id "
                "AND a.plant_id = s.plant_id AND a.is_active AND a.revoked_at IS NULL)"
            )
        )
        # Recreate only our owned index; do not trust its name alone.
        connection.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
        index = next(i for i in PlantObservationAccess.__table__.indexes if i.name == INDEX)
        index.create(connection)


def observation_access_status(engine):
    with engine.connect() as connection:
        ready = connection.execute(
            text(
                "SELECT i.indisunique AND i.indisvalid AND i.indisready "
                "AND i.indnkeyatts = 1 AND i.indnatts = 1 "
                "AND i.indrelid = to_regclass('plant_observation_access') "
                "AND a.attname = 'plant_id' "
                "AND pg_get_expr(i.indpred, i.indrelid) = 'is_active' "
                "FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = i.indkey[0] WHERE i.indexrelid = to_regclass(:name)"
            ),
            {"name": INDEX},
        ).scalar_one_or_none()
        if ready is not True:
            raise ValueError("Prepare public observation access before release")
        return connection.execute(
            text(
                "SELECT is_active, count(*) FROM plant_observation_access "
                "GROUP BY is_active ORDER BY is_active"
            )
        ).all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "status"])
    args = parser.parse_args()
    from app.database import engine

    if args.operation == "prepare":
        prepare_observation_access(engine)
        print("Public access prepared; ambiguous grants and invalid sessions deactivated")
    else:
        for active, count in observation_access_status(engine):
            print(f"{'active' if active else 'inactive'}: {count}")


if __name__ == "__main__":
    main()
