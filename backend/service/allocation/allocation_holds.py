from datetime import datetime, timezone

from sqlalchemy import select

from schema.allocation.allocation_plan import AllocationPlan, AllocationReservation
from schema.dispatch.assignment import Assignment
from schema.dispatch.dispatch_action import DispatchAction
from schema.orders.shipment import Shipment
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.dispatch.evaluation import Evaluation
from schema.fleet.fleet_unit import FleetUnit
from schema.dispatch.scheduling import PendingBidHold
from service.audit.activity import add_activity
from service.orders.expiration import utc
from service.simulation.simulation_time import current_sim
from service.shared.errors import ProductError


def reservation_live(session, row, wall):
    if row.expires_at_sim is not None:
        try:
            return utc(row.expires_at_sim) > current_sim(session, wall)
        except ProductError:
            return True  # An unknown clock cannot prove that capacity is free.
    return row.expires_at_wall is not None and utc(row.expires_at_wall) > wall


def current_holds(session):
    now = datetime.now(timezone.utc)
    ignored = session.info.get('allocation_plan_id')
    rows = [row for row in session.scalars(select(AllocationReservation))
            if not (session.info.get('dispatch_action_id') and row.action_id == session.info['dispatch_action_id'])
            and (row.status != 'reserved' or reservation_live(session, row, now))
            and not (row.status == 'reserved' and row.plan_id == ignored)]
    linked = {row.action_id for row in rows if row.action_id}
    pending = session.execute(select(DispatchAction, Evaluation).join(Evaluation).join(
        Shipment, Shipment.shipment_id == DispatchAction.shipment_id).where(
        DispatchAction.action_type == 'bid', DispatchAction.request_status == 'succeeded',
        Shipment.status == 'bid_submitted', Shipment.board_retired_at.is_(None),
        Evaluation.candidate_unit_id.is_not(None),
    )).all()
    for action, quote in pending:
        window = quote.policy_snapshot.get('occupied_window') if isinstance(quote.policy_snapshot, dict) else None
        if action.action_id not in linked and isinstance(window, dict):
            try:
                rows.append(PendingBidHold(reservation_id=action.action_id, action_id=action.action_id,
                    shipment_id=action.shipment_id, unit_id=quote.candidate_unit_id,
                    busy_from=window['occupied_from'], busy_until=window['occupied_until']))
            except (ValueError, KeyError):
                pass  # The allocator reports unlocated pending capacity conservatively.
    return rows


def cancel_plans(session, reason, run_id=None, plan_id=None, *, include_collecting=False, shipment_ids=None):
    states = ('collecting', 'planning', 'reserved') if include_collecting else ('planning', 'reserved')
    statement = select(AllocationPlan).where(AllocationPlan.status.in_(states))
    if run_id is not None:
        statement = statement.where(AllocationPlan.run_id == run_id)
    if plan_id is not None:
        statement = statement.where(AllocationPlan.plan_id == plan_id)
    if shipment_ids is not None:
        # Expiry only invalidates plans that actually hold the expired orders.
        affected = select(AllocationReservation.plan_id).where(
            AllocationReservation.shipment_id.in_(shipment_ids),
            AllocationReservation.status == 'reserved',
        )
        statement = statement.where(AllocationPlan.plan_id.in_(affected))
    for plan in session.scalars(statement):
        plan.status, plan.finished_at_wall = 'superseded', datetime.now(timezone.utc)
        plan.result = plan.result | {'invalidated_reason': reason}
        for hold in session.scalars(select(AllocationReservation).where(
            AllocationReservation.plan_id == plan.plan_id, AllocationReservation.status == 'reserved',
        )):
            session.delete(hold)
        for attempt in session.scalars(select(AutopilotAttempt).where(
            AutopilotAttempt.allocation_plan_id == plan.plan_id,
            AutopilotAttempt.action_id.is_(None), AutopilotAttempt.status.in_(('evaluating', 'ready')),
        )):
            attempt.status, attempt.finished_at_wall, attempt.reason_codes = 'cancelled', datetime.now(timezone.utc), [reason]
        add_activity(session, 'allocation.invalidated', reason, details={'plan_id': str(plan.plan_id)})
    session.flush()


def reap_plans(session):
    now = datetime.now(timezone.utc)
    for plan in session.scalars(select(AllocationPlan).where(
        AllocationPlan.status.in_(('planning', 'reserved')),
    )):
        if not reservation_live(session, plan, now):
            cancel_plans(session, 'reservation_ttl_expired', plan.run_id)


def reconcile_holds(session):
    reap_plans(session)
    for hold in session.scalars(select(AllocationReservation).where(AllocationReservation.action_id.is_not(None))):
        action = session.get(DispatchAction, hold.action_id)
        shipment = session.get(Shipment, hold.shipment_id)
        release = action.request_status == 'failed' or shipment.board_retired_at is not None
        if action.request_status == 'succeeded' and shipment.board_missing_at is None:
            if shipment.status in ('bid_lost', 'declined', 'expired'):
                release = True
            elif shipment.status == 'bid_submitted':
                hold.status, hold.expires_at_wall, hold.expires_at_sim = 'pending_bid', None, None
            elif shipment.status in ('accepted', 'in_transit', 'completed'):
                release = session.scalar(select(Assignment.assignment_id).join(FleetUnit).where(
                    Assignment.shipment_id == shipment.shipment_id,
                    Assignment.assignment_status.in_(('confirmed', 'accepted', 'in_transit', 'active', 'completed')),
                    Assignment.lock_retired_at.is_(None),
                    FleetUnit.board_unit_id == shipment.board_assigned_unit_id,
                    Assignment.busy_from == shipment.pickup_at, Assignment.busy_until == shipment.delivery_at,
                )) is not None
        if release:
            session.delete(hold)
            add_activity(session, 'allocation.hold_released', 'Allocation outcome reconciled',
                         shipment_id=shipment.shipment_id, action_id=action.action_id,
                         details={'reservation_id': str(hold.reservation_id)})
    session.flush()
