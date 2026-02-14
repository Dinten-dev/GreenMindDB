"""plant_state_1hz_table

Revision ID: 005
Revises: 004
Create Date: 2026-02-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Plant State 1Hz Table - ML-ready resampled data
    op.create_table('plant_state_1hz',
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('species_id', sa.Integer(), sa.ForeignKey('species.id', ondelete='CASCADE'), nullable=False),
        sa.Column('air_temperature_c', sa.Float(), nullable=True),
        sa.Column('rel_humidity_pct', sa.Float(), nullable=True),
        sa.Column('light_ppfd', sa.Float(), nullable=True),
        sa.Column('soil_moisture_pct', sa.Float(), nullable=True),
        sa.Column('soil_ph', sa.Float(), nullable=True),
        sa.Column('bio_voltage_mean', sa.Float(), nullable=True),
        sa.Column('bio_voltage_std', sa.Float(), nullable=True),
        sa.Column('quality_flags', postgresql.JSONB(), server_default='{}', nullable=True),
        sa.PrimaryKeyConstraint('species_id', 'timestamp')
    )
    
    # Create indexes for efficient querying
    op.create_index('ix_plant_state_1hz_timestamp', 'plant_state_1hz', ['timestamp'], unique=False)
    op.create_index('ix_plant_state_1hz_species_timestamp', 'plant_state_1hz', ['species_id', sa.text('timestamp DESC')], unique=False)
    
    # Convert to TimescaleDB hypertable for efficient time-series storage
    op.execute("SELECT create_hypertable('plant_state_1hz', 'timestamp', if_not_exists => TRUE)")
    
    # 2. Resampling State Table - Track pipeline progress per species
    op.create_table('resampling_state',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('species_id', sa.Integer(), sa.ForeignKey('species.id', ondelete='CASCADE'), nullable=False),
        sa.Column('last_processed_ts', sa.TIMESTAMP(timezone=True), server_default=sa.text("'1970-01-01T00:00:00Z'::timestamptz"), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('species_id', name='uq_resampling_state_species')
    )


def downgrade() -> None:
    op.drop_table('resampling_state')
    op.drop_index('ix_plant_state_1hz_species_timestamp', table_name='plant_state_1hz')
    op.drop_index('ix_plant_state_1hz_timestamp', table_name='plant_state_1hz')
    op.drop_table('plant_state_1hz')
