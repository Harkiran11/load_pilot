import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from schema.dispatch.dispatch_policy import DispatchEvaluation, DispatchPolicy
from schema.dispatch.evaluation import Evaluation
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.shared.database import PROJECT_ROOT
from service.pricing.economics import expected_economics
from service.dispatch.dispatch_feedback import add_feedback
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc
from service.dispatch.pickup_routing import evaluate_pickup_routing, routing_policy
from service.pricing.profit_adjustment import ProfitAdjustmentContext, apply_profit_adjustments, profit_adjustment_policy

CHECKS = (
    'offer_open_unexpired', 'pricing_passed', 'deadhead_passed', 'weight_passed',
    'timing_passed', 'location_passed', 'schedule_passed', 'capacity_passed',
)
SCHEDULE_REASONS = {
    'assignment_window_overlap', 'assignment_window_reserved', 'unit_status_unavailable',
    'unit_available_after_pickup', 'unit_availability_unknown', 'unit_unavailable_at_pickup',
    'projected_unit_unavailable', 'unit_available_after_departure',
}


def dispatch_policy() -> DispatchPolicy:
    config = dotenv_values(PROJECT_ROOT / '.env')
    fields = {
        'enforce_margin': ('ENFORCE_MARGIN', 'true'),
        'turnaround_minutes': ('TURNAROUND_MINUTES', '30'),
        'min_expected_margin_pct': ('MIN_EXPECTED_MARGIN_PCT', '15'),
        'min_expected_margin_usd': ('MIN_EXPECTED_MARGIN_USD', '0'),
        'max_weight_lbs': ('MAX_WEIGHT_LBS', '44000'),
        'min_pickup_lead_minutes': ('MIN_PICKUP_LEAD_MINUTES', '0'),
        'pickup_buffer_minutes': ('PICKUP_BUFFER_MINUTES', '0'),
        'assumed_travel_speed_mph': ('ASSUMED_TRAVEL_SPEED_MPH', '45'),
        'max_fleet_snapshot_age_seconds': ('MAX_FLEET_SNAPSHOT_AGE_SECONDS', '120'),
    }
    try:
        return DispatchPolicy(profit_adjustments=profit_adjustment_policy(), max_deadhead_miles=routing_policy().max_pickup_move_miles, **{
            field: os.environ.get(key, config.get(key, default)) for field, (key, default) in fields.items()
        })
    except ValidationError as exc:
        raise ProductError(503, 'Invalid dispatcher policy configuration; check margin, weight, timing, speed, and freshness settings') from exc


