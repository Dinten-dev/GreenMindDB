"""convert sensor_reading to TimescaleDB hypertable

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-24 15:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Convert the existing sensor_reading table into a TimescaleDB hypertable.
    #    migrate_data => true moves any existing rows into the new chunk structure.
    op.execute(
        "SELECT create_hypertable('sensor_reading', 'timestamp', migrate_data => true, if_not_exists => true)"
    )

    # 2. Add a compression policy: automatically compress chunks older than 7 days.
    op.execute(
        "ALTER TABLE sensor_reading SET ("
        "  timescaledb.compress,"
        "  timescaledb.compress_segmentby = 'sensor_id'"
        ")"
    )
    op.execute(
        "SELECT add_compression_policy('sensor_reading', INTERVAL '7 days', if_not_exists => true)"
    )


def downgrade() -> None:
    # Remove compression policy (best-effort; decompress must happen manually)
    op.execute("SELECT remove_compression_policy('sensor_reading', if_exists => true)")
    # Note: Converting a hypertable back to a regular table is not directly
    # supported by TimescaleDB. A full table rebuild would be required.
