from datetime import timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select

from schema.analytics.historical_shipment import HistoricalShipment
from schema.pricing.market_context import HistoricalMarketContext, HistoricalWinContext, MarketStatistics
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.pricing.economics import CALCULATION_CONTEXT, money, non_negative
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc
from service.shared.geography import pickup_distance_miles, valid_coordinates
from service.pricing.profit_adjustment import profit_adjustment_policy


def statistics(values):
    if not values:
        return MarketStatistics()
    values = sorted(values)
    size = len(values)
    mean = sum(values, Decimal('0')) / size
    median = values[size // 2] if size % 2 else (values[size // 2 - 1] + values[size // 2]) / 2
    variance = sum(((value - mean) ** 2 for value in values), Decimal('0')) / size
    return MarketStatistics(sample_count=size, median=money(median), mean=money(mean),
        minimum=money(values[0]), maximum=money(values[-1]), population_stddev=money(variance.sqrt()))


class MarketContext:
    def __init__(self, session, policy, now):
        self.session, self.policy, self.now = session, policy, utc(now)
        self.since = self.now - timedelta(days=policy.historical_lookback_days)
        self.rows = None

    def for_order(self, shipment):
        with localcontext(CALCULATION_CONTEXT):
            return self._calculate(shipment)

    def _calculate(self, shipment):
        geographical = (valid_coordinates(shipment.origin_latitude, shipment.origin_longitude)
                        and valid_coordinates(shipment.destination_latitude, shipment.destination_longitude))
        result = HistoricalMarketContext(origin_state=shipment.origin_state, destination_state=shipment.destination_state,
            order_type=shipment.order_type, matching_method='directed_state_pair_and_endpoint_radius' if geographical else 'directed_state_pair',
            since_sim=self.since, through_sim=self.now, radius_miles=self.policy.historical_origin_radius_miles,
            minimum_samples=self.policy.historical_min_samples)
        if not shipment.origin_state or not shipment.destination_state:
            result.warnings.append('Origin and destination states are required; no lane match was inferred.')
            return result
        if not geographical:
            result.warnings.append('Order coordinates are missing; this is a broader state-pair summary, not an exact geographic lane.')
        if self.rows is None:
            self.rows = list(self.session.scalars(select(HistoricalShipment).where(
                HistoricalShipment.shipped_at >= self.since, HistoricalShipment.shipped_at <= self.now,
            ).order_by(HistoricalShipment.source_record_key)))
        rows = []
        for row in self.rows:
            if (row.order_type != shipment.order_type or row.origin_state.upper() != shipment.origin_state.upper()
                    or row.destination_state.upper() != shipment.destination_state.upper()):
                continue
            if geographical:
                if not (valid_coordinates(row.origin_latitude, row.origin_longitude)
                        and valid_coordinates(row.destination_latitude, row.destination_longitude)):
                    continue
                if (pickup_distance_miles(row.origin_latitude, row.origin_longitude, shipment.origin_latitude, shipment.origin_longitude) > result.radius_miles
                    or pickup_distance_miles(row.destination_latitude, row.destination_longitude, shipment.destination_latitude, shipment.destination_longitude) > result.radius_miles):
                    continue
            rows.append(row)
        result.sample_count = len(rows)
        result.lane_ids = sorted({row.lane_id for row in rows})
        payouts, rates = [], []
        for row in rows:
            payout = row.bid_rate if row.order_type == 'open_bid' else row.offered_rate
            if row.won and non_negative(payout):
                payouts.append(payout)
                if non_negative(row.distance_miles) and row.distance_miles > 0:
                    rates.append(payout / row.distance_miles)
        result.payout, result.rate_per_mile = statistics(payouts), statistics(rates)
        result.sufficient_samples = len(payouts) >= result.minimum_samples
        if shipment.order_type == 'open_bid':
            attempts = [row for row in rows if non_negative(row.bid_rate) and row.bid_rate > 0]
            wins = sum(row.won for row in attempts)
            result.historical_win = HistoricalWinContext(sample_count=len(attempts), wins=wins,
                probability=(Decimal(wins) / len(attempts)).quantize(Decimal('.000001'))
                if len(attempts) >= result.minimum_samples else None)
        if not result.sufficient_samples:
            result.warnings.append('Insufficient won payout samples for market-based rate suggestions; summaries remain descriptive.')
        return result


def order_market_context(session, external_id):
    shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if shipment is None:
        raise ProductError(404, 'Order not found')
    clock = session.get(SimulationClock, 1)
    if clock is None:
        raise ProductError(503, 'Simulation clock has not been synced yet')
    return MarketContext(session, profit_adjustment_policy(), simulated_now(clock)).for_order(shipment)
