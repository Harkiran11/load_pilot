from uuid import uuid4

from sqlalchemy import func, select

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.audit.activity import add_activity
from service.orders.expiration import utc


def retire_reset_state(session, clock, orders, missing_ids, wall_now):
    """Require authoritative 404s plus clock/counter reset evidence, never absence alone."""
    missing = missing_ids or set()
    if not missing:
        # Nothing was reported missing, so no known order can be a subset of it.
        return False
    # The board retains every historic order, so a reset check must not load
    # them all: an existence test answers "is any live order still present?"
    # without materialising thousands of ORM rows on every snapshot.
    unmatched = session.scalar(
        select(Shipment.external_id).where(
            Shipment.board_retired_at.is_(None),
            Shipment.external_id.notin_(missing),
        ).limit(1))
    if unmatched is not None:
        return False
    old_counter = session.scalar(
        select(func.max(Shipment.board_order_id)).where(Shipment.board_retired_at.is_(None))) or 0
    known = list(session.scalars(select(Shipment).where(Shipment.board_retired_at.is_(None))))
    known_ids = {row.external_id for row in known}
    if not known_ids:
        return False
    previous = session.get(SimulationClock, 1)
    if previous and utc(clock.wall_time_utc) <= utc(previous.wall_time_utc):
        return False
    counter_reset = any(order.external_id not in known_ids and order.id <= old_counter for order in orders)
    clock_reset = previous is not None and (utc(previous.sim_time_utc) - utc(clock.sim_time_utc)).total_seconds() > 1
    if not counter_reset and not clock_reset:
        return False
    from service.autopilot.autopilot_guards import stop_running_on_reset
    stop_running_on_reset(session)
    shipment_ids = [row.shipment_id for row in known]
    for row in known:
        row.board_retired_at = wall_now
        row.board_missing_at = row.board_missing_at or wall_now
    for assignment in session.scalars(select(Assignment).where(Assignment.shipment_id.in_(shipment_ids))):
        assignment.lock_retired_at = wall_now
        if assignment.assignment_status in ACTIVE_ASSIGNMENT_STATUSES:
            assignment.assignment_status = 'cancelled'
            assignment.cancelled_at = clock.sim_time_utc
        add_activity(session, 'assignment.reset_retired', 'Previous board generation lock retired; history retained',
            shipment_id=assignment.shipment_id, details={'assignment_id': str(assignment.assignment_id)})
    actions = list(session.scalars(select(DispatchAction).where(DispatchAction.shipment_id.in_(shipment_ids))))
    for action in actions:
        if action.request_status in ('submitting', 'unknown'):
            action.request_status = 'failed'
            action.board_message = 'Previous board generation was reset; submission retired locally'
            action.resolved_at = wall_now
        hold = session.get(AssignmentReservation, action.action_id)
        if hold is not None:
            session.delete(hold)
            add_activity(session, 'reservation.reset_retired', 'Previous board generation reservation released',
                         shipment_id=action.shipment_id, action_id=action.action_id)
    add_activity(session, 'board.reset_detected', 'Confirmed board reset; old identities and locks retired',
        details={'generation_id': str(uuid4()), 'retired_order_count': len(known),
                 'counter_reset': counter_reset, 'clock_rewind': clock_reset})
    session.flush()
    return True
