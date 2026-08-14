"""Add plant evaluation table for structured ML-ready scoring

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-02 11:43:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plant_evaluation",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("zone_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Numerical scores (1–5)
        sa.Column("overall_score", sa.SmallInteger(), nullable=False),
        sa.Column("color_score", sa.SmallInteger(), nullable=False),
        sa.Column("structure_score", sa.SmallInteger(), nullable=False),
        sa.Column("growth_score", sa.SmallInteger(), nullable=False),
        sa.Column("water_score", sa.SmallInteger(), nullable=False),
        # Anomaly bitmask
        sa.Column("anomalies_vector", sa.SmallInteger(), nullable=False, server_default="0"),
        # Raw choice keys for traceability
        sa.Column("color_raw", sa.String(length=50), nullable=False),
        sa.Column("structure_raw", sa.String(length=50), nullable=False),
        sa.Column("growth_raw", sa.String(length=50), nullable=False),
        sa.Column("water_raw", sa.String(length=50), nullable=False),
        sa.Column("anomalies_raw", sa.String(length=200), nullable=False),
        # Computed
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("detail_notes", sa.Text(), nullable=True),
        # Metadata
        sa.Column("used_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Constraints
        sa.ForeignKeyConstraint(["plant_id"], ["plant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["zone_id"], ["zone.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["plant_observation_session.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("overall_score BETWEEN 1 AND 5", name="ck_eval_overall_range"),
        sa.CheckConstraint("color_score BETWEEN 1 AND 5", name="ck_eval_color_range"),
        sa.CheckConstraint("structure_score BETWEEN 1 AND 5", name="ck_eval_structure_range"),
        sa.CheckConstraint("growth_score BETWEEN 1 AND 5", name="ck_eval_growth_range"),
        sa.CheckConstraint("water_score BETWEEN 1 AND 5", name="ck_eval_water_range"),
    )
    op.create_index(op.f("ix_plant_evaluation_plant_id"), "plant_evaluation", ["plant_id"])
    op.create_index(op.f("ix_plant_evaluation_evaluated_at"), "plant_evaluation", ["evaluated_at"])


def downgrade() -> None:
    op.drop_table("plant_evaluation")
