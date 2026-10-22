"""Retire old board identities and locks without deleting history."""
from importlib import import_module

from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def drop_guards():
    for prefix in ('assignments', 'reservations'):
        for event in ('insert', 'update'):
            op.execute(f'DROP TRIGGER {prefix}_no_overlap_{event}')


def recreate_guards(retired):
    drop_guards()
    import_module('migrations.versions.0009_occupied_windows').guards(True, retired=retired)


def upgrade():
    op.add_column('shipments', sa.Column('board_retired_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('assignments', sa.Column('lock_retired_at', sa.DateTime(timezone=True), nullable=True))
    recreate_guards(True)


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM assignments WHERE lock_retired_at IS NOT NULL')):
        raise RuntimeError('Retired board locks cannot be restored by downgrading')
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM shipments WHERE board_retired_at IS NOT NULL')):
        raise RuntimeError('Retired board identities cannot be discarded by downgrading')
    drop_guards()
    # Native SQLite DROP COLUMN preserves tables referenced by foreign keys.
    op.drop_column('assignments', 'lock_retired_at')
    op.drop_column('shipments', 'board_retired_at')
    import_module('migrations.versions.0009_occupied_windows').guards(True)
