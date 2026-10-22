"""Protect deadhead travel and turnaround in local assignment windows."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

ACTIVE = "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active')"


def guards(extended, retired=False):
    def locking(alias):
        condition = f"({alias}.{ACTIVE} OR ({alias}.assignment_status = 'completed' AND {alias}.occupied_until > {alias}.busy_until))" if extended else f'{alias}.{ACTIVE}'
        return f'({alias}.lock_retired_at IS NULL AND {condition})' if retired else condition

    def start(alias):
        return f"CASE WHEN {alias}.assignment_status = 'completed' THEN {alias}.busy_until ELSE coalesce({alias}.occupied_from, {alias}.busy_from) END" if extended else f'{alias}.busy_from'

    def end(alias):
        return f'coalesce({alias}.occupied_until, {alias}.busy_until)' if extended else f'{alias}.busy_until'

    # ORM timestamps are normalized to UTC; lexical comparison preserves
    # microseconds that SQLite julianday() would round away.
    for event in ('insert', 'update'):
        op.execute(f'''
            CREATE TRIGGER assignments_no_overlap_{event}
            BEFORE {event.upper()} ON assignments WHEN {locking('NEW')}
            BEGIN
                SELECT RAISE(ABORT, 'overlapping active assignment') WHERE EXISTS (
                    SELECT 1 FROM assignments a WHERE a.unit_id = NEW.unit_id
                    AND a.assignment_id != NEW.assignment_id AND {locking('a')}
                    AND ({start('a')}) < ({end('NEW')})
                    AND ({end('a')}) > ({start('NEW')})
                );
                SELECT RAISE(ABORT, 'assignment overlaps another submission reservation') WHERE EXISTS (
                    SELECT 1 FROM assignment_reservations r JOIN dispatch_actions d ON d.action_id = r.action_id
                    WHERE r.unit_id = NEW.unit_id AND d.shipment_id != NEW.shipment_id
                    AND (r.busy_from) < ({end('NEW')})
                    AND (r.busy_until) > ({start('NEW')})
                );
            END
        ''')
        op.execute(f'''
            CREATE TRIGGER reservations_no_overlap_{event}
            BEFORE {event.upper()} ON assignment_reservations
            BEGIN
                SELECT RAISE(ABORT, 'overlapping submission reservation') WHERE EXISTS (
                    SELECT 1 FROM assignment_reservations r WHERE r.unit_id = NEW.unit_id AND r.action_id != NEW.action_id
                    AND (r.busy_from) < (NEW.busy_until)
                    AND (r.busy_until) > (NEW.busy_from)
                );
                SELECT RAISE(ABORT, 'reservation overlaps active assignment') WHERE EXISTS (
                    SELECT 1 FROM assignments a WHERE a.unit_id = NEW.unit_id AND {locking('a')}
                    AND ({start('a')}) < (NEW.busy_until)
                    AND ({end('a')}) > (NEW.busy_from)
                );
            END
        ''')


def drop_guards():
    for prefix in ('assignments', 'reservations'):
        for event in ('insert', 'update'):
            op.execute(f'DROP TRIGGER {prefix}_no_overlap_{event}')


def upgrade():
    if op.get_bind().dialect.name != 'sqlite':
        raise RuntimeError('Occupied-window guards require SQLite')
    drop_guards()
    with op.batch_alter_table('assignments') as batch:
        batch.add_column(sa.Column('occupied_from', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('occupied_until', sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint('occupied_covers_load',
            '(occupied_from IS NULL AND occupied_until IS NULL) OR '
            '(occupied_from IS NOT NULL AND occupied_until IS NOT NULL AND occupied_from <= busy_from AND occupied_until >= busy_until)')
    guards(True)


def downgrade():
    if op.get_bind().execute(sa.text('''SELECT 1 FROM assignments
        WHERE occupied_from < busy_from OR occupied_until > busy_until LIMIT 1''')).first():
        raise RuntimeError('Cannot downgrade occupied windows without discarding travel/turnaround locks')
    drop_guards()
    with op.batch_alter_table('assignments') as batch:
        batch.drop_constraint('occupied_covers_load', type_='check')
        batch.drop_column('occupied_until')
        batch.drop_column('occupied_from')
    guards(False)
