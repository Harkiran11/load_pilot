import os
from decimal import Decimal

from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.dispatch.pickup_routing import PickupRouting, PickupRoutingPolicy, PickupUnitRoute
from schema.orders.shipment import Shipment
from schema.terminal.terminal import Terminal
from service.shared.database import PROJECT_ROOT
from service.dispatch.chaining import chaining_candidates
from service.shared.errors import ProductError
from service.orders.expiration import utc
from service.shared.geography import nearest_terminal, pickup_distance_miles, valid_coordinates
from service.dispatch.scheduling import unit_schedule_rejections
from service.dispatch.occupancy import occupancy_policy


def routing_policy() -> PickupRoutingPolicy:
    config = dotenv_values(PROJECT_ROOT / '.env')
    value = os.environ.get('MAX_DEADHEAD_MILES', os.environ.get(
        'MAX_PICKUP_MOVE_MILES', config.get('MAX_DEADHEAD_MILES', config.get('MAX_PICKUP_MOVE_MILES', '100')),
    ))
    try:
        return PickupRoutingPolicy(max_pickup_move_miles=value, **occupancy_policy().model_dump())
    except ValidationError as exc:
        raise ProductError(503, 'MAX_DEADHEAD_MILES / MAX_PICKUP_MOVE_MILES must be a finite non-negative number') from exc


