from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from schema.audit.activity import Activity
from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.dispatch_action import DispatchAction
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.terminal.terminal import Terminal
from service.audit.activity import add_activity
from service.dispatch.assignment_occupancy import assignment_occupied_bounds
from service.shared.geography import nearest_terminal
from service.dispatch.reservations import release_resolved_reservations

CONFIRMED_STATUSES = ('accepted', 'in_transit', 'completed')
# Orders the board will never book. They stay in the local table as history,
# so a full reconcile must skip them: the board has no booking left to mirror
# and re-reading every historic order turned one snapshot into tens of
# thousands of queries inside the write transaction.
TERMINAL_BOOKING_STATUSES = ('expired', 'bid_lost', 'declined')


def reconcile_bookings(session: Session, sim_time: datetime, external_ids: list[str] | None = None) -> None:
    """Mirror board bookings into local assignments.

    ``external_ids`` scopes reconciliation to the orders a caller just changed,
    which keeps high-frequency event snapshots cheap. ``None`` reconciles every
    locally retained shipment that can still change on the board.
    """
    if external_ids is not None and not external_ids:
        return
    terminals = session.scalars(select(Terminal).order_by(Terminal.board_terminal_id)).all()
    by_code = {terminal.code.upper(): terminal for terminal in terminals}
    units = {unit.board_unit_id: unit for unit in session.scalars(select(FleetUnit).where(FleetUnit.board_missing_at.is_(None)))}
    statement = select(Shipment).where(Shipment.board_missing_at.is_(None))
    if external_ids is not None:
        statement = statement.where(Shipment.external_id.in_(external_ids))
    else:
        # Keep orders that can still be booked, plus any order that still holds
        # an active assignment so a superseded booking is released.
        held = select(Assignment.shipment_id).where(
            Assignment.shipment_id == Shipment.shipment_id,
            Assignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
        ).exists()
        statement = statement.where(
            or_(Shipment.status.notin_(TERMINAL_BOOKING_STATUSES), held)
        )
    shipments = session.scalars(statement.execution_options(populate_existing=True)).all()
    # Load the assignments for these orders in one query. A per-order lookup
    # made a board snapshot issue hundreds of queries inside the write lock.
    shipment_ids = [shipment.shipment_id for shipment in shipments]
    active_by_shipment = {}
    completed_by_shipment = {}
    if shipment_ids:
        for assignment in session.scalars(select(Assignment).where(
                Assignment.shipment_id.in_(shipment_ids)).order_by(
                Assignment.shipment_id, Assignment.assignment_status,
                Assignment.confirmed_at.desc(), Assignment.assignment_id.desc())):
            if assignment.assignment_status in ACTIVE_ASSIGNMENT_STATUSES:
                active_by_shipment.setdefault(assignment.shipment_id, assignment)
            elif assignment.assignment_status == 'completed':
                completed_by_shipment.setdefault(assignment.shipment_id, []).append(assignment)
    # Release superseded locks first: a new booking may share a unit with a
    # delivery or reassignment reconciled in this same snapshot.
    for shipment in shipments:
        active = active_by_shipment.get(shipment.shipment_id)
        if active is None:
            continue
        previous_unit = session.get(FleetUnit, active.unit_id)
        reassigned = (
            shipment.status in CONFIRMED_STATUSES
            and shipment.board_assigned_unit_id is not None
            and previous_unit.board_unit_id != shipment.board_assigned_unit_id
        )
        if reassigned or shipment.status in ('declined', 'bid_lost', 'expired'):
            active.assignment_status = 'cancelled'
            active.cancelled_at = sim_time
            add_activity(session, 'assignment.cancelled',
                         'Assignment superseded by board reassignment' if reassigned else 'Board booking no longer active',
                         shipment_id=shipment.shipment_id,
                         details={'assignment_id': str(active.assignment_id)})
        elif shipment.status == 'completed':
            active.assignment_status = 'completed'
            active.completed_at = shipment.delivery_at or sim_time
    session.flush()

    for shipment in shipments:
        if shipment.status in CONFIRMED_STATUSES and shipment.board_assigned_unit_id is not None:
            unit = units.get(shipment.board_assigned_unit_id)
            origin = by_code.get((shipment.assigned_terminal_code or shipment.origin_terminal_code or '').upper())
            destination = by_code.get((shipment.destination_terminal_code or '').upper())
            origin = origin or nearest_terminal(terminals, shipment.origin_latitude, shipment.origin_longitude)
            destination = destination or nearest_terminal(terminals, shipment.destination_latitude, shipment.destination_longitude)
            if unit and origin and destination and shipment.pickup_at and shipment.delivery_at:
                assignment = active_by_shipment.get(shipment.shipment_id)
                if assignment is None and shipment.status == 'completed':
                    assignment = next((row for row in completed_by_shipment.get(shipment.shipment_id, [])
                                       if row.unit_id == unit.unit_id), None)
                    # Completed rows are immutable history, including their interval.
                historical_completion = assignment is not None and assignment.assignment_status == 'completed'
                if not historical_completion:
                    try:
                        with session.begin_nested():
                            occupied_from, occupied_until = assignment_occupied_bounds(session, shipment, unit, assignment, sim_time)
                            is_new = assignment is None
                            if is_new:
                                assignment = Assignment(shipment_id=shipment.shipment_id, confirmed_at=sim_time)
                                session.add(assignment)
                            assignment.unit_id = unit.unit_id
                            assignment.origin_terminal_id = origin.terminal_id
                            assignment.destination_terminal_id = destination.terminal_id
                            assignment.occupied_from = occupied_from
                            assignment.occupied_until = occupied_until
                            assignment.busy_from = shipment.pickup_at
                            assignment.busy_until = shipment.delivery_at
                            assignment.assignment_status = 'confirmed' if shipment.status == 'accepted' else shipment.status
                            assignment.completed_at = shipment.delivery_at if shipment.status == 'completed' else None
                            session.flush()
                            if is_new:
                                add_activity(session, 'assignment.confirmed', 'Board booking mirrored locally',
                                             shipment_id=shipment.shipment_id)
                    except IntegrityError:
                        # Keep upstream truth even if its booking cannot satisfy local
                        # scheduling invariants. Surface the conflict for review.
                        details = {
                            'board_unit_id': shipment.board_assigned_unit_id,
                            'busy_from': shipment.pickup_at.isoformat(),
                            'busy_until': shipment.delivery_at.isoformat(),
                        }
                        previous = session.scalar(select(Activity).where(
                            Activity.shipment_id == shipment.shipment_id,
                            Activity.event_type == 'assignment.conflict',
                        ).order_by(Activity.occurred_at_wall.desc()))
                        if previous is None or previous.details != details:
                            add_activity(session, 'assignment.conflict', 'Board assignment violates local interval or lock rules',
                                         shipment_id=shipment.shipment_id, details=details)

        unresolved = session.scalars(select(DispatchAction).where(
            DispatchAction.shipment_id == shipment.shipment_id,
            DispatchAction.request_status.in_(('submitting', 'unknown')),
        )).all()
        for action in unresolved:
            confirmed = (
                (action.action_type == 'accept' and shipment.status in CONFIRMED_STATUSES and shipment.board_assigned_unit_id is not None)
                or (action.action_type == 'decline' and shipment.status == 'declined')
                or (action.action_type == 'bid' and shipment.status in ('bid_submitted', 'bid_lost', *CONFIRMED_STATUSES)
                    and shipment.winning_bid_rate == action.bid_rate)
            )
            if confirmed:
                action.request_status = 'succeeded'
                action.board_message = 'Outcome reconciled from board snapshot'
                action.resolved_at = datetime.now(timezone.utc)
                add_activity(session, 'action.reconciled', action.board_message,
                             shipment_id=shipment.shipment_id, action_id=action.action_id)
    session.flush()
    release_resolved_reservations(session)
