"""Add firmware OTA tables

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-12 20:20:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firmware_release",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("board_type", sa.String(length=50), nullable=False),
        sa.Column("hardware_revision", sa.String(length=50), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("min_version", sa.String(length=50), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_firmware_release_version"), "firmware_release", ["version"], unique=False)

    op.create_table(
        "rollout_policy",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("zone_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canary_percentage", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["firmware_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zone.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "firmware_report",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gateway_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["firmware_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

def downgrade() -> None:
    op.drop_table("firmware_report")
    op.drop_table("rollout_policy")
    op.drop_index(op.f("ix_firmware_release_version"), table_name="firmware_release")
    op.drop_table("firmware_release")
