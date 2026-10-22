import os
from datetime import timedelta
from decimal import Decimal

from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from schema.dispatch.assignment import ACTIVE_ASSIGNMENT_STATUSES, Assignment
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.dispatch.recommendation import (
    OrderRecommendations, RankedMatch, RankingPolicy, RecommendationsPage, RejectedUnit,
)
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from schema.terminal.terminal import Terminal
from service.shared.database import PROJECT_ROOT
from service.shared.errors import ProductError
from service.dispatch.dispatch_policy import dispatch_policy, evaluate_dispatch
from service.pricing.profit_adjustment import ProfitAdjustmentContext, apply_profit_adjustments
from service.pricing.market_context import MarketContext
from service.dispatch.dispatch_feedback import rejection_detail
from service.pricing.economics import expected_economics
from service.orders.expiration import simulated_now, utc
from service.shared.geography import nearest_terminal
from service.dispatch.pickup_routing import evaluate_pickup_routing

ZERO = Decimal('0')
ONE = Decimal('1')
SIX_PLACES = Decimal('0.000001')


def ranking_policy() -> RankingPolicy:
    config = dotenv_values(PROJECT_ROOT / '.env')
    fields = {
        'profit_weight': ('RANK_PROFIT_WEIGHT', '0.40'),
        'profit_target_per_hour': ('RANK_PROFIT_TARGET_PER_HOUR', '100'),
        'empty_miles_weight': ('RANK_EMPTY_MILES_WEIGHT', '0.60'),
        'continuity_weight': ('RANK_CONTINUITY_WEIGHT', '0.25'),
        'utilization_weight': ('RANK_UTILIZATION_WEIGHT', '0.15'),
        'utilization_horizon_hours': ('RANK_UTILIZATION_HORIZON_HOURS', '24'),
    }
    try:
        return RankingPolicy(**{
            field: os.environ.get(key, config.get(key, default)) for field, (key, default) in fields.items()
        })
    except ValidationError as exc:
        raise ProductError(503, 'Invalid ranking policy: weights must be finite values from 0 to 1 with a positive sum; horizon must be 1–168 hours') from exc


def occupied_seconds(intervals, start, end) -> Decimal:
    """Clip and merge intervals so historical overlap is never counted twice."""
    clipped = sorted((max(utc(left), start), min(utc(right), end)) for left, right in intervals
                     if utc(left) < end and utc(right) > start)
    seconds = ZERO
    previous = None
    for left, right in clipped:
        if previous is None:
            previous = (left, right)
        elif left <= previous[1]:
            previous = (previous[0], max(previous[1], right))
        else:
            seconds += Decimal(str((previous[1] - previous[0]).total_seconds()))
            previous = (left, right)
    if previous:
        seconds += Decimal(str((previous[1] - previous[0]).total_seconds()))
    return seconds


class RankingContext:
    def __init__(self, session: Session, unit_id=None, bid_rate=None):
        if unit_id is not None and session.get(FleetUnit, unit_id) is None:
            raise ProductError(404, 'Fleet unit not found')
        clock = session.get(SimulationClock, 1)
        if clock is None:
            raise ProductError(503, 'Simulation clock has not been synced yet')
        self.now = simulated_now(clock)
        dispatch = dispatch_policy()
        self.market = MarketContext(session, dispatch.profit_adjustments, self.now)
        self.adjustments = ProfitAdjustmentContext(session, dispatch.profit_adjustments, self.now,
                                                  dispatch.max_fleet_snapshot_age_seconds)
        self.policy = ranking_policy()
        self.end = self.now + timedelta(hours=float(self.policy.utilization_horizon_hours))
        self.unit_id = unit_id
        self.bid_rate = bid_rate
        self.units = {row.unit_id: row for row in session.scalars(select(FleetUnit))}
        self.bookings = {key: [] for key in self.units}
        rows = session.execute(select(Assignment, Shipment).join(
            Shipment, Shipment.shipment_id == Assignment.shipment_id,
        ).join(FleetUnit, FleetUnit.unit_id == Assignment.unit_id).where(
            Assignment.assignment_status.in_((*ACTIVE_ASSIGNMENT_STATUSES, 'completed')),
            Shipment.status.in_(('accepted', 'in_transit', 'completed')),
            Shipment.board_missing_at.is_(None),
            Shipment.board_assigned_unit_id == FleetUnit.board_unit_id,
            Shipment.pickup_at == Assignment.busy_from,
            Shipment.delivery_at == Assignment.busy_until,
        ).order_by(Assignment.busy_until, Assignment.assignment_id)).all()
        for assignment, shipment in rows:
            self.bookings[assignment.unit_id].append((assignment, shipment))
        self.terminals = session.scalars(select(Terminal).join(FleetCapacity).where(
            FleetCapacity.board_total_units.is_not(None),
        ).order_by(Terminal.board_terminal_id)).all()
        self.by_code = {row.code.upper(): row for row in self.terminals}


