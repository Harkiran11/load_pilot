"""Store fleet capacity comparisons and track units absent from the board."""

from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('fleet_units', sa.Column('board_missing_at', sa.DateTime(timezone=True)))
    op.create_table(
        'fleet_capacity',
        sa.Column('terminal_id', sa.Uuid(), nullable=False),
        sa.Column('board_total_units', sa.Integer()),
        sa.Column('board_available_units', sa.Integer()),
        sa.Column('board_assigned_units', sa.Integer()),
        sa.Column('local_total_units', sa.Integer(), nullable=False),
        sa.Column('local_available_units', sa.Integer(), nullable=False),
        sa.Column('local_assigned_units', sa.Integer(), nullable=False),
        sa.Column('discrepancies', sa.JSON(), nullable=False),
        sa.Column('as_of_sim_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('terminal_id', name=op.f('pk_fleet_capacity')),
        sa.ForeignKeyConstraint(
            ['terminal_id'], ['terminals.terminal_id'],
            name=op.f('fk_fleet_capacity_terminal_id_terminals'),
        ),
    )


def downgrade() -> None:
    op.drop_table('fleet_capacity')
    with op.batch_alter_table('fleet_units') as batch_op:
        batch_op.drop_column('board_missing_at')
