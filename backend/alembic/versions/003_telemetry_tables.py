"""telemetry_tables

Revision ID: 003
Revises: 002
Create Date: 2026-02-06 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002_add_auth_audit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Device Table
    op.create_table('device',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('device_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('api_key_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # 2. Telemetry Channel Table
    op.create_table('telemetry_channel',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('species_id', sa.Integer(), nullable=False),
        sa.Column('metric_id', sa.Integer(), nullable=False),
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('channel_key', sa.String(length=100), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['device.id'], ),
        sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ),
        sa.ForeignKeyConstraint(['species_id'], ['species.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_key')
    )
    op.create_index(op.f('ix_telemetry_channel_species_id'), 'telemetry_channel', ['species_id'], unique=False)

    # 3. Telemetry Measurement Table (Hypertable)
    op.create_table('telemetry_measurement',
        sa.Column('time', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('quality', sa.SmallInteger(), server_default='0', nullable=True),
        sa.ForeignKeyConstraint(['channel_id'], ['telemetry_channel.id'], )
        # Primary key is composite (time, channel_id) usually for hypertables, but simple PK constraint might conflict with hypertable partitioning if not handled carefully.
        # Ideally, we don't define a generic PK constraint here if we want standard hypertable behavior, or we include 'time' in it.
    )
    
    # Create index on time and channel_id (descending time is common for queries)
    op.create_index('ix_telemetry_measurement_time_channel', 'telemetry_measurement', ['channel_id', sa.text('time DESC')], unique=False)

    # Convert to Hypertable
    # Note: ensure timescaledb extension is created. It usually is in the docker image init, but good to be sure?
    # Actually, the user requirement says "Erweitere docker-compose um TimescaleDB".
    # We will assume the extension is enabled in the DB or we enable it here.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    op.execute("SELECT create_hypertable('telemetry_measurement', 'time')")


def downgrade() -> None:
    op.drop_table('telemetry_measurement')
    op.drop_index(op.f('ix_telemetry_channel_species_id'), table_name='telemetry_channel')
    op.drop_table('telemetry_channel')
    op.drop_table('device')
