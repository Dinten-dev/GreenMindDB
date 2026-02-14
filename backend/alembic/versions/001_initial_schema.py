"""Initial simplified schema

Revision ID: 001
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Species table
    op.create_table(
        'species',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('common_name', sa.String(100), nullable=False),
        sa.Column('latin_name', sa.String(100), nullable=False),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_species_id', 'species', ['id'])
    op.create_index('ix_species_common_name', 'species', ['common_name'], unique=True)
    
    # Metric table (exactly 5 metrics)
    op.create_table(
        'metric',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(50), nullable=False),
        sa.Column('label', sa.String(100), nullable=False),
        sa.Column('unit', sa.String(20), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_metric_id', 'metric', ['id'])
    op.create_index('ix_metric_key', 'metric', ['key'], unique=True)
    
    # Source table
    op.create_table(
        'source',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('publisher', sa.String(255), nullable=False),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_source_id', 'source', ['id'])
    
    # Target range table (matrix: species_id + metric_id)
    op.create_table(
        'target_range',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('species_id', sa.Integer(), nullable=False),
        sa.Column('metric_id', sa.Integer(), nullable=False),
        sa.Column('optimal_low', sa.Float(), nullable=False),
        sa.Column('optimal_high', sa.Float(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['species_id'], ['species.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_id'], ['source.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_target_range_id', 'target_range', ['id'])
    op.create_index('ix_target_range_species_metric', 'target_range', ['species_id', 'metric_id'], unique=True)


def downgrade() -> None:
    op.drop_table('target_range')
    op.drop_table('source')
    op.drop_table('metric')
    op.drop_table('species')
