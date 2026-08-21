"""Add gateway remote management tables

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-14 18:30:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New columns on gateway table ─────────────────────────────
    op.add_column("gateway", sa.Column("app_version", sa.String(50), nullable=True))
    op.add_column("gateway", sa.Column("config_version", sa.String(50), nullable=True))
    op.add_column("gateway", sa.Column("agent_version", sa.String(50), nullable=True))
    op.add_column(
        "gateway",
        sa.Column("rollout_ring", sa.String(50), nullable=True, server_default="stable"),
    )
    op.add_column(
        "gateway",
        sa.Column("maintenance_mode", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "gateway",
        sa.Column("blocked", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column("gateway", sa.Column("os_version", sa.String(100), nullable=True))
    op.add_column("gateway", sa.Column("disk_free_mb", sa.Integer(), nullable=True))
    op.add_column("gateway", sa.Column("update_download_status", sa.String(20), nullable=True))
    op.add_column("gateway", sa.Column("update_apply_status", sa.String(20), nullable=True))
    op.add_column("gateway", sa.Column("signature_status", sa.String(20), nullable=True))

    # ── gateway_app_release ──────────────────────────────────────
    op.create_table(
        "gateway_app_release",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("artifact_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("channel", sa.String(20), nullable=False, server_default="'stable'"),
        sa.Column("min_version", sa.String(50), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_app_release_version",
        "gateway_app_release",
        ["version"],
        unique=True,
    )

    # ── gateway_config_release ───────────────────────────────────
    op.create_table(
        "gateway_config_release",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("config_payload", postgresql.JSONB(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="'1'"),
        sa.Column("compatible_app_min", sa.String(50), nullable=True),
        sa.Column("compatible_app_max", sa.String(50), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_config_release_version",
        "gateway_config_release",
        ["version"],
        unique=True,
    )

    # ── gateway_desired_state ────────────────────────────────────
    op.create_table(
        "gateway_desired_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("desired_app_version", sa.String(50), nullable=True),
        sa.Column("desired_config_version", sa.String(50), nullable=True),
        sa.Column("maintenance_mode", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reboot_allowed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rollout_ring", sa.String(50), nullable=False, server_default="'stable'"),
        sa.Column("force_downgrade", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("update_window_start", sa.String(5), nullable=True),
        sa.Column("update_window_end", sa.String(5), nullable=True),
        sa.Column("update_timezone", sa.String(50), nullable=False, server_default="'UTC'"),
        sa.Column("allow_download_outside_window", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("allow_apply_outside_window", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allow_reboot_outside_window", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_desired_state_gateway_id",
        "gateway_desired_state",
        ["gateway_id"],
        unique=True,
    )

    # ── gateway_command ──────────────────────────────────────────
    op.create_table(
        "gateway_command",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gateway_command_gateway_id", "gateway_command", ["gateway_id"])

    # ── gateway_state_report ─────────────────────────────────────
    op.create_table(
        "gateway_state_report",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_version", sa.String(50), nullable=True),
        sa.Column("config_version", sa.String(50), nullable=True),
        sa.Column("agent_version", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("health_status", sa.String(20), nullable=True),
        sa.Column("disk_free_mb", sa.Integer(), nullable=True),
        sa.Column("cpu_temp_c", sa.Float(), nullable=True),
        sa.Column("ram_usage_pct", sa.Float(), nullable=True),
        sa.Column("uptime_seconds", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("update_download_status", sa.String(20), nullable=True),
        sa.Column("update_apply_status", sa.String(20), nullable=True),
        sa.Column("signature_status", sa.String(20), nullable=True),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gateway_state_report_gateway_id", "gateway_state_report", ["gateway_id"])
    op.create_index("ix_gateway_state_report_reported_at", "gateway_state_report", ["reported_at"])

    # ── gateway_update_log ───────────────────────────────────────
    op.create_table(
        "gateway_update_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_type", sa.String(20), nullable=False),
        sa.Column("from_version", sa.String(50), nullable=True),
        sa.Column("to_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateway.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gateway_update_log_gateway_id", "gateway_update_log", ["gateway_id"])


def downgrade() -> None:
    op.drop_index("ix_gateway_update_log_gateway_id", table_name="gateway_update_log")
    op.drop_table("gateway_update_log")
    op.drop_index("ix_gateway_state_report_reported_at", table_name="gateway_state_report")
    op.drop_index("ix_gateway_state_report_gateway_id", table_name="gateway_state_report")
    op.drop_table("gateway_state_report")
    op.drop_index("ix_gateway_command_gateway_id", table_name="gateway_command")
    op.drop_table("gateway_command")
    op.drop_index("ix_gateway_desired_state_gateway_id", table_name="gateway_desired_state")
    op.drop_table("gateway_desired_state")
    op.drop_index("ix_gateway_config_release_version", table_name="gateway_config_release")
    op.drop_table("gateway_config_release")
    op.drop_index("ix_gateway_app_release_version", table_name="gateway_app_release")
    op.drop_table("gateway_app_release")

    op.drop_column("gateway", "signature_status")
    op.drop_column("gateway", "update_apply_status")
    op.drop_column("gateway", "update_download_status")
    op.drop_column("gateway", "disk_free_mb")
    op.drop_column("gateway", "os_version")
    op.drop_column("gateway", "blocked")
    op.drop_column("gateway", "maintenance_mode")
    op.drop_column("gateway", "rollout_ring")
    op.drop_column("gateway", "agent_version")
    op.drop_column("gateway", "config_version")
    op.drop_column("gateway", "app_version")
