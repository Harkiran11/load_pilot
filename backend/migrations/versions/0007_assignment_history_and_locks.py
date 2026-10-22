"""Retain assignment history and enforce temporal locks in SQLite."""

from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

ACTIVE = "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active')"


def upgrade() -> None:
    if op.get_bind().dialect.name != 'sqlite':
        raise RuntimeError('Assignment temporal-lock migration currently supports SQLite only')
    # Fail before changing the table if existing locks need manual review.
    invalid = op.get_bind().execute(sa.text('''
        SELECT assignment_id FROM assignments
        WHERE busy_until <= busy_from OR assignment_status NOT IN
        ('confirmed', 'accepted', 'in_transit', 'active', 'completed', 'cancelled')
        LIMIT 1
    ''')).first()
    overlapping = op.get_bind().execute(sa.text(f'''
        SELECT a.assignment_id FROM assignments a JOIN assignments b
          ON a.unit_id = b.unit_id AND a.assignment_id < b.assignment_id
        WHERE a.{ACTIVE} AND b.{ACTIVE}
          AND julianday(a.busy_from) < julianday(b.busy_until)
          AND julianday(a.busy_until) > julianday(b.busy_from)
        LIMIT 1
    ''')).first()
    if invalid or overlapping:
        raise RuntimeError('Existing assignment intervals/statuses conflict; review them before migration 0007')
    with op.batch_alter_table('assignments') as batch:
        batch.add_column(sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
        batch.drop_index('ix_assignments_shipment_id')
        batch.create_index('ix_assignments_shipment_id', ['shipment_id'], unique=False)
        batch.create_check_constraint('positive_interval', 'busy_until > busy_from')
        batch.create_check_constraint('valid_status',
            "assignment_status IN ('confirmed', 'accepted', 'in_transit', 'active', 'completed', 'cancelled')")
        batch.create_index('uq_assignments_active_shipment', ['shipment_id'], unique=True,
                           sqlite_where=sa.text(ACTIVE))
    # SQLite serializes writers, so these checks also cover concurrent connections.
    for event in ('INSERT', 'UPDATE'):
        op.execute(f'''
            CREATE TRIGGER assignments_no_overlap_{event.lower()}
            BEFORE {event} ON assignments
            WHEN NEW.{ACTIVE}
            BEGIN
                SELECT RAISE(ABORT, 'overlapping active assignment')
                WHERE EXISTS (
                    SELECT 1 FROM assignments a
                    WHERE a.unit_id = NEW.unit_id
                      AND a.assignment_id != NEW.assignment_id
                      AND a.{ACTIVE}
                      AND julianday(a.busy_from) < julianday(NEW.busy_until)
                      AND julianday(a.busy_until) > julianday(NEW.busy_from)
                );
            END
        ''')


def downgrade() -> None:
    duplicates = op.get_bind().execute(sa.text('''
        SELECT shipment_id FROM assignments GROUP BY shipment_id HAVING count(*) > 1 LIMIT 1
    ''')).first()
    if duplicates:
        raise RuntimeError('Cannot downgrade assignment history without discarding records; archive history first')
    for event in ('insert', 'update'):
        op.execute(f'DROP TRIGGER assignments_no_overlap_{event}')
    with op.batch_alter_table('assignments') as batch:
        batch.drop_index('uq_assignments_active_shipment')
        batch.drop_index('ix_assignments_shipment_id')
        batch.create_index('ix_assignments_shipment_id', ['shipment_id'], unique=True)
        batch.drop_constraint(op.f('ck_assignments_positive_interval'), type_='check')
        batch.drop_constraint(op.f('ck_assignments_valid_status'), type_='check')
        batch.drop_column('cancelled_at')