def evaluate_dispatch(session: Session, shipment: Shipment, bid_rate=None, unit_id=None, routing=None, adjustment_context=None, *, at_sim_time=None) -> DispatchEvaluation:
    policy = dispatch_policy()
    clock = session.get(SimulationClock, 1)
    if clock is None:
        raise ProductError(503, 'Simulation clock has not been synced yet')
    now = at_sim_time if at_sim_time is not None else simulated_now(clock)
    routing = routing or evaluate_pickup_routing(session, shipment)
    candidate_id = unit_id or routing.routing_candidate_unit_id
    unit = session.get(FleetUnit, candidate_id) if candidate_id else None
    if unit_id is not None and unit is None:
        raise ProductError(404, 'Fleet unit not found')
    candidate = next((item for item in routing.units if item.unit_id == candidate_id), None)
    planned = next((item for item in routing.chaining_units if item.projection.unit_id == candidate_id), None)
    projection = planned.projection if planned else None
    miles = planned.projected_deadhead_miles if planned else None
    result = DispatchEvaluation(
        external_id=shipment.external_id, candidate_unit_id=candidate_id, evaluated_at_sim=now,
        policy=policy, checks={key: True for key in CHECKS},
        economics=expected_economics(
            shipment, miles, bid_rate, projection=projection, timing_policy=routing.policy,
            window=planned.occupied_window if planned else None,
            minimum_profit_usd=policy.min_expected_margin_usd, minimum_margin_pct=policy.min_expected_margin_pct,
        ),
        deadhead_miles=miles, projection=projection, occupied_window=planned.occupied_window if planned else None,
    )

    adjustment_context = adjustment_context or ProfitAdjustmentContext(
        session, policy.profit_adjustments, now, policy.max_fleet_snapshot_age_seconds,
    )
    apply_profit_adjustments(result.economics, shipment, planned, routing, adjustment_context)

    reason_checks = {}

    def fail(check, reason):
        result.checks[check] = False
        if reason not in result.rejection_reasons:
            result.rejection_reasons.append(reason)
            reason_checks[reason] = check

    if shipment.board_missing_at is not None:
        fail('offer_open_unexpired', 'order_missing_from_board')
    if shipment.status != 'open':
        fail('offer_open_unexpired', 'order_not_open')
    if shipment.expires_at is None:
        fail('offer_open_unexpired', 'offer_expiry_unknown')
    elif utc(shipment.expires_at) <= now:
        fail('offer_open_unexpired', 'offer_expired')
    if shipment.weight_lbs is None or shipment.weight_lbs <= 0:
        fail('weight_passed', 'weight_unknown_or_invalid')
    elif shipment.weight_lbs > policy.max_weight_lbs:
        fail('weight_passed', 'maximum_weight_exceeded')

    for reason in routing.rejection_reasons:
        check = 'location_passed'
        if reason in SCHEDULE_REASONS:
            check = 'schedule_passed'
        elif reason in ('pickup_move_exceeds_limit', 'projected_pickup_move_exceeds_limit'):
            check = 'deadhead_passed'
        elif reason in ('pickup_time_unknown', 'delivery_time_unknown', 'invalid_pickup_delivery_window'):
            check = 'timing_passed'
        elif reason == 'no_available_units_at_pickup_terminal':
            check = 'capacity_passed'
        fail(check, reason)
    if candidate_id is None or candidate_id != routing.routing_candidate_unit_id:
        fail('location_passed', 'unit_not_board_selectable')
    if unit is None or unit.board_missing_at is not None:
        fail('location_passed', 'unit_missing_from_board')
    if candidate is None or not candidate.schedule_passed:
        fail('schedule_passed', 'unit_schedule_unavailable')
    if projection is None or projection.status != 'available':
        fail('location_passed', 'projected_location_unavailable')
    if projection is None or projection.terminal_id != routing.pickup_terminal_id:
        fail('location_passed', 'projected_pickup_terminal_mismatch')
    if miles is None:
        fail('deadhead_passed', 'deadhead_distance_unknown')
    elif miles > policy.max_deadhead_miles:
        fail('deadhead_passed', 'maximum_deadhead_exceeded')

    economics = result.economics
    if economics.gross_profit is not None and economics.payout is not None and economics.payout > 0:
        result.margin_pct_for_policy = economics.gross_profit / economics.payout * Decimal('100')
    if not policy.enforce_margin:
        result.skipped_checks.append('pricing_passed')
    elif result.margin_pct_for_policy is None:
        fail('pricing_passed', 'expected_margin_unknown')
        for reason in economics.missing_reasons:
            fail('pricing_passed', reason)
    else:
        if economics.gross_profit < policy.min_expected_margin_usd:
            fail('pricing_passed', 'minimum_margin_usd_not_met')
        if result.margin_pct_for_policy < policy.min_expected_margin_pct:
            fail('pricing_passed', 'minimum_margin_pct_not_met')

    if shipment.pickup_at is None or shipment.delivery_at is None:
        fail('timing_passed', 'booking_times_unknown')
    else:
        pickup = utc(shipment.pickup_at)
        if utc(shipment.delivery_at) <= pickup:
            fail('timing_passed', 'invalid_pickup_delivery_window')
        if Decimal(str((pickup - now).total_seconds())) < policy.min_pickup_lead_minutes * 60:
            fail('timing_passed', 'insufficient_pickup_lead_time')
        if projection is None or projection.available_at is None or miles is None:
            fail('timing_passed', 'pickup_arrival_unknown')
        else:
            start = max(now, projection.available_at)
            result.earliest_pickup_at = start + timedelta(
                hours=float(miles / routing.policy.assumed_travel_speed_mph), minutes=float(policy.pickup_buffer_minutes),
            )
            if result.earliest_pickup_at > pickup:
                fail('timing_passed', 'cannot_reach_pickup_in_time')

        if result.occupied_window is None:
            fail('timing_passed', 'occupied_window_unknown')
        elif result.occupied_window.occupied_from < now:
            fail('timing_passed', 'departure_in_past')

    capacity = session.get(FleetCapacity, routing.pickup_terminal_id) if routing.pickup_terminal_id else None
    result.capacity_units_for_window = sum(item.eligible for item in routing.units)
    if result.capacity_units_for_window < 1:
        fail('capacity_passed', 'no_terminal_capacity_for_window')
    wall_now = datetime.now(timezone.utc)
    if unit is not None and (unit.last_synced_at is None or (wall_now - utc(unit.last_synced_at)).total_seconds() > policy.max_fleet_snapshot_age_seconds):
        fail('location_passed', 'unit_snapshot_stale_or_unknown')
    if capacity is None or capacity.board_total_units is None:
        fail('capacity_passed', 'terminal_capacity_unknown')
    else:
        result.capacity_warnings = [dict(issue) for issue in capacity.discrepancies]
        if any(issue.get('kind') in ('roster_terminal_mismatch', 'terminal_missing_from_snapshot')
               for issue in capacity.discrepancies):
            fail('capacity_passed', 'terminal_capacity_discrepancy')
        if (wall_now - utc(capacity.checked_at)).total_seconds() > policy.max_fleet_snapshot_age_seconds:
            fail('capacity_passed', 'terminal_snapshot_stale')
        units = session.scalars(select(FleetUnit).where(
            FleetUnit.home_terminal_id == routing.pickup_terminal_id, FleetUnit.board_missing_at.is_(None),
        )).all()
        free = sum(unit.status not in ('maintenance', 'off_duty') and (
            unit.status == 'available' or (unit.available_at is not None and utc(unit.available_at) <= utc(capacity.as_of_sim_time))
        ) for unit in units)
        assigned = sum(unit.status == 'assigned' and not (
            unit.available_at is not None and utc(unit.available_at) <= utc(capacity.as_of_sim_time)
        ) for unit in units)
        if (capacity.board_total_units != len(units) or capacity.board_available_units != free
                or capacity.board_assigned_units != assigned or capacity.board_total_units <= 0):
            fail('capacity_passed', 'terminal_capacity_discrepancy')
            result.capacity_warnings.append({
                'kind': 'preflight_capacity_discrepancy', 'board_total': capacity.board_total_units,
                'local_roster_total': len(units), 'board_available': capacity.board_available_units,
                'roster_available': free, 'board_assigned': capacity.board_assigned_units, 'roster_assigned': assigned,
            })
    add_feedback(result, shipment, routing, capacity, unit, reason_checks)
    result.eligible = all(result.checks.values())
    return result


