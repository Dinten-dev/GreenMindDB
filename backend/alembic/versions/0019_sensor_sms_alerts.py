"""Add sms_alerts_enabled to sensor

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-07 15:26:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("sensor", sa.Column("sms_alerts_enabled", sa.Boolean(), server_default="true", nullable=False))

def downgrade():
    op.drop_column("sensor", "sms_alerts_enabled")
