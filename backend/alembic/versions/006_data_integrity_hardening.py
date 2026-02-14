"""data_integrity_hardening

Revision ID: 006
Revises: 005
Create Date: 2026-02-08 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize existing text fields first.
    op.execute("UPDATE species SET common_name = btrim(common_name)")
    op.execute("UPDATE species SET latin_name = btrim(latin_name)")
    op.execute("UPDATE species SET category = btrim(category)")

    # Avoid unique-index creation failures if case-insensitive duplicates already exist.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, common_name,
                   row_number() OVER (PARTITION BY lower(common_name) ORDER BY id) AS rn
            FROM species
        )
        UPDATE species s
        SET common_name = s.common_name || ' (' || s.id || ')'
        FROM ranked r
        WHERE s.id = r.id AND r.rn > 1
        """
    )

    op.create_index(
        "ix_species_common_name_ci_unique",
        "species",
        [sa.text("lower(common_name)")],
        unique=True,
    )

    op.create_check_constraint(
        "ck_species_common_name_not_empty",
        "species",
        "length(btrim(common_name)) > 0",
    )
    op.create_check_constraint(
        "ck_species_category_not_empty",
        "species",
        "length(btrim(category)) > 0",
    )
    op.create_check_constraint(
        "ck_target_range_valid_bounds",
        "target_range",
        "optimal_high >= optimal_low",
    )
    op.create_check_constraint(
        "ck_source_url_http_https",
        "source",
        "url IS NULL OR url ~* '^https?://.+'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_source_url_http_https", "source", type_="check")
    op.drop_constraint("ck_target_range_valid_bounds", "target_range", type_="check")
    op.drop_constraint("ck_species_category_not_empty", "species", type_="check")
    op.drop_constraint("ck_species_common_name_not_empty", "species", type_="check")
    op.drop_index("ix_species_common_name_ci_unique", table_name="species")
