"""Persist history cleanup without removing operational booking records."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shipments', sa.Column('shipment_history_deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('shipments', sa.Column('bid_history_deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Native DROP COLUMN preserves the referenced shipment table and its guards.
    op.drop_column('shipments', 'bid_history_deleted_at')
    op.drop_column('shipments', 'shipment_history_deleted_at')
