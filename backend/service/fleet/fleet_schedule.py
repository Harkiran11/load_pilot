from sqlalchemy import func, select

from schema.shared.api import AssignmentView, Page
from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.fleet.fleet_schedule import ScheduledAssignment, ScheduledReservation, UnitSchedule
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from schema.terminal.terminal import Terminal
from service.shared.errors import ProductError
from service.dispatch.scheduling import locking_assignments, occupied_start, occupied_end
from service.orders.expiration import simulated_now, utc


def schedules(session, limit=50, offset=0, unit_id=None, terminal=None,
              starts_at=None, ends_at=None, include_history=False, include_missing=False):
    starts_at = utc(starts_at) if starts_at else None
    ends_at = utc(ends_at) if ends_at else None
    if starts_at and ends_at and ends_at <= starts_at:
        raise ProductError(422, 'ends_at must be later than starts_at')
    query = select(FleetUnit, Terminal.code).join(Terminal)
    if unit_id:
        query = query.where(FleetUnit.unit_id == unit_id)
    if not include_missing:
        query = query.where(FleetUnit.board_missing_at.is_(None))
    if terminal:
        query = query.where(Terminal.code == terminal.upper())
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(query.order_by(FleetUnit.unit_number, FleetUnit.unit_id).offset(offset).limit(limit)).all()
    clock = session.get(SimulationClock, 1)
    now = simulated_now(clock) if clock else None
    items = {unit.unit_id: UnitSchedule(
        unit_id=unit.unit_id, board_unit_id=unit.board_unit_id, unit_number=unit.unit_number,
        terminal_code=code, status=unit.status, board_missing=unit.board_missing_at is not None,
        available_at=unit.available_at, as_of_sim_time=now,
    ) for unit, code in rows}

    def window(query, model):
        if starts_at:
            query = query.where((occupied_end() if model is Assignment else model.busy_until) > starts_at)
        if ends_at:
            query = query.where((occupied_start() if model is Assignment else model.busy_from) < ends_at)
        return query.order_by(model.busy_from)

    bookings = select(Assignment, Shipment.external_id).join(Shipment).where(Assignment.unit_id.in_(items))
    if not include_history:
        bookings = bookings.where(locking_assignments())
    if not include_history and now is not None:
        bookings = bookings.where((Assignment.assignment_status != 'completed') | (Assignment.occupied_until > now))
    for assignment, external_id in session.execute(window(bookings, Assignment)):
        items[assignment.unit_id].assignments.append(ScheduledAssignment(
            **AssignmentView.model_validate(assignment).model_dump(),
            external_id=external_id,
        ))
    holds = select(AssignmentReservation, DispatchAction, Shipment.external_id).join(
        DispatchAction, AssignmentReservation.action_id == DispatchAction.action_id,
    ).join(Shipment, DispatchAction.shipment_id == Shipment.shipment_id).where(AssignmentReservation.unit_id.in_(items))
    for hold, action, external_id in session.execute(window(holds, AssignmentReservation)):
        items[hold.unit_id].reservations.append(ScheduledReservation(
            action_id=action.action_id, external_id=external_id, action_type=action.action_type,
            request_status=action.request_status, busy_from=hold.busy_from, busy_until=hold.busy_until,
        ))
    return Page[UnitSchedule](items=list(items.values()), total=total, limit=limit, offset=offset)


def unit_schedule(session, unit_id, starts_at=None, ends_at=None, include_history=False):
    result = schedules(session, unit_id=unit_id, starts_at=starts_at, ends_at=ends_at,
                       include_history=include_history, include_missing=True)
    if not result.items:
        raise ProductError(404, 'Fleet unit not found')
    return result.items[0]
