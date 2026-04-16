"""Add plant observation models

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-16 15:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plant
    op.create_table(
        "plant",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("zone_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plant_code", sa.String(length=100), nullable=True),
        sa.Column("species", sa.String(length=200), nullable=True),
        sa.Column("cultivar", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("planted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zone.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_organization_id"), "plant", ["organization_id"])
    op.create_index(op.f("ix_plant_zone_id"), "plant", ["zone_id"])

    # 2. plant_sensor_assignment
    op.create_table(
        "plant_sensor_assignment",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_sensor_assignment_plant_id"), "plant_sensor_assignment", ["plant_id"])
    op.create_index(op.f("ix_plant_sensor_assignment_sensor_id"), "plant_sensor_assignment", ["sensor_id"])

    # 3. plant_observation_access
    op.create_table(
        "plant_observation_access",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_observation_access_plant_id"), "plant_observation_access", ["plant_id"])
    op.create_index(op.f("ix_plant_observation_access_public_id"), "plant_observation_access", ["public_id"], unique=True)

    # 4. plant_observation_session
    op.create_table(
        "plant_observation_session",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_token", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("used_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["access_id"], ["plant_observation_access.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_observation_session_plant_id"), "plant_observation_session", ["plant_id"])
    op.create_index(op.f("ix_plant_observation_session_access_id"), "plant_observation_session", ["access_id"])
    op.create_index(op.f("ix_plant_observation_session_session_token"), "plant_observation_session", ["session_token"], unique=True)

    # 5. plant_observation
    op.create_table(
        "plant_observation",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("zone_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observed_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("wellbeing_score", sa.Integer(), nullable=False),
        sa.Column("stress_score", sa.Integer(), nullable=True),
        sa.Column("plant_condition", sa.String(length=50), nullable=False),
        sa.Column("leaf_droop", sa.Boolean(), nullable=True),
        sa.Column("leaf_color", sa.String(length=100), nullable=True),
        sa.Column("spots_present", sa.Boolean(), nullable=True),
        sa.Column("soil_condition", sa.String(length=100), nullable=True),
        sa.Column("suspected_stress_type", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["zone_id"], ["zone.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["observation_session_id"], ["plant_observation_session.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["observed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_observation_plant_id"), "plant_observation", ["plant_id"])

    # 6. plant_observation_photo
    op.create_table(
        "plant_observation_photo",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["observation_id"], ["plant_observation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plant_observation_photo_observation_id"), "plant_observation_photo", ["observation_id"])


def downgrade() -> None:
    op.drop_table("plant_observation_photo")
    op.drop_table("plant_observation")
    op.drop_table("plant_observation_session")
    op.drop_table("plant_observation_access")
    op.drop_table("plant_sensor_assignment")
    op.drop_table("plant")
