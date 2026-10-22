"""Keep actual financial outcomes on shipments and persist observed cohort reviews."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('observed_cohorts',
        sa.Column('cohort_id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('strategy', sa.Text(), nullable=False),
        sa.Column('period_start_sim', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end_sim', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fleet_context', sa.Text(), nullable=False),
        sa.Column('available_unit_hours', sa.Numeric(18, 6), nullable=False),
        sa.Column('exposure_evidence', sa.Text(), nullable=False),
        sa.Column('policy_snapshot', sa.JSON(), nullable=False),
        sa.Column('created_at_wall', sa.DateTime(timezone=True), nullable=False))
    # SQLite supports these nullable ADD COLUMN operations without rebuilding
    # shipments and disturbing assignment/reservation triggers.
    op.add_column('shipments', sa.Column('financial_outcome', sa.JSON(), nullable=True))
    if op.get_bind().dialect.name == 'sqlite':
        op.execute('ALTER TABLE shipments ADD COLUMN observed_cohort_id CHAR(32) '
                   'CONSTRAINT fk_shipments_observed_cohort_id_observed_cohorts REFERENCES observed_cohorts (cohort_id)')
    else:
        op.add_column('shipments', sa.Column('observed_cohort_id', sa.Uuid(),
            sa.ForeignKey('observed_cohorts.cohort_id', name='fk_shipments_observed_cohort_id_observed_cohorts'), nullable=True))
    op.create_index('ix_shipments_observed_cohort_id', 'shipments', ['observed_cohort_id'])
    op.create_table('observed_comparisons',
        sa.Column('comparison_id', sa.Uuid(), primary_key=True),
        sa.Column('baseline_id', sa.Uuid(), sa.ForeignKey('observed_cohorts.cohort_id'), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), sa.ForeignKey('observed_cohorts.cohort_id'), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('comparability_notes', sa.Text(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('evidence_fingerprint', sa.Text(), nullable=False),
        sa.Column('created_at_wall', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewed_at_wall', sa.DateTime(timezone=True)),
        sa.Column('reviewer', sa.Text()), sa.Column('review_notes', sa.Text()))
    op.create_index('ix_observed_comparisons_baseline_id', 'observed_comparisons', ['baseline_id'])
    op.create_index('ix_observed_comparisons_candidate_id', 'observed_comparisons', ['candidate_id'])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text('SELECT count(*) FROM observed_cohorts')) or connection.scalar(
            sa.text('SELECT count(*) FROM shipments WHERE financial_outcome IS NOT NULL')):
        raise RuntimeError('Export actual outcomes and cohort records before downgrading; evidence is not discarded')
    op.drop_table('observed_comparisons')
    op.drop_index('ix_shipments_observed_cohort_id', table_name='shipments')
    op.drop_column('shipments', 'observed_cohort_id')
    op.drop_column('shipments', 'financial_outcome')
    op.drop_table('observed_cohorts')
