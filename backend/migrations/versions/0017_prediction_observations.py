"""Durable manual prediction inputs, failures and explicit execution links."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('prediction_observations',
        sa.Column('observation_id', sa.Uuid(), primary_key=True),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('external_id', sa.Text(), nullable=False),
        sa.Column('synthetic', sa.Boolean(), nullable=False),
        sa.Column('started_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at_wall', sa.DateTime(timezone=True)),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON()), sa.Column('response_body', sa.Text()),
        sa.Column('http_status', sa.Integer()), sa.Column('error_code', sa.Text()),
        sa.Column('executions', sa.JSON(), nullable=False))
    for name in ('request_id', 'external_id'):
        op.create_index(f'ix_prediction_observations_{name}', 'prediction_observations', [name])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM prediction_observations')):
        raise RuntimeError('Retain prediction evidence before downgrading; observations are not discarded')
    op.drop_table('prediction_observations')
