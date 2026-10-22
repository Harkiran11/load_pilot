"""Explainable planning estimates; never used to satisfy cash-margin checks."""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import select

from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.analytics.historical_shipment import HistoricalShipment
from schema.pricing.profit_adjustment import ProfitAdjustmentDetails, ProfitAdjustmentPolicy
from schema.terminal.terminal import Terminal
from service.shared.database import PROJECT_ROOT
from service.pricing.economics import CALCULATION_CONTEXT, missing, money, non_negative, source
from service.shared.errors import ProductError
from service.orders.expiration import utc
from service.shared.geography import nearest_terminal, pickup_distance_miles, valid_coordinates
from service.dispatch.scheduling import reservation_conflicts, schedule_conflicts
from service.fleet.unit_projection import project_units


def profit_adjustment_policy():
    config = dotenv_values(PROJECT_ROOT / '.env')
    defaults = ProfitAdjustmentPolicy().model_dump()
    try:
        return ProfitAdjustmentPolicy(**{
            name: os.environ.get(name.upper(), config.get(name.upper(), default))
            for name, default in defaults.items()
        })
    except ValidationError as exc:
        raise ProductError(503, 'Invalid profit adjustment configuration; check opportunity, positioning, and historical settings') from exc


class ProfitAdjustmentContext:
    """Cache history and time-specific fleet projections for a recommendation batch."""

    def __init__(self, session, policy, now, max_snapshot_age_seconds=120):
        self.session, self.policy, self.now = session, policy, utc(now)
        self.wall_now = datetime.now(timezone.utc)
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self.units = list(session.scalars(select(FleetUnit).where(FleetUnit.board_missing_at.is_(None))))
        self.capacities = {row.terminal_id: row for row in session.scalars(select(FleetCapacity).where(
            FleetCapacity.board_total_units.is_not(None),
        ))}
        self.terminals = list(session.scalars(select(Terminal).where(
            Terminal.terminal_id.in_(self.capacities),
        ).order_by(Terminal.board_terminal_id)))
        self.by_code = {terminal.code.upper(): terminal for terminal in self.terminals}
        self.projections = {}
        self.capacity_estimates = {}
        self.history = None
        self.baselines = {}

    def at(self, time):
        time = utc(time)
        if time not in self.projections:
            rows = project_units(self.session, self.units, time)
            ids = [row.unit_id for row in rows]
            end = time + timedelta(microseconds=1)
            locks = schedule_conflicts(self.session, ids, time, end)
            holds = reservation_conflicts(self.session, ids, time, end)
            for row in rows:
                if row.status == 'available' and (locks[row.unit_id] or holds[row.unit_id]):
                    row.status = 'busy'
            self.projections[time] = rows
        return self.projections[time]

    def capacity(self, terminal, time, selected_unit_id, *, arriving=False):
        key = (terminal.terminal_id, utc(time), selected_unit_id, arriving)
        if key in self.capacity_estimates:
            return self.capacity_estimates[key]
        projections = self.at(time)
        unknown = [str(row.unit_id) for row in projections if row.status == 'unknown']
        def fresh(value):
            return value is not None and 0 <= (self.wall_now - utc(value)).total_seconds() <= self.max_snapshot_age_seconds

        stale = [str(unit.unit_id) for unit in self.units if not fresh(unit.last_synced_at)]
        stale_capacities = [terminal.code for terminal in self.terminals
                            if not fresh(self.capacities[terminal.terminal_id].checked_at)]
        incomplete = [terminal.code for terminal in self.terminals
                      if self.capacities[terminal.terminal_id].board_total_units is None
                      or self.capacities[terminal.terminal_id].board_total_units != sum(
                          unit.home_terminal_id == terminal.terminal_id for unit in self.units)]
        members = [row for row in projections if row.unit_id != selected_unit_id and row.terminal_id == terminal.terminal_id]
        total = len(members)
        free = sum(row.status == 'available' for row in members)
        # Model the proposed unit at its departure/arrival terminal, while
        # retaining any separate booking that begins exactly when it is free.
        selected_free = True
        if arriving:
            end = time + timedelta(microseconds=1)
            selected_free = not (schedule_conflicts(self.session, [selected_unit_id], time, end)[selected_unit_id]
                                 or reservation_conflicts(self.session, [selected_unit_id], time, end)[selected_unit_id])
        total += 1
        free += int(selected_free)
        details = {'terminal_code': terminal.code, 'at_sim': time.isoformat(),
                   'total_units': total, 'available_units': free,
                   'includes_proposed_unit': True, 'proposed_unit_available': selected_free,
                   'unknown_unit_ids': unknown, 'stale_unit_ids': stale,
                   'stale_capacity_terminal_codes': stale_capacities,
                   'freshness_basis': 'wall_clock_utc',
                   'incomplete_terminal_codes': incomplete,
                   'explanation': 'Projected local fleet and confirmed occupied windows, including the proposed move; unknown positions are not assumed free.'}
        result = (details, not unknown and not stale and not stale_capacities and not incomplete)
        self.capacity_estimates[key] = result
        return result

    def historical_baseline(self, terminal):
        if terminal.terminal_id in self.baselines:
            return self.baselines[terminal.terminal_id]
        policy = self.policy
        since = self.now - timedelta(days=policy.historical_lookback_days)
        if self.history is None:
            self.history = list(self.session.scalars(select(HistoricalShipment).where(
                HistoricalShipment.won.is_(True), HistoricalShipment.shipped_at >= since,
                HistoricalShipment.shipped_at <= self.now,
            ).order_by(HistoricalShipment.source_record_key)))
        profits, keys = [], []
        for row in self.history:
            if not valid_coordinates(row.origin_latitude, row.origin_longitude):
                continue
            hub = nearest_terminal(self.terminals, row.origin_latitude, row.origin_longitude)
            if hub is None or hub.terminal_id != terminal.terminal_id:
                continue
            distance = pickup_distance_miles(row.origin_latitude, row.origin_longitude, terminal.latitude, terminal.longitude)
            if distance > policy.historical_origin_radius_miles:
                continue
            payout = row.bid_rate if row.order_type == 'open_bid' else row.offered_rate if row.order_type == 'fixed_rate' else None
            cost = row.estimated_cost
            if cost is None and non_negative(row.distance_miles) and non_negative(row.cost_per_mile):
                cost = row.distance_miles * row.cost_per_mile
            if not non_negative(payout) or not non_negative(cost):
                continue
            # Include losses in the mean; selecting only positive outcomes biases credit.
            profits.append(money(payout) - money(cost))
            keys.append(row.source_record_key)
        average = sum(profits, Decimal('0')) / len(profits) if profits else None
        result = {'terminal_code': terminal.code, 'sample_count': len(profits),
                  'minimum_samples': policy.historical_min_samples, 'since_sim': since.isoformat(),
                  'through_sim': self.now.isoformat(), 'mean_loaded_profit': str(average) if average is not None else None,
                  'source_record_keys': keys, 'cost_basis': 'historical loaded cost; repositioning cost is not available',
                  'payout_basis': 'won fixed offers or entered winning bids; losses included in average'}
        self.baselines[terminal.terminal_id] = result
        return result


