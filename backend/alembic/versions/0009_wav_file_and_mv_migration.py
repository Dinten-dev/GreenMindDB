"""Add wav_file table and migrate sensor readings V to mV.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create wav_file table
    op.create_table(
        "wav_file",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sensor_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "gateway_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("sensor_mac", sa.String(20), nullable=False, index=True),
        sa.Column("s3_key", sa.String(500), nullable=False, unique=True),
        sa.Column("sample_rate", sa.Integer(), nullable=False, server_default="380"),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # 2. Migrate existing sensor readings from V to mV
    op.execute(
        "UPDATE sensor_reading SET value = value * 1000, unit = 'mV' WHERE unit = 'V'"
    )


def downgrade() -> None:
    # Reverse the unit migration
    op.execute(
        "UPDATE sensor_reading SET value = value / 1000, unit = 'V' WHERE unit = 'mV'"
    )

    # Drop wav_file table
    op.drop_table("wav_file")
