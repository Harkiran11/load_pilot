"""Durable bounded autopilot sessions and auditable order attempts."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('autopilot_runs',
        sa.Column('run_id', sa.Uuid(), primary_key=True),
        sa.Column('status', sa.Text(), nullable=False), sa.Column('mode', sa.Text(), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('started_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deadline_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('stopped_at_wall', sa.DateTime(timezone=True)), sa.Column('stop_reason', sa.Text()),
        sa.Column('last_attempt_at_wall', sa.DateTime(timezone=True)),
        sa.Column('orders_reserved', sa.Integer(), nullable=False),
        sa.Column('estimated_loss_usd', sa.Numeric(18, 2), nullable=False),
        sa.Column('pending_estimated_loss_usd', sa.Numeric(18, 2), nullable=False),
        sa.Column('consecutive_errors', sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('running', 'stopped')", name='valid_status'),
        sa.CheckConstraint("mode IN ('shadow', 'live')", name='valid_mode'),
        sa.CheckConstraint('orders_reserved >= 0', name='nonnegative_orders'),
    )
    op.create_index('uq_autopilot_running', 'autopilot_runs', ['status'], unique=True, sqlite_where=sa.text("status = 'running'"))
    op.create_table('autopilot_attempts',
        sa.Column('attempt_id', sa.Uuid(), primary_key=True),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('autopilot_runs.run_id'), nullable=False),
        sa.Column('shipment_id', sa.Uuid(), sa.ForeignKey('shipments.shipment_id'), nullable=False),
        sa.Column('external_id', sa.Text(), nullable=False), sa.Column('order_type', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False), sa.Column('decision', sa.Text()),
        sa.Column('action_id', sa.Uuid(), sa.ForeignKey('dispatch_actions.action_id'), unique=True),
        sa.Column('candidate_unit_id', sa.Uuid(), sa.ForeignKey('fleet_units.unit_id')),
        sa.Column('bid_rate', sa.Numeric(18, 2)),
        sa.Column('model_snapshot', sa.JSON(), nullable=False),
        sa.Column('evaluation_snapshot', sa.JSON(), nullable=False),
        sa.Column('reason_codes', sa.JSON(), nullable=False), sa.Column('message', sa.Text()),
        sa.Column('created_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at_wall', sa.DateTime(timezone=True)), sa.Column('finished_at_wall', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('run_id', 'shipment_id'),
    )
    op.create_index('ix_autopilot_attempts_run_id', 'autopilot_attempts', ['run_id'])
    op.create_index('ix_autopilot_attempts_shipment_id', 'autopilot_attempts', ['shipment_id'])
    op.create_index('uq_autopilot_inflight', 'autopilot_attempts', ['run_id'], unique=True,
                    sqlite_where=sa.text("status IN ('evaluating', 'ready', 'submitting')"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM autopilot_runs WHERE status = 'running'")):
        raise RuntimeError('Stop autopilot before downgrading')
    op.drop_table('autopilot_attempts')
    op.drop_table('autopilot_runs')
