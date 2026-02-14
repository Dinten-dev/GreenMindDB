"""enforce_unique_channel

Revision ID: 004
Revises: 003
Create Date: 2026-02-06 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop existing constraint if it exists (it was separate channel_key unique)
    op.drop_constraint('telemetry_channel_channel_key_key', 'telemetry_channel', type_='unique')
    
    # 2. Cleanup duplicates if any (keep latest or arbitrary)
    # Since we are in dev, we can truncate or delete duplicates. 
    # For now, let's assume we want to keep one.
    # A simple way for dev: DELETE FROM telemetry_channel a USING telemetry_channel b WHERE a.id < b.id AND a.species_id = b.species_id AND a.metric_id = b.metric_id;
    op.execute("""
        DELETE FROM telemetry_channel a USING telemetry_channel b 
        WHERE a.id < b.id AND a.species_id = b.species_id AND a.metric_id = b.metric_id
    """)

    # 3. Create new Unique Constraint
    op.create_unique_constraint('uq_telemetry_channel_species_metric', 'telemetry_channel', ['species_id', 'metric_id'])


def downgrade() -> None:
    op.drop_constraint('uq_telemetry_channel_species_metric', 'telemetry_channel', type_='unique')
    op.create_unique_constraint('telemetry_channel_channel_key_key', 'telemetry_channel', ['channel_key'])
