"""Add biosignal tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-07 19:25:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bio_session",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_mac", sa.String(length=17), nullable=False),
        sa.Column("gateway_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hardware_model", sa.String(length=50), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_samples", sa.Integer(), nullable=False),
        sa.Column("invalid_samples", sa.Integer(), nullable=False),
        sa.Column("raw_storage_key", sa.String(length=500), nullable=True),
        sa.Column("wav_storage_key", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bio_session_sensor_mac"), "bio_session", ["sensor_mac"], unique=False)

    op.create_table(
        "bio_aggregate",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mean_mv", sa.Float(), nullable=False),
        sa.Column("min_mv", sa.Float(), nullable=False),
        sa.Column("max_mv", sa.Float(), nullable=False),
        sa.Column("samples_total", sa.Integer(), nullable=False),
        sa.Column("samples_invalid", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["bio_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bio_aggregate_session_id"), "bio_aggregate", ["session_id"], unique=False)
    op.create_index(op.f("ix_bio_aggregate_timestamp"), "bio_aggregate", ["timestamp"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bio_aggregate_timestamp"), table_name="bio_aggregate")
    op.drop_index(op.f("ix_bio_aggregate_session_id"), table_name="bio_aggregate")
    op.drop_table("bio_aggregate")
    
    op.drop_index(op.f("ix_bio_session_sensor_mac"), table_name="bio_session")
    op.drop_table("bio_session")
