"""Create dispatcher database schema

Revision ID: 0001
Revises: 
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('historical_shipments',
    sa.Column('historical_shipment_id', sa.Uuid(), nullable=False),
    sa.Column('source_record_key', sa.Text(), nullable=False),
    sa.Column('lane_id', sa.Text(), nullable=False),
    sa.Column('origin_state', sa.Text(), nullable=False),
    sa.Column('destination_state', sa.Text(), nullable=False),
    sa.Column('origin_latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('origin_longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('destination_latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('destination_longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('order_type', sa.Text(), nullable=False),
    sa.Column('distance_miles', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('weight_lbs', sa.Integer(), nullable=False),
    sa.Column('offered_rate', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('bid_rate', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('won', sa.Boolean(), nullable=False),
    sa.Column('cost_per_mile', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('estimated_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('margin_estimate', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('historical_shipment_id', name=op.f('pk_historical_shipments')),
    sa.UniqueConstraint('source_record_key', name=op.f('uq_historical_shipments_source_record_key'))
    )
    op.create_table('terminals',
    sa.Column('terminal_id', sa.Uuid(), nullable=False),
    sa.Column('board_terminal_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('city', sa.Text(), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('seeded_unit_capacity', sa.Integer(), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('terminal_id', name=op.f('pk_terminals')),
    sa.UniqueConstraint('board_terminal_id', name=op.f('uq_terminals_board_terminal_id')),
    sa.UniqueConstraint('code', name=op.f('uq_terminals_code'))
    )
    op.create_table('fleet_units',
    sa.Column('unit_id', sa.Uuid(), nullable=False),
    sa.Column('board_unit_id', sa.BigInteger(), nullable=False),
    sa.Column('home_terminal_id', sa.Uuid(), nullable=False),
    sa.Column('unit_number', sa.Text(), nullable=False),
    sa.Column('employee_code', sa.Text(), nullable=False),
    sa.Column('first_name', sa.Text(), nullable=False),
    sa.Column('last_name', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('current_latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('current_longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['home_terminal_id'], ['terminals.terminal_id'], name=op.f('fk_fleet_units_home_terminal_id_terminals')),
    sa.PrimaryKeyConstraint('unit_id', name=op.f('pk_fleet_units')),
    sa.UniqueConstraint('board_unit_id', name=op.f('uq_fleet_units_board_unit_id')),
    sa.UniqueConstraint('employee_code', name=op.f('uq_fleet_units_employee_code')),
    sa.UniqueConstraint('unit_number', name=op.f('uq_fleet_units_unit_number'))
    )
    with op.batch_alter_table('fleet_units', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fleet_units_home_terminal_id'), ['home_terminal_id'], unique=False)

    op.create_table('shipments',
    sa.Column('shipment_id', sa.Uuid(), nullable=False),
    sa.Column('external_id', sa.Text(), nullable=False),
    sa.Column('board_order_id', sa.BigInteger(), nullable=True),
    sa.Column('order_type', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('origin_terminal_code', sa.Text(), nullable=True),
    sa.Column('destination_terminal_code', sa.Text(), nullable=True),
    sa.Column('origin_terminal_id', sa.Uuid(), nullable=True),
    sa.Column('destination_terminal_id', sa.Uuid(), nullable=True),
    sa.Column('origin_city', sa.Text(), nullable=True),
    sa.Column('origin_state', sa.Text(), nullable=True),
    sa.Column('origin_latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('origin_longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('destination_city', sa.Text(), nullable=True),
    sa.Column('destination_state', sa.Text(), nullable=True),
    sa.Column('destination_latitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('destination_longitude', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('distance_miles', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('weight_lbs', sa.Integer(), nullable=True),
    sa.Column('offered_rate', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('cost_per_mile', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('estimated_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('estimated_deadhead_miles', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('estimated_deadhead_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('estimated_total_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('pickup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivery_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('board_created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('bid_decision_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('winning_bid_rate', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('board_assigned_unit_id', sa.BigInteger(), nullable=True),
    sa.Column('assigned_terminal_code', sa.Text(), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['destination_terminal_id'], ['terminals.terminal_id'], name=op.f('fk_shipments_destination_terminal_id_terminals')),
    sa.ForeignKeyConstraint(['origin_terminal_id'], ['terminals.terminal_id'], name=op.f('fk_shipments_origin_terminal_id_terminals')),
    sa.PrimaryKeyConstraint('shipment_id', name=op.f('pk_shipments')),
    sa.UniqueConstraint('external_id', name=op.f('uq_shipments_external_id'))
    )
    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shipments_destination_terminal_id'), ['destination_terminal_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_origin_terminal_id'), ['origin_terminal_id'], unique=False)

    op.create_table('assignments',
    sa.Column('assignment_id', sa.Uuid(), nullable=False),
    sa.Column('shipment_id', sa.Uuid(), nullable=False),
    sa.Column('unit_id', sa.Uuid(), nullable=False),
    sa.Column('origin_terminal_id', sa.Uuid(), nullable=False),
    sa.Column('destination_terminal_id', sa.Uuid(), nullable=False),
    sa.Column('busy_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('busy_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('assignment_status', sa.Text(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['destination_terminal_id'], ['terminals.terminal_id'], name=op.f('fk_assignments_destination_terminal_id_terminals')),
    sa.ForeignKeyConstraint(['origin_terminal_id'], ['terminals.terminal_id'], name=op.f('fk_assignments_origin_terminal_id_terminals')),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.shipment_id'], name=op.f('fk_assignments_shipment_id_shipments')),
    sa.ForeignKeyConstraint(['unit_id'], ['fleet_units.unit_id'], name=op.f('fk_assignments_unit_id_fleet_units')),
    sa.PrimaryKeyConstraint('assignment_id', name=op.f('pk_assignments'))
    )
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assignments_destination_terminal_id'), ['destination_terminal_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assignments_origin_terminal_id'), ['origin_terminal_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_assignments_shipment_id'), ['shipment_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_assignments_unit_id'), ['unit_id'], unique=False)

    op.create_table('evaluations',
    sa.Column('evaluation_id', sa.Uuid(), nullable=False),
    sa.Column('shipment_id', sa.Uuid(), nullable=False),
    sa.Column('candidate_unit_id', sa.Uuid(), nullable=True),
    sa.Column('payout', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('loaded_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('deadhead_miles', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('deadhead_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('total_cost', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('profit_usd', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('margin_pct', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('suggested_bid', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('cost_source', sa.Text(), nullable=True),
    sa.Column('pricing_passed', sa.Boolean(), nullable=False),
    sa.Column('capacity_passed', sa.Boolean(), nullable=False),
    sa.Column('schedule_passed', sa.Boolean(), nullable=False),
    sa.Column('weight_passed', sa.Boolean(), nullable=False),
    sa.Column('location_passed', sa.Boolean(), nullable=False),
    sa.Column('offer_open_unexpired', sa.Boolean(), nullable=False),
    sa.Column('eligible', sa.Boolean(), nullable=False),
    sa.Column('rejection_reasons', sa.JSON(), nullable=False),
    sa.Column('policy_snapshot', sa.JSON(), nullable=False),
    sa.Column('evaluated_at_sim', sa.DateTime(timezone=True), nullable=False),
    sa.Column('evaluated_at_wall', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['candidate_unit_id'], ['fleet_units.unit_id'], name=op.f('fk_evaluations_candidate_unit_id_fleet_units')),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.shipment_id'], name=op.f('fk_evaluations_shipment_id_shipments')),
    sa.PrimaryKeyConstraint('evaluation_id', name=op.f('pk_evaluations'))
    )
    with op.batch_alter_table('evaluations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evaluations_candidate_unit_id'), ['candidate_unit_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evaluations_shipment_id'), ['shipment_id'], unique=False)

    op.create_table('dispatch_actions',
    sa.Column('action_id', sa.Uuid(), nullable=False),
    sa.Column('shipment_id', sa.Uuid(), nullable=False),
    sa.Column('evaluation_id', sa.Uuid(), nullable=True),
    sa.Column('action_type', sa.Text(), nullable=False),
    sa.Column('bid_rate', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('carrier_note', sa.Text(), nullable=True),
    sa.Column('request_status', sa.Text(), nullable=False),
    sa.Column('board_http_status', sa.Integer(), nullable=True),
    sa.Column('board_message', sa.Text(), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['evaluation_id'], ['evaluations.evaluation_id'], name=op.f('fk_dispatch_actions_evaluation_id_evaluations')),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.shipment_id'], name=op.f('fk_dispatch_actions_shipment_id_shipments')),
    sa.PrimaryKeyConstraint('action_id', name=op.f('pk_dispatch_actions'))
    )
    with op.batch_alter_table('dispatch_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dispatch_actions_evaluation_id'), ['evaluation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_dispatch_actions_shipment_id'), ['shipment_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('dispatch_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dispatch_actions_shipment_id'))
        batch_op.drop_index(batch_op.f('ix_dispatch_actions_evaluation_id'))

    op.drop_table('dispatch_actions')
    with op.batch_alter_table('evaluations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evaluations_shipment_id'))
        batch_op.drop_index(batch_op.f('ix_evaluations_candidate_unit_id'))

    op.drop_table('evaluations')
    with op.batch_alter_table('assignments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assignments_unit_id'))
        batch_op.drop_index(batch_op.f('ix_assignments_shipment_id'))
        batch_op.drop_index(batch_op.f('ix_assignments_origin_terminal_id'))
        batch_op.drop_index(batch_op.f('ix_assignments_destination_terminal_id'))

    op.drop_table('assignments')
    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shipments_origin_terminal_id'))
        batch_op.drop_index(batch_op.f('ix_shipments_destination_terminal_id'))

    op.drop_table('shipments')
    with op.batch_alter_table('fleet_units', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fleet_units_home_terminal_id'))

    op.drop_table('fleet_units')
    op.drop_table('terminals')
    op.drop_table('historical_shipments')
