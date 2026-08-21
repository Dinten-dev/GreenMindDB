"""Add ingest_log and measurement_id

Revision ID: 0003_ingest_log
Revises: 0002_greenmind_v3
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_ingest_log"
down_revision = "0002_greenmind_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Ingest Log ─────────────────────────────────────────
    op.create_table(
        "ingest_log",
        sa.Column("measurement_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="success"),
        sa.Column("raw_file_reference", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ingest_log_device_id", "ingest_log", ["device_id"])

    # ── Sensor Reading ─────────────────────────────────────
    op.execute("ALTER TABLE sensor_reading ADD COLUMN measurement_id UUID NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE sensor_reading DROP COLUMN IF EXISTS measurement_id")
    op.drop_table("ingest_log")
