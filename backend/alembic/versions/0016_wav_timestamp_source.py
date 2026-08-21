"""Add timestamp_source column to wav_file

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-12 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wav_file",
        sa.Column(
            "timestamp_source",
            sa.String(length=20),
            nullable=False,
            server_default="filename",
        ),
    )


def downgrade() -> None:
    op.drop_column("wav_file", "timestamp_source")