def save_evaluation(session: Session, shipment: Shipment, result: DispatchEvaluation) -> Evaluation:
    economics = result.economics
    row = Evaluation(
        shipment_id=shipment.shipment_id, candidate_unit_id=result.candidate_unit_id,
        payout=economics.payout, loaded_cost=economics.loaded_cost,
        deadhead_miles=result.deadhead_miles, deadhead_cost=economics.projected_deadhead_cost,
        total_cost=economics.total_cost, profit_usd=economics.gross_profit,
        margin_pct=result.margin_pct_for_policy, cost_source=economics.sources.loaded_cost.method,
        pricing_passed=result.checks['pricing_passed'], capacity_passed=result.checks['capacity_passed'],
        schedule_passed=result.checks['schedule_passed'], weight_passed=result.checks['weight_passed'],
        location_passed=result.checks['location_passed'], offer_open_unexpired=result.checks['offer_open_unexpired'],
        eligible=result.eligible, rejection_reasons=result.rejection_reasons,
        policy_snapshot=result.model_dump(mode='json'), evaluated_at_sim=result.evaluated_at_sim,
        evaluated_at_wall=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def dispatch_evaluation(session: Session, external_id: str, bid_rate=None, unit_id=None) -> DispatchEvaluation:
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise ProductError(404, 'Order not found')
    if bid_rate is not None and shipment.order_type != 'open_bid':
        raise ProductError(422, 'bid_rate is only valid for open-bid orders')
    return evaluate_dispatch(session, shipment, bid_rate, unit_id)
