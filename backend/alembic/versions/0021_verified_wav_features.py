"""Add verified WAV features and archive lifecycle state.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wav_file", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.add_column(
        "wav_file",
        sa.Column("feature_status", sa.String(20), server_default="pending", nullable=False),
    )
    op.add_column(
        "wav_file",
        sa.Column("feature_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("wav_file", sa.Column("feature_error", sa.String(500), nullable=True))
    op.add_column(
        "wav_file", sa.Column("feature_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "wav_file", sa.Column("feature_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "wav_file", sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_wav_file_raw_deleted_at", "wav_file", ["raw_deleted_at"])

    op.create_table(
        "wav_feature",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wav_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_mac", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extractor_version", sa.String(50), nullable=False),
        sa.Column("feature_checksum", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("value_unit", sa.String(20), nullable=False),
        sa.Column("mean", sa.Float(), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("rms", sa.Float(), nullable=False),
        sa.Column("standard_deviation", sa.Float(), nullable=False),
        sa.Column("minimum", sa.Float(), nullable=False),
        sa.Column("maximum", sa.Float(), nullable=False),
        sa.Column("quantiles", sa.JSON(), nullable=False),
        sa.Column("outlier_count", sa.BigInteger(), nullable=False),
        sa.Column("outlier_ratio", sa.Float(), nullable=False),
        sa.Column("clipping_count", sa.BigInteger(), nullable=False),
        sa.Column("clipping_ratio", sa.Float(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("timing_status", sa.String(20), nullable=False),
        sa.Column("missing_duration_seconds", sa.Float(), nullable=False),
        sa.Column("flatline_count", sa.Integer(), nullable=False),
        sa.Column("flatline_seconds", sa.Float(), nullable=False),
        sa.Column("sequence_observations", sa.Integer(), nullable=False),
        sa.Column("sequence_gap_count", sa.Integer(), nullable=False),
        sa.Column("sequence_missing_count", sa.BigInteger(), nullable=False),
        sa.Column("sequence_reset_count", sa.Integer(), nullable=False),
        sa.Column("source_dropped_samples_delta", sa.BigInteger(), nullable=True),
        sa.Column("spectral_energy_total", sa.Float(), nullable=False),
        sa.Column("dominant_frequency_hz", sa.Float(), nullable=False),
        sa.Column("spectral_bands", sa.JSON(), nullable=False),
        sa.Column("is_anomaly", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("anomaly_reasons", sa.JSON(), nullable=False),
        sa.Column("anomaly_s3_key", sa.String(500), nullable=True),
        sa.Column("anomaly_sha256", sa.String(64), nullable=True),
        sa.Column("anomaly_file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("anomaly_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anomaly_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flac_s3_key", sa.String(500), nullable=True),
        sa.Column("flac_sha256", sa.String(64), nullable=True),
        sa.Column("flac_file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("flac_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flac_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["wav_file_id"], ["wav_file.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("anomaly_s3_key"),
        sa.UniqueConstraint("flac_s3_key"),
        sa.UniqueConstraint("wav_file_id"),
    )
    op.create_index("ix_wav_feature_anomaly_expires_at", "wav_feature", ["anomaly_expires_at"])
    op.create_index("ix_wav_feature_flac_expires_at", "wav_feature", ["flac_expires_at"])
    op.create_index("ix_wav_feature_gateway_id", "wav_feature", ["gateway_id"])
    op.create_index("ix_wav_feature_is_anomaly", "wav_feature", ["is_anomaly"])
    op.create_index("ix_wav_feature_sensor_id", "wav_feature", ["sensor_id"])
    op.create_index("ix_wav_feature_sensor_mac", "wav_feature", ["sensor_mac"])
    op.create_index("ix_wav_feature_started_at", "wav_feature", ["started_at"])
    op.create_index("ix_wav_feature_verified_at", "wav_feature", ["verified_at"])
    op.create_index("ix_wav_feature_wav_file_id", "wav_feature", ["wav_file_id"])


def downgrade() -> None:
    op.drop_table("wav_feature")
    op.drop_index("ix_wav_file_raw_deleted_at", table_name="wav_file")
    op.drop_column("wav_file", "raw_deleted_at")
    op.drop_column("wav_file", "feature_verified_at")
    op.drop_column("wav_file", "feature_started_at")
    op.drop_column("wav_file", "feature_error")
    op.drop_column("wav_file", "feature_attempts")
    op.drop_column("wav_file", "feature_status")
    op.drop_column("wav_file", "content_sha256")
