"""Use simulation deadlines without relabeling historical wall-clock evidence."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('started_at_sim', 'deadline_at_sim', 'stopped_at_sim', 'last_attempt_at_sim',
                 'last_clock_sim_at', 'last_clock_synced_at_wall'):
        op.add_column('autopilot_runs', sa.Column(name, sa.DateTime(timezone=True)))
    for table in ('fleet_allocation_plans', 'fleet_allocation_reservations'):
        op.add_column(table, sa.Column('expires_at_sim', sa.DateTime(timezone=True)))
    op.add_column('live_submission_audits', sa.Column('submitted_at_sim', sa.DateTime(timezone=True)))
    # Existing active wall-clock runs must not silently acquire a new policy.
    runs = sa.table('autopilot_runs', sa.column('status', sa.Text), sa.column('stop_reason', sa.Text),
                    sa.column('stopped_at_wall', sa.DateTime(timezone=True)))
    op.get_bind().execute(runs.update().where(runs.c.status == 'running').values(
        status='stopped', stop_reason='clock_policy_changed', stopped_at_wall=datetime.now(timezone.utc)))
    # Retire only unsent legacy work. Submitted/pending-bid reservations must
    # survive the clock-policy migration until their board outcomes are known.
    plans = sa.table('fleet_allocation_plans', sa.column('plan_id', sa.Uuid), sa.column('status', sa.Text),
        sa.column('finished_at_wall', sa.DateTime(timezone=True)), sa.column('result', sa.JSON))
    holds = sa.table('fleet_allocation_reservations', sa.column('plan_id', sa.Uuid), sa.column('status', sa.Text))
    attempts = sa.table('autopilot_attempts', sa.column('allocation_plan_id', sa.Uuid), sa.column('action_id', sa.Uuid),
        sa.column('status', sa.Text), sa.column('finished_at_wall', sa.DateTime(timezone=True)), sa.column('reason_codes', sa.JSON))
    for row in op.get_bind().execute(sa.select(plans).where(plans.c.status.in_(('collecting', 'planning', 'reserved')))).mappings():
        stamp = datetime.now(timezone.utc)
        op.get_bind().execute(plans.update().where(plans.c.plan_id == row['plan_id']).values(status='superseded',
            finished_at_wall=stamp, result=(row['result'] or {}) | {'invalidated_reason': 'clock_policy_changed'}))
        op.get_bind().execute(holds.delete().where(holds.c.plan_id == row['plan_id'], holds.c.status == 'reserved'))
        op.get_bind().execute(attempts.update().where(attempts.c.allocation_plan_id == row['plan_id'],
            attempts.c.action_id.is_(None), attempts.c.status.in_(('ready', 'evaluating'))).values(status='cancelled',
            finished_at_wall=stamp, reason_codes=['clock_policy_changed']))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM autopilot_runs WHERE started_at_sim IS NOT NULL')):
        raise RuntimeError('Retain simulated run timing evidence before downgrading')
    op.drop_column('live_submission_audits', 'submitted_at_sim')
    for table in ('fleet_allocation_reservations', 'fleet_allocation_plans'):
        op.drop_column(table, 'expires_at_sim')
    for name in ('last_clock_synced_at_wall', 'last_clock_sim_at', 'last_attempt_at_sim',
                 'stopped_at_sim', 'deadline_at_sim', 'started_at_sim'):
        op.drop_column('autopilot_runs', name)
