"""Capture original live batch exchanges and durable submission safeguard evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('live_batch_audits',
        sa.Column('audit_id', sa.Uuid(), primary_key=True),
        sa.Column('plan_id', sa.Uuid(), sa.ForeignKey('fleet_allocation_plans.plan_id'), nullable=False),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('autopilot_runs.run_id'), nullable=False),
        sa.Column('synthetic', sa.Boolean(), nullable=False),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('operational_snapshot', sa.JSON(), nullable=False),
        sa.Column('requested_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('received_at_wall', sa.DateTime(timezone=True)),
        sa.Column('response', sa.JSON()), sa.Column('response_body', sa.Text()),
        sa.Column('http_status', sa.Integer()), sa.Column('error_code', sa.Text()))
    for name in ('plan_id', 'run_id'):
        op.create_index(f'ix_live_batch_audits_{name}', 'live_batch_audits', [name])
    op.create_table('live_submission_audits',
        sa.Column('submission_id', sa.Uuid(), primary_key=True),
        sa.Column('batch_audit_id', sa.Uuid(), sa.ForeignKey('live_batch_audits.audit_id')),
        sa.Column('attempt_id', sa.Uuid(), sa.ForeignKey('autopilot_attempts.attempt_id'), nullable=False),
        sa.Column('action_id', sa.Uuid(), sa.ForeignKey('dispatch_actions.action_id'), unique=True),
        sa.Column('decision_id', sa.Text()), sa.Column('external_id', sa.Text(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), sa.ForeignKey('fleet_units.unit_id'), nullable=False),
        sa.Column('idempotency_key', sa.Text(), nullable=False, unique=True),
        sa.Column('amount_usd', sa.Numeric(18, 2), nullable=False),
        sa.Column('attempted_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_at_wall', sa.DateTime(timezone=True)),
        sa.Column('revalidated_at_wall', sa.DateTime(timezone=True)),
        sa.Column('authorization_passed', sa.Boolean()), sa.Column('operational_checks_passed', sa.Boolean()),
        sa.Column('atomic_reservation_acquired', sa.Boolean()), sa.Column('rate_limit_passed', sa.Boolean()),
        sa.Column('status', sa.Text(), nullable=False), sa.Column('result', sa.JSON(), nullable=False))
    for name in ('batch_audit_id', 'attempt_id', 'submitted_at_wall'):
        op.create_index(f'ix_live_submission_audits_{name}', 'live_submission_audits', [name])


def downgrade():
    connection = op.get_bind()
    if any(connection.scalar(sa.text(f'SELECT count(*) FROM {table}')) for table in ('live_batch_audits', 'live_submission_audits')):
        raise RuntimeError('Export and retain live audit records before downgrading; evidence is not discarded')
    op.drop_table('live_submission_audits')
    op.drop_table('live_batch_audits')