def rank_order(session: Session, shipment: Shipment, context: RankingContext):
    report = OrderRecommendations(
        market_context=context.market.for_order(shipment),
        external_id=shipment.external_id, evaluated_at_sim=context.now,
        horizon_end=context.end, policy=context.policy,
        entered_bid_rate=context.bid_rate,
    )
    if shipment.board_missing_at is not None:
        report.rejection_reasons.append('order_missing_from_board')
    if context.unit_id is not None and context.units[context.unit_id].board_missing_at is not None:
        report.rejection_reasons.append('unit_missing_from_board')
    if shipment.status != 'open':
        report.rejection_reasons.append('order_not_open')
    if shipment.expires_at is None:
        report.rejection_reasons.append('offer_expiry_unknown')
    elif utc(shipment.expires_at) <= context.now:
        report.rejection_reasons.append('offer_expired')
    if shipment.pickup_at is not None and utc(shipment.pickup_at) < context.now:
        report.rejection_reasons.append('pickup_in_past')
    if report.rejection_reasons:
        return report, []
    routing = evaluate_pickup_routing(session, shipment)
    checked = evaluate_dispatch(session, shipment, context.bid_rate, context.unit_id, routing, context.adjustments)
    report.dispatch_evaluation = checked
    scored = []
    for item in routing.chaining_units:
        projection = item.projection
        if context.unit_id is not None and projection.unit_id != context.unit_id:
            continue
        if not item.eligible:
            report.rejected_units.append(RejectedUnit(unit_id=projection.unit_id, rejection_reasons=item.rejection_reasons))
            continue
        unit = context.units[projection.unit_id]
        miles = item.projected_deadhead_miles
        maximum = routing.policy.max_pickup_move_miles
        empty_score = max(ZERO, ONE - miles / maximum) if maximum else ONE
        previous = next(((assignment, order) for assignment, order in reversed(context.bookings[unit.unit_id])
                         if utc(assignment.busy_until) <= utc(shipment.pickup_at)), None)
        previous_id = None
        gap = None
        continuity = ZERO
        continues = False
        if previous:
            assignment, order = previous
            previous_id = assignment.assignment_id
            destination = context.by_code.get((order.destination_terminal_code or '').upper())
            destination = destination or nearest_terminal(context.terminals, order.destination_latitude, order.destination_longitude)
            gap = Decimal(str((item.occupied_window.occupied_from - utc(assignment.occupied_until or assignment.busy_until)).total_seconds()))
            continues = destination is not None and destination.terminal_id == routing.pickup_terminal_id
            if continues:
                continuity = ONE / (ONE + gap / Decimal('3600'))
        intervals = [(assignment.occupied_from or assignment.busy_from, assignment.occupied_until or assignment.busy_until) for assignment, _ in context.bookings[unit.unit_id]]
        before = occupied_seconds(intervals, context.now, context.end)
        after = occupied_seconds([*intervals, (item.occupied_window.occupied_from, item.occupied_window.occupied_until)], context.now, context.end)
        horizon = Decimal(str((context.end - context.now).total_seconds()))
        utilization = after / horizon
        policy = context.policy
        economics = expected_economics(
            shipment, miles, context.bid_rate, projection=projection, timing_policy=routing.policy,
            window=item.occupied_window, minimum_profit_usd=checked.policy.min_expected_margin_usd,
            minimum_margin_pct=checked.policy.min_expected_margin_pct,
        )
        apply_profit_adjustments(economics, shipment, item, routing, context.adjustments)
        ranking_economics = economics
        ranking_bid = context.bid_rate
        payout_basis = 'fixed_offer' if shipment.order_type == 'fixed_rate' else 'entered_bid'
        if shipment.order_type == 'open_bid' and ranking_bid is None:
            floor = economics.minimum_profitable_bid
            ranking_bid = max(Decimal('.01'), floor) if floor is not None else None
            payout_basis = 'suggested_cash_floor'
            if ranking_bid is not None and report.market_context.sufficient_samples:
                ranking_bid = max(ranking_bid, report.market_context.payout.median)
                payout_basis = 'historical_median_with_cash_floor'
            if ranking_bid is not None:
                ranking_economics = expected_economics(
                    shipment, miles, ranking_bid, projection=projection, timing_policy=routing.policy,
                    window=item.occupied_window, minimum_profit_usd=checked.policy.min_expected_margin_usd,
                    minimum_margin_pct=checked.policy.min_expected_margin_pct,
                )
        cash_met = (ranking_economics.payout is not None and ranking_economics.payout > 0
                    and ranking_economics.gross_profit is not None
                    and ranking_economics.gross_profit >= checked.policy.min_expected_margin_usd
                    and ranking_economics.gross_profit * 100 >= checked.policy.min_expected_margin_pct * ranking_economics.payout)
        profit_score = (min(ONE, max(ZERO, ranking_economics.profit_per_hour / policy.profit_target_per_hour))
                        if cash_met and ranking_economics.profit_per_hour is not None else None)
        score = (empty_score * policy.empty_miles_weight + continuity * policy.continuity_weight
                 + utilization * policy.utilization_weight + (profit_score or ZERO) * policy.profit_weight) / (
                     policy.empty_miles_weight + policy.continuity_weight + policy.utilization_weight + policy.profit_weight)
        match = RankedMatch(
            profit_score=profit_score.quantize(SIX_PLACES) if profit_score is not None else None,
            ranking_cash_requirements_met=bool(cash_met), ranking_payout_basis=payout_basis,
            ranking_bid_rate=ranking_bid, ranking_economics=ranking_economics,
            external_id=shipment.external_id, shipment_id=shipment.shipment_id,
            unit_id=unit.unit_id, board_unit_id=unit.board_unit_id, unit_number=unit.unit_number,
            pickup_at=utc(shipment.pickup_at), delivery_at=utc(shipment.delivery_at),
            pickup_terminal_code=routing.pickup_terminal_code,
            projected_latitude=projection.latitude, projected_longitude=projection.longitude,
            position_source=projection.source, source_assignment_id=projection.source_assignment_id,
            deadhead_miles=miles.quantize(Decimal('0.001')),
            cost_per_mile=economics.cost_per_mile, deadhead_cost=economics.projected_deadhead_cost,
            cost_missing_reason=None if economics.cost_per_mile is not None else 'cost_per_mile_unknown_or_invalid',
            economics=economics,
            empty_miles_score=empty_score.quantize(SIX_PLACES), continues_previous_route=continues,
            previous_assignment_id=previous_id, idle_gap_seconds=gap,
            continuity_score=continuity.quantize(SIX_PLACES),
            booked_hours=(before / Decimal('3600')).quantize(SIX_PLACES),
            added_loaded_hours=((after - before) / Decimal('3600')).quantize(SIX_PLACES),
            utilization_before=(before / horizon).quantize(SIX_PLACES),
            utilization_after=utilization.quantize(SIX_PLACES), score=score.quantize(SIX_PLACES),
            booking_ready=checked.eligible and routing.routing_candidate_unit_id == unit.unit_id,
            dispatch_rejection_reasons=checked.rejection_reasons if routing.routing_candidate_unit_id == unit.unit_id else ['unit_not_board_selectable'],
            dispatch_rejection_details=checked.rejection_details if routing.routing_candidate_unit_id == unit.unit_id else [
                rejection_detail('unit_not_board_selectable', 'location_passed', {'unit_id': unit.unit_id}),
            ],
            dispatch_primary_rejection=checked.primary_rejection if routing.routing_candidate_unit_id == unit.unit_id else
                rejection_detail('unit_not_board_selectable', 'location_passed', {'unit_id': unit.unit_id}),
        )
        scored.append((score, miles, unit.employee_code, match))
    scored.sort(key=lambda row: (not row[3].ranking_cash_requirements_met if context.policy.profit_weight else False,
                               -row[0], row[1], shipment.external_id, row[2], row[3].board_unit_id))
    for rank, (_, _, _, match) in enumerate(scored, 1):
        match.rank = rank
    report.ranked_units = [row[3] for row in scored]
    # The board chooses the truck on booking. A higher-ranked advisory truck
    # must not replace that truck in the action preview: the frontend gates
    # Accept on selected.booking_ready and displays its costs.
    report.selected = next((match for match in report.ranked_units
                            if match.unit_id == checked.candidate_unit_id), None)
    if report.selected is None and report.ranked_units:
        report.selected = report.ranked_units[0]
    if report.selected is None:
        report.rejection_reasons = routing.rejection_reasons or ['no_feasible_units']
    return report, scored


