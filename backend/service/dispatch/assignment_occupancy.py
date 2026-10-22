from types import SimpleNamespace

from sqlalchemy import select

from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.dispatch.dispatch_action import DispatchAction
from schema.dispatch.evaluation import Evaluation
from schema.dispatch.occupancy import OccupancyPolicy
from service.orders.expiration import utc
from service.shared.geography import pickup_distance_miles, valid_coordinates
from service.dispatch.occupancy import minutes_delta, occupied_window, occupancy_policy


def assignment_occupied_bounds(session, shipment, unit, assignment, sim_time):
    """Latch an actual board unit's local occupancy, preserving committed padding."""
    if (assignment is not None and assignment.occupied_from is not None
            and assignment.unit_id == unit.unit_id
            and utc(assignment.busy_from) == utc(shipment.pickup_at)
            and utc(assignment.busy_until) == utc(shipment.delivery_at)):
        return assignment.occupied_from, assignment.occupied_until
    hold = session.scalar(select(AssignmentReservation).join(DispatchAction).where(
        DispatchAction.shipment_id == shipment.shipment_id, AssignmentReservation.unit_id == unit.unit_id,
    ))
    if hold and utc(hold.busy_from) <= utc(shipment.pickup_at) and utc(hold.busy_until) >= utc(shipment.delivery_at):
        return hold.busy_from, hold.busy_until

    from service.fleet.unit_projection import project_units
    # The board has now assigned this very load; its resulting status/availability
    # must not be mistaken for a preceding commitment or a pre-pickup position.
    baseline = SimpleNamespace(**{name: getattr(unit, name) for name in (
        'unit_id', 'board_unit_id', 'home_terminal_id', 'board_missing_at',
        'current_latitude', 'current_longitude',
    )}, status='available', available_at=None)
    projection = project_units(session, [baseline], shipment.pickup_at, exclude_shipment_id=shipment.shipment_id, snapshot_time=sim_time)[0]
    action = session.scalar(select(DispatchAction).where(
        DispatchAction.shipment_id == shipment.shipment_id, DispatchAction.evaluation_id.is_not(None),
    ).order_by(DispatchAction.requested_at.desc(), DispatchAction.action_id.desc()))
    evaluation = session.get(Evaluation, action.evaluation_id) if action else None
    policy = (OccupancyPolicy.model_validate(evaluation.policy_snapshot['policy'])
              if evaluation and 'policy' in evaluation.policy_snapshot else occupancy_policy())
    if valid_coordinates(projection.latitude, projection.longitude) and valid_coordinates(shipment.origin_latitude, shipment.origin_longitude):
        miles = pickup_distance_miles(projection.latitude, projection.longitude,
                                     shipment.origin_latitude, shipment.origin_longitude)
        window = occupied_window(shipment, miles, policy)
        if window:
            return window.occupied_from, window.occupied_until
    # Unknown historic/pre-pickup position: do not invent a distance. Block all
    # remaining time to delivery plus turnaround rather than under-reserving it.
    start = min(utc(sim_time), utc(shipment.pickup_at), utc(hold.busy_from) if hold else utc(sim_time))
    return start, utc(shipment.delivery_at) + minutes_delta(policy.turnaround_minutes)
