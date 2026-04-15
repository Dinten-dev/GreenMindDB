"""Rename greenhouse to zone, add zone_type and geodata columns.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create zone_type enum
    zone_type_enum = sa.Enum(
        "GREENHOUSE", "OPEN_FIELD", "VERTICAL_FARM", "ORCHARD",
        name="zone_type",
    )
    zone_type_enum.create(op.get_bind(), checkfirst=True)

    # 2. Rename table greenhouse → zone
    op.rename_table("greenhouse", "zone")

    # 3. Add new columns to zone
    op.add_column(
        "zone",
        sa.Column(
            "zone_type",
            sa.Enum("GREENHOUSE", "OPEN_FIELD", "VERTICAL_FARM", "ORCHARD", name="zone_type"),
            nullable=False,
            server_default="GREENHOUSE",
        ),
    )
    op.add_column("zone", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("zone", sa.Column("longitude", sa.Float(), nullable=True))

    # 4. Rename FK column in gateway: greenhouse_id → zone_id
    op.alter_column("gateway", "greenhouse_id", new_column_name="zone_id")

    # 5. Rename FK column in pairing_code: greenhouse_id → zone_id
    op.alter_column("pairing_code", "greenhouse_id", new_column_name="zone_id")


def downgrade() -> None:
    # Reverse FK column renames
    op.alter_column("pairing_code", "zone_id", new_column_name="greenhouse_id")
    op.alter_column("gateway", "zone_id", new_column_name="greenhouse_id")

    # Drop new columns
    op.drop_column("zone", "longitude")
    op.drop_column("zone", "latitude")
    op.drop_column("zone", "zone_type")

    # Rename table back
    op.rename_table("zone", "greenhouse")

    # Drop enum
    sa.Enum(name="zone_type").drop(op.get_bind(), checkfirst=True)
