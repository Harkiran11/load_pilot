"""Store the latest board simulation clock.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'simulation_clock',
        sa.Column('clock_id', sa.Integer(), nullable=False),
        sa.Column('wall_time_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sim_time_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('simulation_speed', sa.Numeric(18, 6), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('clock_id', name=op.f('pk_simulation_clock')),
        sa.CheckConstraint('clock_id = 1', name=op.f('ck_simulation_clock_singleton')),
    )


def downgrade() -> None:
    op.drop_table('simulation_clock')