def apply_profit_adjustments(result, shipment, planned, routing, context):
    """Attach estimates without modifying payout, cash profit, margins, or floor."""
    with localcontext(CALCULATION_CONTEXT):
        _apply(result, shipment, planned, routing, context)


def _apply(result, shipment, planned, routing, context):
    policy = context.policy
    result.profit_adjustments = ProfitAdjustmentDetails(policy=policy)
    details = result.profit_adjustments
    window = planned.occupied_window if planned else None
    unit_id = planned.projection.unit_id if planned else None
    origin = next((row for row in context.terminals if row.terminal_id == routing.pickup_terminal_id), None)

    if not policy.opportunity_cost_enabled or policy.opportunity_cost_per_hour == 0:
        result.opportunity_cost = Decimal('0.00')
        details.opportunity_cost = {'explanation': 'Opportunity cost is disabled or its hourly rate is zero.'}
        result.sources.opportunity_cost = source('disabled_or_zero_rate', opportunity_cost_per_hour=policy.opportunity_cost_per_hour)
    elif (window is None or origin is None or planned.projection.status != 'available'
          or planned.projection.terminal_id != origin.terminal_id
          or (planned.projection.available_at is not None and planned.projection.available_at > window.occupied_from)):
        missing(result, 'opportunity_cost', 'opportunity_capacity_unknown', 'A known origin terminal, available selected unit, and occupied window are required.')
    else:
        capacity, known = context.capacity(origin, window.occupied_from, unit_id)
        details.opportunity_cost = capacity
        if known:
            scarcity = Decimal('1') - Decimal(max(0, capacity['available_units'] - 1)) / capacity['total_units']
            result.opportunity_cost = money(window.occupied_hours * policy.opportunity_cost_per_hour * scarcity)
            details.opportunity_cost.update(scarcity_fraction=str(scarcity), occupied_hours=str(window.occupied_hours),
                explanation='Longer occupied time costs more; scarcity is the unavailable fraction after removing the proposed unit from origin capacity.')
            result.sources.opportunity_cost = source('occupied_time_times_origin_scarcity',
                'occupied_hours * opportunity_cost_per_hour * (1 - (available_units - 1) / total_units)',
                occupied_hours=window.occupied_hours, opportunity_cost_per_hour=policy.opportunity_cost_per_hour,
                scarcity_fraction=scarcity, terminal_code=origin.code, at_sim=window.occupied_from,
                total_units=capacity['total_units'], available_units=capacity['available_units'])
        else:
            missing(result, 'opportunity_cost', 'opportunity_capacity_unknown', 'Fleet projections contain unknown locations or stale snapshots; refresh before estimating scarcity.')
            details.opportunity_cost['explanation'] = 'Capacity estimate is unavailable; inspect unknown/stale units and incomplete terminal counts.'

    if not policy.positioning_value_enabled or policy.positioning_credit_fraction == 0 or policy.positioning_max_credit == 0:
        result.positioning_value = Decimal('0.00')
        details.positioning_value = {'explanation': 'Positioning credit is disabled or configured to zero.'}
        result.sources.positioning_value = source('disabled_or_zero_credit')
    elif window is None or unit_id is None or planned.projection.status != 'available':
        missing(result, 'positioning_value', 'positioning_window_unknown', 'A selected unit and occupied window are required to project destination capacity.')
    else:
        destination = context.by_code.get((shipment.destination_terminal_code or '').upper())
        destination = destination or nearest_terminal(context.terminals, shipment.destination_latitude, shipment.destination_longitude)
        if destination is None:
            missing(result, 'positioning_value', 'positioning_destination_unknown', 'Resolve a destination terminal or provide valid delivery coordinates.')
        elif origin is not None and destination.terminal_id == origin.terminal_id:
            result.positioning_value = Decimal('0.00')
            details.positioning_value = {'explanation': 'Returning to the same terminal gives no geographic positioning improvement.'}
            result.sources.positioning_value = source('same_terminal_no_positioning_improvement', terminal_code=origin.code)
        elif origin is None:
            missing(result, 'positioning_value', 'positioning_origin_unknown', 'Resolve the origin terminal to compare outbound opportunities.')
        else:
            capacity, known = context.capacity(destination, window.occupied_until, unit_id, arriving=True)
            origin_capacity, origin_known = context.capacity(origin, window.occupied_until, unit_id, arriving=True)
            baseline = context.historical_baseline(destination)
            origin_baseline = context.historical_baseline(origin)
            details.positioning_value = {'capacity': capacity, 'history': baseline,
                'origin_alternative_capacity': origin_capacity, 'origin_history': origin_baseline,
                'explanation': 'Discounted positive improvement in historical outbound loaded profit per available unit versus returning to origin, at delivery plus turnaround; not guaranteed revenue.'}
            if not known or not origin_known:
                missing(result, 'positioning_value', 'positioning_capacity_unknown', 'Projected capacity is uncertain because fleet positions, counts, or snapshot freshness are unknown.')
            elif min(baseline['sample_count'], origin_baseline['sample_count']) < policy.historical_min_samples:
                missing(result, 'positioning_value', 'positioning_history_insufficient', 'Not enough won outbound shipments with known payout and cost near both origin and destination.')
            else:
                destination_opportunity = max(Decimal('0'), Decimal(baseline['mean_loaded_profit'])) / max(1, capacity['available_units'])
                origin_opportunity = max(Decimal('0'), Decimal(origin_baseline['mean_loaded_profit'])) / max(1, origin_capacity['available_units'])
                improvement = max(Decimal('0'), destination_opportunity - origin_opportunity)
                credit = improvement * policy.positioning_credit_fraction if capacity['proposed_unit_available'] else Decimal('0')
                result.positioning_value = money(min(policy.positioning_max_credit, credit))
                details.positioning_value.update(destination_profit_per_available_unit=str(destination_opportunity),
                                                 origin_profit_per_available_unit=str(origin_opportunity), improvement=str(improvement))
                details.warnings.append('Positioning value is a historical planning estimate, excludes unknown future deadhead, and is not cash revenue or a win probability.')
                result.sources.positioning_value = source('discounted_outbound_opportunity_improvement',
                    'min(positioning_max_credit, max(0, destination_profit_per_available_unit - origin_profit_per_available_unit) * positioning_credit_fraction); zero when proposed unit is already booked',
                    mean_loaded_profit=baseline['mean_loaded_profit'], sample_count=baseline['sample_count'],
                    origin_mean_loaded_profit=origin_baseline['mean_loaded_profit'], origin_sample_count=origin_baseline['sample_count'],
                    destination_profit_per_available_unit=destination_opportunity, origin_profit_per_available_unit=origin_opportunity,
                    origin_available_units=origin_capacity['available_units'], origin_terminal_code=origin.code,
                    positioning_credit_fraction=policy.positioning_credit_fraction,
                    positioning_max_credit=policy.positioning_max_credit, available_units=capacity['available_units'],
                    proposed_unit_available=capacity['proposed_unit_available'], terminal_code=destination.code,
                    at_sim=window.occupied_until)
    if result.gross_profit is not None and result.opportunity_cost is not None and result.positioning_value is not None:
        result.adjusted_profit = money(result.gross_profit - result.opportunity_cost + result.positioning_value)
        result.sources.adjusted_profit = source('cash_profit_minus_opportunity_plus_positioning',
            'gross_profit - opportunity_cost + positioning_value', gross_profit=result.gross_profit,
            opportunity_cost=result.opportunity_cost, positioning_value=result.positioning_value)
