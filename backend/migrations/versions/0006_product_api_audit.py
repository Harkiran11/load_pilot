"""Persist product activity and prevent duplicate unresolved submissions."""

from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'activity',
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('shipment_id', sa.Uuid()),
        sa.Column('action_id', sa.Uuid()),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('occurred_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('occurred_at_sim', sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint('event_id', name=op.f('pk_activity')),
        sa.ForeignKeyConstraint(['shipment_id'], ['shipments.shipment_id'], name=op.f('fk_activity_shipment_id_shipments')),
        sa.ForeignKeyConstraint(['action_id'], ['dispatch_actions.action_id'], name=op.f('fk_activity_action_id_dispatch_actions')),
    )
    for column in ('shipment_id', 'event_type', 'occurred_at_wall'):
        op.create_index(f'ix_activity_{column}', 'activity', [column])
    op.create_index(
        'uq_dispatch_actions_unresolved', 'dispatch_actions', ['shipment_id'], unique=True,
        sqlite_where=sa.text("request_status IN ('submitting', 'unknown')"),
        postgresql_where=sa.text("request_status IN ('submitting', 'unknown')"),
    )


def downgrade() -> None:
    op.drop_index('uq_dispatch_actions_unresolved', table_name='dispatch_actions')
    op.drop_table('activity')
