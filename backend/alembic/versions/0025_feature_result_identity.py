"""Acknowledge the separately prepared online feature identity capability.

Revision ID: 0025
Revises: 0024

On production head0023 use the explicit feature-only online operation, which
keeps0023 and authentication unchanged. Never upgrade to0025 to obtain that
feature capability: it would request the separate0024 authentication migration.
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    from app.raw_archive.feature_identity_upgrade import completed

    connection = op.get_bind()

    class Catalog:
        @property
        def backend_pid(self):
            return connection.connection.driver_connection.get_backend_pid()

        def __call__(self, statement, parameters=()):
            return list(connection.exec_driver_sql(statement, parameters).mappings())

    completed(Catalog())  # Metadata only; no table/index build, auth DDL or stamp.


def downgrade():
    raise RuntimeError(
        "The previous identity cannot retain distinct feature results. "
        "Keep the result history; no automatic downgrade or deletion is supported."
    )
