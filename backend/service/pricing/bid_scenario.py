from decimal import Decimal, localcontext

from sqlalchemy import select

from schema.pricing.bid_scenario import BidScenario, BidScenarioReport
from schema.orders.shipment import Shipment
from service.pricing.economics import CALCULATION_CONTEXT, money
from service.shared.errors import ProductError
from service.dispatch.ranking import RankingContext, rank_order


def bid_scenarios(session, external_id, request):
    with localcontext(CALCULATION_CONTEXT):
        return _bid_scenarios(session, external_id, request)


def _bid_scenarios(session, external_id, request):
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise ProductError(404, 'Order not found')
    if shipment.order_type != 'open_bid':
        raise ProductError(422, 'Bid scenarios require an open-bid order')
    context = RankingContext(session, request.unit_id)
    base, _ = rank_order(session, shipment, context)
    result = BidScenarioReport(external_id=external_id, rank_by=request.rank_by, market_context=base.market_context)
    if base.selected is None:
        result.rejection_reasons = base.rejection_reasons or ['no_feasible_selected_unit']
        return result
    result.reference_economics = base.selected.economics
    # Keep unit, projected miles, and occupancy fixed across price comparisons.
    context.unit_id = base.selected.unit_id
    prices = {}

    def add(value, label):
        if value is not None and value > 0:
            rate = money(value)
            if rate > 0:
                prices.setdefault(rate, []).append(label)

    for value in request.entered_bids:
        if money(value) <= 0:
            raise ProductError(422, 'Entered bids must round to at least one cent')
        add(value, 'entered_bid')
    if request.include_suggestions:
        floor = base.selected.economics.minimum_profitable_bid
        add(max(Decimal('.01'), floor) if floor is not None else None, 'minimum_cash_bid')
        if base.market_context.sufficient_samples and floor is not None:
            add(max(base.market_context.payout.median, floor), 'historical_median_payout_with_cash_floor')
            median_rate = base.market_context.rate_per_mile.median
            if median_rate is not None and shipment.distance_miles is not None and shipment.distance_miles > 0:
                add(max(median_rate * shipment.distance_miles, floor), 'historical_median_rate_per_mile_with_cash_floor')
    for rate, labels in prices.items():
        context.bid_rate = rate
        report, _ = rank_order(session, shipment, context)
        selected = report.selected
        if selected is None:
            continue
        economics = selected.economics
        policy = report.dispatch_evaluation.policy
        cash_met = (economics.gross_profit is not None and economics.payout is not None and economics.payout > 0
                    and economics.gross_profit >= policy.min_expected_margin_usd
                    and economics.gross_profit * 100 >= policy.min_expected_margin_pct * economics.payout)
        result.scenarios.append(BidScenario(bid_rate=rate, labels=list(dict.fromkeys(labels)),
            unit_id=selected.unit_id, economics=economics, cash_requirements_met=cash_met,
            booking_ready=selected.booking_ready, rejection_reasons=selected.dispatch_rejection_reasons))
    if not prices:
        result.rejection_reasons.append('no_bid_rates_available')
    result.scenarios.sort(key=lambda scenario: (
        not scenario.cash_requirements_met, not scenario.booking_ready,
        getattr(scenario.economics, request.rank_by) is None,
        -(getattr(scenario.economics, request.rank_by) or Decimal('0')), scenario.bid_rate,
    ))
    for rank, scenario in enumerate(result.scenarios, 1):
        scenario.rank = rank
    return result
