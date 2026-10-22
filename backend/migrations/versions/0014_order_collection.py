"""Allow one durable collecting/planning/reserved batch per autopilot run."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('uq_allocation_active_run', table_name='fleet_allocation_plans')
    op.create_index('uq_allocation_active_run', 'fleet_allocation_plans', ['run_id'], unique=True,
                    sqlite_where=sa.text("status IN ('collecting', 'planning', 'reserved')"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM fleet_allocation_plans WHERE status = 'collecting'")):
        raise RuntimeError('Stop collecting autopilot runs before downgrading')
    op.drop_index('uq_allocation_active_run', table_name='fleet_allocation_plans')
    op.create_index('uq_allocation_active_run', 'fleet_allocation_plans', ['run_id'], unique=True,
                    sqlite_where=sa.text("status IN ('planning', 'reserved')"))
