"""Add WAV quality, gateway backlog, and source continuity metadata.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wav_file",
        sa.Column("coverage_ratio", sa.Float(), server_default="1", nullable=False),
    )
    op.add_column(
        "wav_file",
        sa.Column("timing_status", sa.String(20), server_default="complete", nullable=False),
    )

    op.add_column("gateway", sa.Column("wav_pending_files", sa.Integer(), nullable=True))
    op.add_column("gateway", sa.Column("wav_pending_bytes", sa.BigInteger(), nullable=True))
    op.add_column(
        "gateway",
        sa.Column("wav_oldest_pending_age_hours", sa.Float(), nullable=True),
    )
    op.add_column(
        "gateway",
        sa.Column("wav_last_upload_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gateway",
        sa.Column("wav_last_error_code", sa.String(100), nullable=True),
    )

    op.add_column(
        "sensor_reading",
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sensor_reading",
        sa.Column("source_uptime_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sensor_reading",
        sa.Column("source_dropped_samples_total", sa.BigInteger(), nullable=True),
    )

    # Migration 0007 recreated the hypertable and unintentionally removed
    # migration 0005's compression settings.
    op.execute(
        "ALTER TABLE sensor_reading SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'sensor_id,kind', "
        "timescaledb.compress_orderby = 'timestamp DESC'"
        ")"
    )
    op.execute(
        "SELECT add_compression_policy('sensor_reading', INTERVAL '7 days', if_not_exists => true)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('sensor_reading', if_exists => true)")
    op.drop_column("sensor_reading", "source_dropped_samples_total")
    op.drop_column("sensor_reading", "source_uptime_ms")
    op.drop_column("sensor_reading", "source_sequence")
    op.drop_column("gateway", "wav_last_error_code")
    op.drop_column("gateway", "wav_last_upload_at")
    op.drop_column("gateway", "wav_oldest_pending_age_hours")
    op.drop_column("gateway", "wav_pending_bytes")
    op.drop_column("gateway", "wav_pending_files")
    op.drop_column("wav_file", "timing_status")
    op.drop_column("wav_file", "coverage_ratio")
