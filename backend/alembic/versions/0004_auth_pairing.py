"""auth and pairing updates

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-23 20:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003_ingest_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add is_verified to users
    op.add_column(
        "users", sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False)
    )

    # 2. Create email_verification table
    op.create_table(
        "email_verification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_verification_token"), "email_verification", ["token"], unique=True
    )

    # 3. Add created_by_user_id to pairing_code
    op.add_column(
        "pairing_code",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_pairing_code_user_id",
        "pairing_code",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # We need to handle existing rows if there are any. For now, since it's nullable=True above, we can set default if needed.
    # Actually, the model says nullable=False. Let's make it nullable=True initially, then alter it if needed, or just leave it nullable=True in migration.
    # We will leave nullable=True in DB but False in SQLAlchemy to avoid breaking existing dev databases.


def downgrade() -> None:
    op.drop_constraint("fk_pairing_code_user_id", "pairing_code", type_="foreignkey")
    op.drop_column("pairing_code", "created_by_user_id")
    op.drop_index(op.f("ix_email_verification_token"), table_name="email_verification")
    op.drop_table("email_verification")
    op.drop_column("users", "is_verified")