def evaluate_pickup_routing(session: Session, shipment: Shipment) -> PickupRouting:
    result = PickupRouting(
        external_id=shipment.external_id, policy=routing_policy(),
        pickup_at=utc(shipment.pickup_at) if shipment.pickup_at else None,
        delivery_at=utc(shipment.delivery_at) if shipment.delivery_at else None,
    )
    if shipment.board_missing_at is not None:
        result.rejection_reasons.append('order_missing_from_board')
        return result
    terminals = session.scalars(select(Terminal).join(FleetCapacity).where(
        FleetCapacity.board_total_units.is_not(None),
    ).order_by(Terminal.board_terminal_id)).all()
    if shipment.origin_terminal_code:
        terminal = next((row for row in terminals if row.code.upper() == shipment.origin_terminal_code.upper()), None)
        if terminal is None:
            result.rejection_reasons.append('unknown_origin_terminal_code')
            return result
        result.terminal_resolution = 'origin_code'
    else:
        if not valid_coordinates(shipment.origin_latitude, shipment.origin_longitude):
            result.rejection_reasons.append('pickup_coordinates_unknown_or_invalid')
            return result
        terminal = nearest_terminal(terminals, shipment.origin_latitude, shipment.origin_longitude)
        if terminal is None:
            result.rejection_reasons.append('pickup_terminal_unresolved')
            return result
        result.terminal_resolution = 'nearest_origin'
    result.pickup_terminal_id = terminal.terminal_id
    result.pickup_terminal_code = terminal.code
    if not valid_coordinates(shipment.origin_latitude, shipment.origin_longitude):
        result.rejection_reasons.append('pickup_coordinates_unknown_or_invalid')
        return result
    if shipment.pickup_at is None:
        result.rejection_reasons.append('pickup_time_unknown')
        return result
    if shipment.delivery_at is None:
        result.rejection_reasons.append('delivery_time_unknown')
        return result
    if utc(shipment.delivery_at) <= utc(shipment.pickup_at):
        result.rejection_reasons.append('invalid_pickup_delivery_window')
        return result
    all_units = session.scalars(select(FleetUnit).where(
        FleetUnit.board_missing_at.is_(None),
    ).order_by(FleetUnit.employee_code, FleetUnit.board_unit_id)).all()
    result.chaining_units, result.chaining_candidate_unit_id = chaining_candidates(
        session, all_units, shipment, terminal, result.policy.max_pickup_move_miles, result.policy,
    )
    result.chaining_passed = result.chaining_candidate_unit_id is not None
    units = [unit for unit in all_units if unit.home_terminal_id == terminal.terminal_id]
    board_distances = {}
    planned_units = {item.projection.unit_id: item for item in result.chaining_units}
    for unit in units:
        planned = planned_units[unit.unit_id]
        start = planned.occupied_window.occupied_from if planned.occupied_window else shipment.pickup_at
        available = unit.status not in ('maintenance', 'off_duty') and (
            unit.status == 'available'
            or (unit.available_at is not None and utc(unit.available_at) <= utc(shipment.pickup_at))
        )
        reasons = []
        if not available:
            reasons.append('unit_unavailable_at_pickup')
        schedule_reasons = unit_schedule_rejections(unit, start, planned.conflicting_assignments)
        if planned.conflicting_reservations:
            schedule_reasons.append('assignment_window_reserved')
        reasons.extend(schedule_reasons)
        # Current coordinates predict the board selection only. The shared
        # pickup-time projection owns every local distance/cost/arrival check.
        planned = planned_units[unit.unit_id]
        distance = planned.projected_deadhead_miles
        within_limit = distance is not None and distance <= result.policy.max_pickup_move_miles
        if distance is not None and not within_limit:
            reasons.append('pickup_move_exceeds_limit')
        reasons.extend(reason for reason in planned.rejection_reasons if reason not in reasons)
        board_distance = None
        if valid_coordinates(unit.current_latitude, unit.current_longitude):
            board_distance = pickup_distance_miles(
                unit.current_latitude, unit.current_longitude,
                shipment.origin_latitude, shipment.origin_longitude,
            )
            board_distances[unit.unit_id] = board_distance
        else:
            reasons.append('unit_coordinates_unknown_or_invalid')
        result.units.append(PickupUnitRoute(
            unit_id=unit.unit_id, board_unit_id=unit.board_unit_id,
            unit_number=unit.unit_number, projection=planned.projection,
            board_selection_distance_miles=board_distance.quantize(Decimal('0.001')) if board_distance is not None else None,
            distance_miles=distance.quantize(Decimal('0.001')) if distance is not None else None,
            within_move_limit=within_limit, available_at_pickup=bool(available),
            schedule_passed=not schedule_reasons,
            eligible=bool(available) and planned.eligible and not schedule_reasons and board_distance is not None,
            conflicting_assignments=planned.conflicting_assignments,
            conflicting_reservations=planned.conflicting_reservations,
            rejection_reasons=reasons,
        ))
    available_units = [unit for unit in result.units if unit.available_at_pickup]
    if not available_units:
        result.rejection_reasons.append('no_available_units_at_pickup_terminal')
        if result.chaining_passed:
            result.rejection_reasons.append('future_terminal_not_yet_board_selectable')
    elif any(unit.unit_id not in board_distances for unit in available_units):
        # The board chooses the unit; unknown positions prevent verification.
        result.rejection_reasons.append('available_unit_coordinates_unknown_or_invalid')
    else:
        # Match the board's one-decimal distance comparison and employee-order
        # tie-break; apply our maximum to unrounded miles, including ties.
        candidate = min(available_units, key=lambda unit: round(float(board_distances[unit.unit_id]), 1))
        result.routing_candidate_unit_id = candidate.unit_id
        result.schedule_passed = candidate.schedule_passed
        if not candidate.schedule_passed:
            result.rejection_reasons.extend(reason for reason in candidate.rejection_reasons if reason in (
                'unit_status_unavailable', 'unit_available_after_pickup',
                'unit_availability_unknown', 'assignment_window_overlap',
                'assignment_window_reserved',
            ))
        projected = planned_units[candidate.unit_id]
        result.rejection_reasons.extend(reason for reason in projected.rejection_reasons if reason not in result.rejection_reasons)
        if projected.projected_deadhead_miles is not None and not candidate.within_move_limit:
            result.rejection_reasons.append('pickup_move_exceeds_limit')
    result.routing_passed = not result.rejection_reasons
    return result


def pickup_routing(session: Session, external_id: str) -> PickupRouting:
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise ProductError(404, 'Order not found')
    return evaluate_pickup_routing(session, shipment)
