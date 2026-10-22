from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from schema.terminal.terminal import Terminal
from schema.fleet.unit_projection import ProjectedDelivery, UnitProjection
from schema.dispatch.scheduling import PendingBidHold
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc
from service.shared.geography import nearest_terminal, valid_coordinates
from service.dispatch.scheduling import BOOKABLE_UNIT_STATUSES


def project_units(session: Session, units: list[FleetUnit], at: datetime | None = None, *, exclude_shipment_id=None, snapshot_time=None) -> list[UnitProjection]:
    """Derive positions from confirmed bookings, without changing mirrored state."""
    if not units:
        return []
    capacities = {row.terminal_id: row for row in session.scalars(select(FleetCapacity))}
    terminals = session.scalars(select(Terminal).order_by(Terminal.board_terminal_id)).all()
    by_id = {row.terminal_id: row for row in terminals}
    current_terminals = [row for row in terminals if row.terminal_id in capacities
                         and capacities[row.terminal_id].board_total_units is not None]
    by_code = {row.code.upper(): row for row in current_terminals}
    clock = session.get(SimulationClock, 1)
    now = simulated_now(clock) if clock else None
    bookings = {unit.unit_id: [] for unit in units}
    holds = {unit.unit_id: [] for unit in units}
    from service.allocation.allocation_holds import current_holds
    allocation_holds = current_holds(session)
    for hold in session.scalars(select(AssignmentReservation).where(AssignmentReservation.unit_id.in_(holds))):
        if hold.action_id == session.info.get('dispatch_action_id'):
            continue
        holds[hold.unit_id].append(hold)
    rows = session.execute(select(Assignment, Shipment).join(
        Shipment, Shipment.shipment_id == Assignment.shipment_id,
    ).join(FleetUnit, FleetUnit.unit_id == Assignment.unit_id).where(
        Assignment.unit_id.in_(bookings),
        Assignment.lock_retired_at.is_(None),
        Assignment.shipment_id != exclude_shipment_id if exclude_shipment_id is not None else True,
        Assignment.assignment_status.in_((*ACTIVE_ASSIGNMENT_STATUSES, 'completed')),
        Shipment.status.in_(('accepted', 'in_transit', 'completed')),
        Shipment.board_missing_at.is_(None),
        Shipment.board_assigned_unit_id == FleetUnit.board_unit_id,
        Shipment.pickup_at == Assignment.busy_from,
        Shipment.delivery_at == Assignment.busy_until,
    ).order_by(Assignment.busy_from, Assignment.assignment_id)).all()
    for assignment, shipment in rows:
        bookings[assignment.unit_id].append((assignment, shipment))
    trusted_ids = {assignment.assignment_id for assignment, _ in rows}
    unverified = {unit.unit_id: [] for unit in units}
    for assignment in session.scalars(select(Assignment).where(
        Assignment.unit_id.in_(unverified),
        Assignment.lock_retired_at.is_(None),
        Assignment.shipment_id != exclude_shipment_id if exclude_shipment_id is not None else True,
        Assignment.assignment_status.in_(ACTIVE_ASSIGNMENT_STATUSES),
    ).order_by(Assignment.busy_from, Assignment.assignment_id)):
        if assignment.assignment_id not in trusted_ids:
            unverified[assignment.unit_id].append(assignment)
    results = []
    for unit in units:
        capacity = capacities.get(unit.home_terminal_id)
        baseline = utc(capacity.as_of_sim_time) if capacity else utc(snapshot_time) if snapshot_time else None
        projection = UnitProjection(unit_id=unit.unit_id, board_unit_id=unit.board_unit_id,
                                    baseline_sim_time=baseline)
        results.append(projection)
        relevant = [(assignment, shipment) for assignment, shipment in bookings[unit.unit_id]
                    if baseline is not None and (
                        utc(assignment.occupied_until or assignment.busy_until) > baseline
                        or (utc(assignment.busy_until) == baseline
                            and assignment.assignment_status in ACTIVE_ASSIGNMENT_STATUSES
                            and unit.status in ('assigned', 'in_transit'))
                    )]
        for assignment, shipment in relevant:
            terminal = by_code.get((shipment.destination_terminal_code or '').upper())
            resolution = 'destination_code' if terminal else 'nearest_destination'
            terminal = terminal or nearest_terminal(current_terminals, shipment.destination_latitude, shipment.destination_longitude)
            valid = valid_coordinates(shipment.destination_latitude, shipment.destination_longitude)
            projection.deliveries.append(ProjectedDelivery(
                assignment_id=assignment.assignment_id, shipment_id=shipment.shipment_id,
                external_id=shipment.external_id, pickup_at=utc(assignment.busy_from),
                delivery_at=utc(assignment.busy_until),
                occupied_from=utc(assignment.occupied_from or assignment.busy_from),
                available_at=utc(assignment.occupied_until or assignment.busy_until), terminal_id=terminal.terminal_id if terminal else None,
                terminal_code=terminal.code if terminal else None,
                terminal_resolution=resolution if terminal else 'unresolved',
                latitude=shipment.destination_latitude if valid else None,
                longitude=shipment.destination_longitude if valid else None,
            ))
        times = [baseline, now, *[delivery.available_at for delivery in projection.deliveries]]
        projection.at = utc(at) if at is not None else max(
            (value for value in times if value is not None), default=None,
        )
        if unit.board_missing_at is not None:
            projection.rejection_reasons.append('unit_missing_from_board')
            continue
        if baseline is None or projection.at is None:
            projection.rejection_reasons.append('fleet_projection_time_unknown')
            continue
        if projection.at < baseline:
            projection.rejection_reasons.append('projection_before_fleet_snapshot')
            continue
        terminal = by_id.get(unit.home_terminal_id)
        projection.terminal_id = unit.home_terminal_id
        projection.terminal_code = terminal.code if terminal else None
        projection.latitude = unit.current_latitude
        projection.longitude = unit.current_longitude
        projection.available_at = utc(unit.available_at) if unit.available_at else baseline if unit.status == 'available' else None
        projection.source = 'snapshot'
        projection.status = 'available'
        for delivery in projection.deliveries:
            if delivery.delivery_at <= projection.at:
                if delivery.delivery_at >= baseline:
                    projection.terminal_id = delivery.terminal_id
                    projection.terminal_code = delivery.terminal_code
                    projection.latitude = delivery.latitude
                    projection.longitude = delivery.longitude
                    projection.source = 'booked_delivery'
                    projection.source_assignment_id = delivery.assignment_id
                projection.available_at = max(projection.available_at or delivery.available_at, delivery.available_at)
            elif delivery.occupied_from <= projection.at:
                projection.status = 'busy'
                projection.source = 'in_transit'
                projection.source_assignment_id = delivery.assignment_id
                projection.latitude = projection.longitude = None
                projection.available_at = delivery.available_at
                projection.next_terminal_id = delivery.terminal_id
                projection.next_terminal_code = delivery.terminal_code
                break
            else:
                projection.next_terminal_id = delivery.terminal_id
                projection.next_terminal_code = delivery.terminal_code
                break
        if unit.status not in BOOKABLE_UNIT_STATUSES:
            projection.status = 'unavailable'
            projection.rejection_reasons.append('unit_status_unavailable')
        elif projection.available_at is None:
            projection.status = 'unknown'
            projection.rejection_reasons.append('unit_availability_unknown')
        elif projection.available_at > projection.at:
            projection.status = 'busy'
        elif projection.status == 'available' and unit.status in ('assigned', 'in_transit') and projection.source != 'booked_delivery':
            projection.status = 'unknown'
            projection.latitude = projection.longitude = None
            projection.terminal_id = projection.terminal_code = None
            projection.rejection_reasons.append('booked_destination_unknown')
        if projection.status == 'available' and (projection.terminal_id is None or not valid_coordinates(projection.latitude, projection.longitude)):
            projection.status = 'unknown'
            projection.rejection_reasons.append('projected_destination_unknown')
        projection.unverified_assignment_ids = [assignment.assignment_id for assignment in unverified[unit.unit_id]
                                               if baseline < utc(assignment.busy_until) <= projection.at]
        if projection.unverified_assignment_ids:
            projection.status = 'unknown'
            projection.source = 'unknown'
            projection.latitude = projection.longitude = None
            projection.terminal_id = projection.terminal_code = None
            projection.rejection_reasons.append('prior_assignment_destination_unverified')
        projection.uncertain_action_ids = [hold.action_id for hold in holds[unit.unit_id] if utc(hold.busy_from) <= projection.at]
        projection.uncertain_action_ids.extend(hold.action_id for hold in allocation_holds
            if isinstance(hold, PendingBidHold) and hold.unit_id == unit.unit_id and utc(hold.busy_from) <= projection.at)
        projection.uncertain_allocation_ids = [hold.reservation_id for hold in allocation_holds
                                              if not isinstance(hold, PendingBidHold) and hold.unit_id == unit.unit_id and utc(hold.busy_from) <= projection.at]
        if projection.uncertain_action_ids or projection.uncertain_allocation_ids:
            projection.status = 'unknown'
            projection.source = 'unknown'
            projection.latitude = projection.longitude = None
            projection.terminal_id = projection.terminal_code = None
            projection.rejection_reasons.append('unresolved_submission_location')
    return results


def fleet_projections(session: Session, at: datetime | None = None) -> list[UnitProjection]:
    units = session.scalars(select(FleetUnit).where(FleetUnit.board_missing_at.is_(None)).order_by(FleetUnit.unit_number)).all()
    return project_units(session, units, at)


def unit_projection(session: Session, unit_id: UUID, at: datetime | None = None) -> UnitProjection:
    unit = session.get(FleetUnit, unit_id)
    if unit is None:
        raise ProductError(404, 'Fleet unit not found')
    return project_units(session, [unit], at)[0]