def order_recommendations(session: Session, external_id: str, unit_id=None, bid_rate=None) -> OrderRecommendations:
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise ProductError(404, 'Order not found')
    if bid_rate is not None and shipment.order_type != 'open_bid':
        raise ProductError(422, 'bid_rate is only valid for open-bid orders')
    return rank_order(session, shipment, RankingContext(session, unit_id, bid_rate))[0]


def recommendations(session: Session, limit=50, offset=0, unit_id=None,
                    order_type=None, origin_terminal=None, bookable_only=False) -> RecommendationsPage:
    context = RankingContext(session, unit_id)
    statement = select(Shipment).where(Shipment.status == 'open', Shipment.board_missing_at.is_(None)).order_by(Shipment.external_id)
    if order_type:
        statement = statement.where(Shipment.order_type == order_type)
    scored = []
    for shipment in session.scalars(statement):
        scored.extend(rank_order(session, shipment, context)[1])
    if origin_terminal:
        scored = [row for row in scored if row[3].pickup_terminal_code.upper() == origin_terminal.upper()]
    if bookable_only:
        scored = [row for row in scored if row[3].booking_ready]
    scored.sort(key=lambda row: (not row[3].ranking_cash_requirements_met if context.policy.profit_weight else False,
                               -row[0], row[1], row[3].external_id, row[2], row[3].board_unit_id))
    for rank, (_, _, _, match) in enumerate(scored, 1):
        match.rank = rank
    return RecommendationsPage(
        items=[row[3] for row in scored[offset:offset + limit]], total=len(scored),
        limit=limit, offset=offset, evaluated_at_sim=context.now, horizon_end=context.end, policy=context.policy,
    )
