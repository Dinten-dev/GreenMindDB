"""Optimize aggregates, feature versions, and operations.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    aggregate_columns = (
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("sample_rate_hz", sa.Float(), nullable=True),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("rms", sa.Float(), nullable=True),
        sa.Column("standard_deviation", sa.Float(), nullable=True),
        sa.Column("minimum", sa.Float(), nullable=True),
        sa.Column("maximum", sa.Float(), nullable=True),
        sa.Column("p05", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("coverage_ratio", sa.Float(), nullable=True),
        sa.Column("source_boot_id", sa.BigInteger(), nullable=True),
        sa.Column("protocol_version", sa.Integer(), nullable=True),
        sa.Column("firmware_version", sa.String(50), nullable=True),
        sa.Column("calibration_version", sa.String(50), nullable=True),
        sa.Column("quality_valid_count", sa.Integer(), nullable=True),
        sa.Column("quality_lead_off_count", sa.Integer(), nullable=True),
        sa.Column("quality_rail_high_count", sa.Integer(), nullable=True),
        sa.Column("quality_rail_low_count", sa.Integer(), nullable=True),
        sa.Column("quality_jump_count", sa.Integer(), nullable=True),
        sa.Column("quality_recovery_count", sa.Integer(), nullable=True),
    )
    for column in aggregate_columns:
        op.add_column("sensor_reading", column)

    op.add_column(
        "wav_file",
        sa.Column(
            "pcm_encoding_version",
            sa.String(50),
            server_default="unsigned-mv-linear-int16-v1",
            nullable=False,
        ),
    )
    op.add_column(
        "wav_file",
        sa.Column("pcm_scale_mv", sa.Float(), server_default="0.100711081270", nullable=False),
    )
    op.add_column(
        "wav_file", sa.Column("pcm_offset_mv", sa.Float(), server_default="0", nullable=False)
    )
    op.add_column(
        "wav_file",
        sa.Column(
            "calibration_version",
            sa.String(50),
            server_default="nominal-adc-3v3-v1",
            nullable=False,
        ),
    )

    op.add_column(
        "wav_feature",
        sa.Column(
            "calibration_version",
            sa.String(50),
            server_default="nominal-adc-3v3-v1",
            nullable=False,
        ),
    )
    op.add_column(
        "wav_feature",
        sa.Column("parameter_hash", sa.String(64), server_default="legacy", nullable=False),
    )
    op.add_column(
        "wav_feature",
        sa.Column("data_quality_status", sa.String(20), server_default="unknown", nullable=False),
    )
    op.add_column(
        "wav_feature",
        sa.Column("technical_fault_score", sa.Float(), server_default="0", nullable=False),
    )
    op.add_column(
        "wav_feature",
        sa.Column(
            "technical_fault_reasons",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "wav_feature",
        sa.Column("biological_candidate_score", sa.Float(), server_default="0", nullable=False),
    )
    op.add_column(
        "wav_feature",
        sa.Column(
            "biological_candidate_reasons",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "wav_feature",
        sa.Column(
            "source_quality_counts",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )

    op.create_table(
        "wav_feature_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wav_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extractor_version", sa.String(50), nullable=False),
        sa.Column("calibration_version", sa.String(50), nullable=False),
        sa.Column("parameter_hash", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("feature_checksum", sa.String(64), nullable=False),
        sa.Column("feature_payload", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["wav_file_id"], ["wav_file.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "wav_file_id",
            "extractor_version",
            "calibration_version",
            "parameter_hash",
            name="uq_wav_feature_version_identity",
        ),
    )
    op.create_index("ix_wav_feature_version_wav_file_id", "wav_feature_version", ["wav_file_id"])
    op.create_index(
        "ix_wav_file_feature_status_started_at",
        "wav_file",
        ["feature_status", "started_at"],
    )

    op.create_table(
        "retention_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dry_run", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "retention_run_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("affected_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["retention_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retention_run_item_run_id", "retention_run_item", ["run_id"])
    op.create_table(
        "background_worker_heartbeat",
        sa.Column("worker_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("worker_name"),
    )

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ingest_log_created_at_brin "
            "ON ingest_log USING brin (created_at)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ingest_log_created_at_brin")
    op.drop_table("background_worker_heartbeat")
    op.drop_table("retention_run_item")
    op.drop_table("retention_run")
    op.drop_index("ix_wav_file_feature_status_started_at", table_name="wav_file")
    op.drop_table("wav_feature_version")
    for column in (
        "source_quality_counts",
        "biological_candidate_reasons",
        "biological_candidate_score",
        "technical_fault_reasons",
        "technical_fault_score",
        "data_quality_status",
        "parameter_hash",
        "calibration_version",
    ):
        op.drop_column("wav_feature", column)
    for column in ("calibration_version", "pcm_offset_mv", "pcm_scale_mv", "pcm_encoding_version"):
        op.drop_column("wav_file", column)
    for column in (
        "quality_recovery_count",
        "quality_jump_count",
        "quality_rail_low_count",
        "quality_rail_high_count",
        "quality_lead_off_count",
        "quality_valid_count",
        "calibration_version",
        "firmware_version",
        "protocol_version",
        "source_boot_id",
        "coverage_ratio",
        "p95",
        "p05",
        "maximum",
        "minimum",
        "standard_deviation",
        "rms",
        "median",
        "sample_rate_hz",
        "sample_count",
    ):
        op.drop_column("sensor_reading", column)
