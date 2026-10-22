from decimal import Decimal

from schema.fleet.unit_projection import ChainingUnit
from service.shared.geography import pickup_distance_miles, valid_coordinates
from service.dispatch.scheduling import reservation_conflicts, schedule_conflicts
from service.fleet.unit_projection import project_units
from service.dispatch.occupancy import occupied_window, occupancy_policy
from service.orders.expiration import utc


def chaining_candidates(session, units, shipment, terminal, max_move, timing_policy=None):
    timing_policy = timing_policy or occupancy_policy()
    projections = project_units(session, units, shipment.pickup_at)
    by_id = {unit.unit_id: unit for unit in units}
    items = []
    distances = {}
    for projection in projections:
        item = ChainingUnit(
            projection=projection,
            at_current_pickup_hub=by_id[projection.unit_id].home_terminal_id == terminal.terminal_id,
            rejection_reasons=list(projection.rejection_reasons),
        )
        items.append(item)
        if projection.status != 'available':
            item.rejection_reasons.append('projected_unit_unavailable')
        if projection.terminal_id != terminal.terminal_id:
            item.rejection_reasons.append('projected_pickup_terminal_mismatch')
        if valid_coordinates(projection.latitude, projection.longitude):
            distance = pickup_distance_miles(projection.latitude, projection.longitude,
                                             shipment.origin_latitude, shipment.origin_longitude)
            distances[projection.unit_id] = distance
            item.projected_deadhead_miles = distance
            item.distance_miles = distance.quantize(Decimal('0.001'))
            if distance > max_move:
                item.rejection_reasons.append('projected_pickup_move_exceeds_limit')
        else:
            item.rejection_reasons.append('projected_location_unknown')
        item.occupied_window = occupied_window(shipment, item.projected_deadhead_miles, timing_policy)
    starts = [item.occupied_window.occupied_from for item in items if item.occupied_window]
    ends = [item.occupied_window.occupied_until for item in items if item.occupied_window]
    conflicts = schedule_conflicts(session, list(by_id), min(starts, default=shipment.pickup_at), max(ends, default=shipment.delivery_at))
    reservations = reservation_conflicts(session, list(by_id), min(starts, default=shipment.pickup_at), max(ends, default=shipment.delivery_at))
    for item in items:
        projection = item.projection
        start = item.occupied_window.occupied_from if item.occupied_window else utc(shipment.pickup_at)
        end = item.occupied_window.occupied_until if item.occupied_window else utc(shipment.delivery_at)
        item.conflicting_assignments = [row for row in conflicts[projection.unit_id] if row.busy_from < end and row.busy_until > start]
        item.conflicting_reservations = [row for row in reservations[projection.unit_id] if row.busy_from < end and row.busy_until > start]
        if item.occupied_window and projection.available_at and projection.available_at > start:
            item.rejection_reasons.append('unit_available_after_departure')
        if item.conflicting_assignments:
            item.rejection_reasons.append('assignment_window_overlap')
        if item.conflicting_reservations:
            item.rejection_reasons.append('assignment_window_reserved')
        item.eligible = not item.rejection_reasons
    eligible = [item for item in items if item.eligible]
    candidate = min(eligible, key=lambda item: (
        distances[item.projection.unit_id], by_id[item.projection.unit_id].employee_code,
        by_id[item.projection.unit_id].board_unit_id,
    )) if eligible else None
    return items, candidate.projection.unit_id if candidate else None
