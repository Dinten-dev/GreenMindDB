"""Explicit member zone access, preserving access to existing zones.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='1s'")
    op.create_table(
        "zone_access",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zone.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Previously members could read every existing zone in their organization.
    # Preserve that access explicitly; new members/zones receive no implicit grant.
    op.execute("""INSERT INTO zone_access(user_id,zone_id)
        SELECT u.id,z.id FROM users u JOIN zone z ON z.organization_id=u.organization_id
        WHERE u.role='member'""")


def downgrade():
    raise RuntimeError(
        "Removing zone grants would broaden access. Restore the reviewed access policy first."
    )
