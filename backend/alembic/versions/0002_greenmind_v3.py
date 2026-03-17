"""GreenMind v3 – complete schema rebuild.

Revision ID: 0002_greenmind_v3
Create Date: 2026-02-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0002_greenmind_v3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ─────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "timescaledb"')

    # ── Organization ───────────────────────────────────────
    op.create_table(
        "organization",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # ── Users ──────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("owner", "admin", "member", name="user_role"), nullable=False, server_default="member"),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ── Greenhouse ─────────────────────────────────────────
    op.create_table(
        "greenhouse",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_greenhouse_org_id", "greenhouse", ["organization_id"])

    # ── Device ─────────────────────────────────────────────
    op.create_table(
        "device",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=True),
        sa.Column("serial", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("type", sa.String(50), nullable=False, server_default="esp32"),
        sa.Column("fw_version", sa.String(50), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("api_key_hash", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_greenhouse_id", "device", ["greenhouse_id"])

    # ── Sensor ─────────────────────────────────────────────
    op.create_table(
        "sensor",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
    )
    op.create_index("ix_sensor_device_id", "sensor", ["device_id"])

    # ── Pairing Code ───────────────────────────────────────
    op.create_table(
        "pairing_code",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(8), nullable=False, unique=True),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("device.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_pairing_code_code", "pairing_code", ["code"])

    # ── Sensor Reading (TimescaleDB hypertable) ────────────
    op.execute("""
        CREATE TABLE sensor_reading (
            timestamp   TIMESTAMPTZ NOT NULL,
            sensor_id   UUID        NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            unit        VARCHAR(20) NOT NULL
        )
    """)
    op.execute("SELECT create_hypertable('sensor_reading', 'timestamp')")
    op.execute("CREATE INDEX ix_reading_sensor_time ON sensor_reading (sensor_id, timestamp DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sensor_reading CASCADE")
    for t in ["pairing_code", "sensor", "device", "greenhouse", "users", "organization"]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP TYPE IF EXISTS user_role")
