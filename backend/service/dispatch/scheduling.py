from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.dispatch.scheduling import ReservationConflict, ScheduleConflict
from schema.allocation.allocation_plan import AllocationReservation
from service.orders.expiration import utc

BOOKABLE_UNIT_STATUSES = ('available', 'assigned', 'in_transit')


def locking_assignments():
    return Assignment.lock_retired_at.is_(None) & or_(Assignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
               (Assignment.assignment_status == 'completed') & (Assignment.occupied_until > Assignment.busy_until))


def occupied_start():
    return case((Assignment.assignment_status == 'completed', Assignment.busy_until),
                else_=func.coalesce(Assignment.occupied_from, Assignment.busy_from))


def occupied_end():
    return func.coalesce(Assignment.occupied_until, Assignment.busy_until)


def schedule_conflicts(session: Session, unit_ids, pickup_at: datetime, delivery_at: datetime):
    """Find every active overlap, including future locks and retained missing orders."""
    conflicts = {unit_id: [] for unit_id in unit_ids}
    if not conflicts:
        return conflicts
    assignments = session.scalars(select(Assignment).where(
        Assignment.unit_id.in_(unit_ids),
        locking_assignments(),
        occupied_start() < utc(delivery_at),
        occupied_end() > utc(pickup_at),
    ).order_by(Assignment.busy_from, Assignment.assignment_id)).all()
    for assignment in assignments:
        conflicts[assignment.unit_id].append(ScheduleConflict(
            assignment_id=assignment.assignment_id, shipment_id=assignment.shipment_id,
            busy_from=utc(assignment.busy_until if assignment.assignment_status == 'completed' else assignment.occupied_from or assignment.busy_from),
            busy_until=utc(assignment.occupied_until or assignment.busy_until),
            assignment_status=assignment.assignment_status,
        ))
    return conflicts


def unit_schedule_rejections(unit, pickup_at: datetime, conflicts: list[ScheduleConflict]) -> list[str]:
    reasons = []
    if unit.status not in BOOKABLE_UNIT_STATUSES:
        reasons.append('unit_status_unavailable')
    if unit.available_at is not None and utc(unit.available_at) > utc(pickup_at):
        reasons.append('unit_available_after_pickup')
    elif unit.status != 'available' and unit.available_at is None:
        reasons.append('unit_availability_unknown')
    if conflicts:
        reasons.append('assignment_window_overlap')
    return reasons


def reservation_conflicts(session: Session, unit_ids, pickup_at: datetime, delivery_at: datetime):
    conflicts = {unit_id: [] for unit_id in unit_ids}
    if not conflicts:
        return conflicts
    rows = session.execute(select(AssignmentReservation, DispatchAction.request_status).join(
        DispatchAction, DispatchAction.action_id == AssignmentReservation.action_id,
    ).where(
        AssignmentReservation.unit_id.in_(unit_ids),
        AssignmentReservation.busy_from < utc(delivery_at),
        AssignmentReservation.busy_until > utc(pickup_at),
        AssignmentReservation.action_id != session.info['dispatch_action_id'] if session.info.get('dispatch_action_id') else True,
    ).order_by(AssignmentReservation.busy_from, AssignmentReservation.action_id)).all()
    for reservation, status in rows:
        conflicts[reservation.unit_id].append(ReservationConflict(
            action_id=reservation.action_id, busy_from=utc(reservation.busy_from),
            busy_until=utc(reservation.busy_until), request_status=status,
        ))
    from service.allocation.allocation_holds import current_holds
    for hold in current_holds(session):
        if hold.unit_id in conflicts and utc(hold.busy_from) < utc(delivery_at) and utc(hold.busy_until) > utc(pickup_at):
            conflicts[hold.unit_id].append(ReservationConflict(
                action_id=hold.action_id, allocation_reservation_id=hold.reservation_id if isinstance(hold, AllocationReservation) else None,
                busy_from=utc(hold.busy_from), busy_until=utc(hold.busy_until), request_status=hold.status,
            ))
    return conflicts
