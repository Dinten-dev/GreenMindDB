"""Add gateway/sensor hierarchy, replacing flat device model.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Drop old tables in dependency order ─────────────────
    # sensor_reading is a hypertable; must drop before sensor
    op.execute("DROP TABLE IF EXISTS sensor_reading CASCADE")
    op.execute("DROP TABLE IF EXISTS ingest_log CASCADE")
    op.execute("DROP TABLE IF EXISTS pairing_code CASCADE")
    op.execute("DROP TABLE IF EXISTS sensor CASCADE")
    op.execute("DROP TABLE IF EXISTS device CASCADE")

    # ── Create gateway table ────────────────────────────────
    op.create_table(
        "gateway",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "greenhouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("greenhouse.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("hardware_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("local_ip", sa.String(45), nullable=True),
        sa.Column("fw_version", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("api_key_hash", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Create sensor table (ESP32 physical modules) ────────
    op.create_table(
        "sensor",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "gateway_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gateway.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("mac_address", sa.String(17), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("sensor_type", sa.String(50), nullable=False, server_default="generic"),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Recreate pairing_code with gateway FK ───────────────
    op.create_table(
        "pairing_code",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(8), nullable=False, unique=True, index=True),
        sa.Column(
            "greenhouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("greenhouse.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "gateway_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gateway.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    # ── Recreate ingest_log with gateway FK ─────────────────
    op.create_table(
        "ingest_log",
        sa.Column(
            "measurement_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "gateway_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gateway.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="success"),
        sa.Column("raw_file_reference", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── Recreate sensor_reading hypertable with kind column ─
    op.execute(
        """
        CREATE TABLE sensor_reading (
            "timestamp" TIMESTAMPTZ NOT NULL,
            sensor_id   UUID        NOT NULL,
            kind        VARCHAR(100) NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            unit        VARCHAR(20) NOT NULL,
            measurement_id UUID,
            PRIMARY KEY ("timestamp", sensor_id, kind)
        )
    """
    )
    op.execute("SELECT create_hypertable('sensor_reading', 'timestamp')")
    op.create_index("ix_sensor_reading_sensor_id", "sensor_reading", ["sensor_id"])
    op.create_index("ix_sensor_reading_measurement_id", "sensor_reading", ["measurement_id"])


def downgrade() -> None:
    # Downgrade is destructive — drops new tables
    op.execute("DROP TABLE IF EXISTS sensor_reading CASCADE")
    op.execute("DROP TABLE IF EXISTS ingest_log CASCADE")
    op.execute("DROP TABLE IF EXISTS pairing_code CASCADE")
    op.execute("DROP TABLE IF EXISTS sensor CASCADE")
    op.execute("DROP TABLE IF EXISTS gateway CASCADE")
