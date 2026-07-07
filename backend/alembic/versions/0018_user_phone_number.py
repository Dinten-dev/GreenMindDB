"""Add phone_number to user

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-07 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(length=50), nullable=True))

def downgrade() -> None:
    op.drop_column("users", "phone_number")
