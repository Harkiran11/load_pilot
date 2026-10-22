"""Auditable fleet allocation plans and durable capacity holds."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('fleet_allocation_plans',
        sa.Column('plan_id', sa.Uuid(), primary_key=True),
        sa.Column('run_id', sa.Uuid(), sa.ForeignKey('autopilot_runs.run_id'), nullable=False),
        sa.Column('status', sa.Text(), nullable=False), sa.Column('snapshot_fingerprint', sa.Text()),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at_wall', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_fleet_allocation_plans_run_id', 'fleet_allocation_plans', ['run_id'])
    op.create_index('uq_allocation_active_run', 'fleet_allocation_plans', ['run_id'], unique=True,
                    sqlite_where=sa.text("status IN ('planning', 'reserved')"))
    op.create_table('fleet_allocation_reservations',
        sa.Column('reservation_id', sa.Uuid(), primary_key=True),
        sa.Column('plan_id', sa.Uuid(), sa.ForeignKey('fleet_allocation_plans.plan_id'), nullable=False),
        sa.Column('shipment_id', sa.Uuid(), sa.ForeignKey('shipments.shipment_id'), nullable=False),
        sa.Column('unit_id', sa.Uuid(), sa.ForeignKey('fleet_units.unit_id'), nullable=False),
        sa.Column('action_id', sa.Uuid(), sa.ForeignKey('dispatch_actions.action_id'), unique=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('busy_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('busy_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at_wall', sa.DateTime(timezone=True)),
    )
    for field in ('plan_id', 'shipment_id', 'unit_id'):
        op.create_index(f'ix_fleet_allocation_reservations_{field}', 'fleet_allocation_reservations', [field])
    op.execute('ALTER TABLE autopilot_attempts ADD COLUMN allocation_plan_id CHAR(32) REFERENCES fleet_allocation_plans(plan_id)')
    for event in ('insert', 'update'):
        op.execute(f'''
            CREATE TRIGGER allocation_no_overlap_{event}
            BEFORE {event.upper()} ON fleet_allocation_reservations BEGIN
                SELECT RAISE(ABORT, 'invalid allocation interval') WHERE NEW.busy_until <= NEW.busy_from;
                SELECT RAISE(ABORT, 'overlapping allocation reservation') WHERE EXISTS (
                    SELECT 1 FROM fleet_allocation_reservations r WHERE r.reservation_id != NEW.reservation_id
                    AND (r.shipment_id = NEW.shipment_id OR (r.unit_id = NEW.unit_id
                         AND r.busy_from < NEW.busy_until AND r.busy_until > NEW.busy_from))
                );
                SELECT RAISE(ABORT, 'allocation overlaps assignment') WHERE EXISTS (
                    SELECT 1 FROM assignments a WHERE a.unit_id = NEW.unit_id AND a.shipment_id != NEW.shipment_id
                    AND a.lock_retired_at IS NULL
                    AND (a.assignment_status IN ('confirmed','accepted','in_transit','active') OR
                         (a.assignment_status = 'completed' AND a.occupied_until > a.busy_until))
                    AND CASE WHEN a.assignment_status = 'completed' THEN a.busy_until ELSE coalesce(a.occupied_from,a.busy_from) END < NEW.busy_until
                    AND coalesce(a.occupied_until,a.busy_until) > NEW.busy_from
                );
                SELECT RAISE(ABORT, 'allocation overlaps submission') WHERE EXISTS (
                    SELECT 1 FROM assignment_reservations r JOIN dispatch_actions d ON d.action_id = r.action_id
                    WHERE r.unit_id = NEW.unit_id AND d.shipment_id != NEW.shipment_id
                    AND r.busy_from < NEW.busy_until AND r.busy_until > NEW.busy_from
                );
            END
        ''')
        op.execute(f'''
            CREATE TRIGGER submission_allocation_guard_{event}
            BEFORE {event.upper()} ON assignment_reservations BEGIN
                SELECT RAISE(ABORT, 'submission overlaps fleet allocation') WHERE EXISTS (
                    SELECT 1 FROM fleet_allocation_reservations r JOIN dispatch_actions d ON d.action_id = NEW.action_id
                    WHERE r.unit_id = NEW.unit_id AND r.shipment_id != d.shipment_id
                    AND r.busy_from < NEW.busy_until AND r.busy_until > NEW.busy_from
                );
            END
        ''')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM fleet_allocation_reservations')):
        raise RuntimeError('Resolve allocation holds before downgrading')
    for event in ('insert', 'update'):
        op.execute(f'DROP TRIGGER submission_allocation_guard_{event}')
        op.execute(f'DROP TRIGGER allocation_no_overlap_{event}')
    op.drop_column('autopilot_attempts', 'allocation_plan_id')
    op.drop_table('fleet_allocation_reservations')
    op.drop_table('fleet_allocation_plans')
