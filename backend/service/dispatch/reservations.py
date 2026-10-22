from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from service.audit.activity import add_activity


def release_resolved_reservations(session: Session) -> None:
    """Release only after rejection, a known non-booking outcome, or mirrored booking."""
    rows = session.execute(select(AssignmentReservation, DispatchAction, Shipment).join(
        DispatchAction, DispatchAction.action_id == AssignmentReservation.action_id,
    ).join(Shipment, Shipment.shipment_id == DispatchAction.shipment_id)).all()
    for reservation, action, shipment in rows:
        release = action.request_status == 'failed'
        if action.request_status == 'succeeded' and shipment.board_missing_at is None:
            release = shipment.status in ('bid_submitted', 'bid_lost', 'declined', 'expired')
            if shipment.status in ('accepted', 'in_transit', 'completed'):
                release = session.scalar(select(Assignment.assignment_id).join(FleetUnit).where(
                    Assignment.shipment_id == shipment.shipment_id,
                    Assignment.assignment_status.in_((*ACTIVE_ASSIGNMENT_STATUSES, 'completed')),
                    FleetUnit.board_unit_id == shipment.board_assigned_unit_id,
                    Assignment.busy_from == shipment.pickup_at,
                    Assignment.busy_until == shipment.delivery_at,
                    or_(Assignment.unit_id != reservation.unit_id, and_(
                        func.coalesce(Assignment.occupied_from, Assignment.busy_from) <= reservation.busy_from,
                        func.coalesce(Assignment.occupied_until, Assignment.busy_until) >= reservation.busy_until,
                    )),
                )) is not None
        if release:
            session.delete(reservation)
            add_activity(session, 'reservation.released', 'Submission reservation resolved',
                         shipment_id=shipment.shipment_id, action_id=action.action_id)
    session.flush()
    from service.allocation.allocation_holds import reconcile_holds
    reconcile_holds(session)
