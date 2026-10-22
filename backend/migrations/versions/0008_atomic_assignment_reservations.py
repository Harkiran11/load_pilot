"""Persist provisional locks for atomic preflight and board submission."""

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

ACTIVE = "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active')"


def assignment_guards(include_reservations):
    for event in ('INSERT', 'UPDATE'):
        reservation_check = '''
            SELECT RAISE(ABORT, 'assignment overlaps another submission reservation')
            WHERE EXISTS (
                SELECT 1 FROM assignment_reservations r
                JOIN dispatch_actions d ON d.action_id = r.action_id
                WHERE r.unit_id = NEW.unit_id AND d.shipment_id != NEW.shipment_id
                  AND julianday(r.busy_from) < julianday(NEW.busy_until)
                  AND julianday(r.busy_until) > julianday(NEW.busy_from)
            );
        ''' if include_reservations else ''
        op.execute(f'''
            CREATE TRIGGER assignments_no_overlap_{event.lower()}
            BEFORE {event} ON assignments WHEN NEW.{ACTIVE}
            BEGIN
                SELECT RAISE(ABORT, 'overlapping active assignment')
                WHERE EXISTS (
                    SELECT 1 FROM assignments a WHERE a.unit_id = NEW.unit_id
                      AND a.assignment_id != NEW.assignment_id AND a.{ACTIVE}
                      AND julianday(a.busy_from) < julianday(NEW.busy_until)
                      AND julianday(a.busy_until) > julianday(NEW.busy_from)
                );
                {reservation_check}
            END
        ''')


def upgrade():
    if op.get_bind().dialect.name != 'sqlite':
        raise RuntimeError('Atomic assignment reservations currently support SQLite only')
    unresolved = op.get_bind().execute(sa.text('''
        SELECT action_id FROM dispatch_actions
        WHERE action_type IN ('accept', 'bid') AND request_status IN ('submitting', 'unknown') LIMIT 1
    ''')).first()
    if unresolved:
        raise RuntimeError('Reconcile unresolved accept/bid submissions before migration 0008')
    op.create_table(
        'assignment_reservations',
        sa.Column('action_id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('busy_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('busy_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('action_id', name=op.f('pk_assignment_reservations')),
        sa.ForeignKeyConstraint(['action_id'], ['dispatch_actions.action_id'], name=op.f('fk_assignment_reservations_action_id_dispatch_actions')),
        sa.ForeignKeyConstraint(['unit_id'], ['fleet_units.unit_id'], name=op.f('fk_assignment_reservations_unit_id_fleet_units')),
        sa.CheckConstraint('busy_until > busy_from', name=op.f('ck_assignment_reservations_positive_interval')),
    )
    op.create_index('ix_assignment_reservations_unit_id', 'assignment_reservations', ['unit_id'])
    for event in ('INSERT', 'UPDATE'):
        op.execute(f'''
            CREATE TRIGGER reservations_no_overlap_{event.lower()}
            BEFORE {event} ON assignment_reservations
            BEGIN
                SELECT RAISE(ABORT, 'overlapping submission reservation')
                WHERE EXISTS (
                    SELECT 1 FROM assignment_reservations r
                    WHERE r.unit_id = NEW.unit_id AND r.action_id != NEW.action_id
                      AND julianday(r.busy_from) < julianday(NEW.busy_until)
                      AND julianday(r.busy_until) > julianday(NEW.busy_from)
                );
                SELECT RAISE(ABORT, 'reservation overlaps active assignment')
                WHERE EXISTS (
                    SELECT 1 FROM assignments a WHERE a.unit_id = NEW.unit_id AND a.{ACTIVE}
                      AND julianday(a.busy_from) < julianday(NEW.busy_until)
                      AND julianday(a.busy_until) > julianday(NEW.busy_from)
                );
            END
        ''')
    for event in ('insert', 'update'):
        op.execute(f'DROP TRIGGER assignments_no_overlap_{event}')
    assignment_guards(True)


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM assignment_reservations LIMIT 1')).first():
        raise RuntimeError('Resolve active submission reservations before downgrade')
    for event in ('insert', 'update'):
        op.execute(f'DROP TRIGGER assignments_no_overlap_{event}')
        op.execute(f'DROP TRIGGER reservations_no_overlap_{event}')
    assignment_guards(False)
    op.drop_table('assignment_reservations')
