"""Add provisioning jobs table

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-07 09:29:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Create enum type first if it doesn't exist
    provisioning_status = postgresql.ENUM('pending', 'in_progress', 'completed', 'failed', name='provisioningstatus')
    provisioning_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "provisioning_jobs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mac_address", sa.String(), nullable=True),
        sa.Column("ssid", sa.String(), nullable=False),
        sa.Column("password", sa.String(), nullable=False),
        sa.Column("pairing_code", sa.String(length=6), nullable=False),
        sa.Column("status", postgresql.ENUM('pending', 'in_progress', 'completed', 'failed', name='provisioningstatus', create_type=False), server_default='pending', nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_provisioning_jobs_id"), "provisioning_jobs", ["id"])
    op.create_index(op.f("ix_provisioning_jobs_mac_address"), "provisioning_jobs", ["mac_address"])


def downgrade() -> None:
    op.drop_index(op.f("ix_provisioning_jobs_mac_address"), table_name="provisioning_jobs")
    op.drop_index(op.f("ix_provisioning_jobs_id"), table_name="provisioning_jobs")
    op.drop_table("provisioning_jobs")
    
    provisioning_status = postgresql.ENUM('pending', 'in_progress', 'completed', 'failed', name='provisioningstatus')
    provisioning_status.drop(op.get_bind(), checkfirst=True)
