"""Track locally retained orders absent from the board after reconciliation."""

from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('shipments', sa.Column('board_missing_at', sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table('shipments') as batch_op:
        batch_op.drop_column('board_missing_at')
