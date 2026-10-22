"""Bounded, auditable search on an isolated copy of the product snapshot."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from schema.allocation.allocation_plan import AllocationReservation
from schema.dispatch.assignment import Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.autopilot.autopilot_run import AutopilotRun
from schema.dispatch.dispatch_action import DispatchAction
from schema.allocation.fleet_allocation import AllocationCombination, AllocationResult, AllocationSelection
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from schema.terminal.terminal import Terminal
from service.autopilot.autopilot_guards import candidate_rejections, wall_now
from service.dispatch.dispatch_policy import dispatch_policy, evaluate_dispatch
from service.pricing.economics import expected_economics
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc
from service.shared.geography import nearest_terminal, pickup_distance_miles, valid_coordinates
from service.dispatch.pickup_routing import evaluate_pickup_routing
from service.allocation.allocation_holds import current_holds
from service.simulation.simulation_time import remaining_seconds


SNAPSHOT_TABLES = (
    'shipments', 'terminals', 'fleet_units', 'fleet_capacity', 'simulation_clock',
    'assignments', 'assignment_reservations', 'dispatch_actions',
    'fleet_allocation_reservations', 'evaluations', 'autopilot_runs', 'autopilot_attempts',
    'historical_shipments', 'observed_comparisons', 'observed_cohorts',
)
# Shipment states the allocation reads. Anything else is settled history that
# the planner never consults.
ALLOCATABLE_SHIPMENT_STATUSES = ('open', 'bid_submitted', 'accepted', 'in_transit', 'completed')


@contextmanager
def snapshot_engine(engine):
    if engine.dialect.name != 'sqlite':
        raise ProductError(503, 'Fleet allocation snapshots currently require SQLite')
    memory = sqlite3.connect(':memory:', check_same_thread=False)
    with engine.connect() as source:
        raw = source.connection.driver_connection
        # Rebuild only the operational dataset. Audit, plan result and
        # prediction tables grow without bound and are never read by the
        # allocation/model preparation code, but copying them into memory made
        # each plan exceed the container limit.
        memory.execute('PRAGMA foreign_keys=OFF')
        for (ddl,) in raw.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND type IN ('table', 'index') ORDER BY type DESC"):
            memory.execute(ddl)
        for table in SNAPSHOT_TABLES:
            columns = [row[1] for row in raw.execute(f'PRAGMA table_info("{table}")')]
            if not columns:
                continue
            query = f'SELECT {", ".join(columns)} FROM "{table}"'
            if table == 'shipments':
                # The board keeps every historic order, but the allocation only
                # reads orders that can still be booked or are awaiting a
                # decision. Copying the whole table made every plan copy
                # thousands of rows it never looked at.
                query += (f' WHERE status IN ({", ".join("?" for _ in ALLOCATABLE_SHIPMENT_STATUSES)})')
            rows = raw.execute(query, ALLOCATABLE_SHIPMENT_STATUSES if table == 'shipments' else ()).fetchall()
            if rows:
                memory.executemany(
                    f'INSERT OR REPLACE INTO "{table}" ({", ".join(columns)}) '
                    f'VALUES ({", ".join("?" for _ in columns)})', rows)
    memory.commit()
    memory.execute('PRAGMA foreign_keys=ON')
    copy = create_engine('sqlite://', creator=lambda: memory, poolclass=StaticPool)
    try:
        yield copy
    finally:
        copy.dispose()


def fingerprint(session):
    data = {}
    for model in (Shipment, Terminal, FleetUnit, FleetCapacity, SimulationClock, Assignment,
                  AssignmentReservation, DispatchAction, AllocationReservation):
        query = select(model.__table__).order_by(*model.__table__.primary_key.columns)
        if model is AllocationReservation:
            query = query.where(model.status != 'reserved')
        data[model.__tablename__] = [dict(row) for row in session.execute(query).mappings()]
    # Clock samples advance continuously. Their read timestamps are freshness
    # evidence, not a changed truck/order snapshot. Speed changes invalidate
    # approval; rewinds and simulated expiry are enforced by the run guards.
    data['simulation_clock'] = [{'clock_id': row['clock_id'], 'simulation_speed': row['simulation_speed']}
                                for row in data['simulation_clock']]
    data['policy'] = dispatch_policy().model_dump(mode='json')
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def selection_fingerprint(session, selections, *, exclude_plan_id=None):
    """Hash only the state a confirmed plan depends on.

    The board refreshes continuously: unrelated orders arrive/expire and
    freshness timestamps are rewritten. Reservation approval must notice a
    change to the chosen orders, units, schedules or competing holds, not
    unrelated churn. Sync timestamps are excluded because freshness has its
    own policy checks.
    """
    shipment_ids = {getattr(item, 'shipment_id', None) for item in selections} - {None}
    unit_ids = {getattr(item, 'unit_id', None) or getattr(item, 'candidate_unit_id', None)
                for item in selections} - {None}

    def dump(model, rows, skip=()):
        return [{column.name: getattr(row, column.name) for column in model.__table__.columns
                 if column.name not in skip} for row in rows]

    units = list(session.scalars(select(FleetUnit).where(FleetUnit.unit_id.in_(unit_ids))
                                .order_by(FleetUnit.unit_id))) if unit_ids else []
    terminal_ids = {unit.home_terminal_id for unit in units}
    data = {
        'shipments': dump(Shipment, session.scalars(select(Shipment).where(
            Shipment.shipment_id.in_(shipment_ids)).order_by(Shipment.shipment_id)), {'last_synced_at'})
        if shipment_ids else [],
        'units': dump(FleetUnit, units, {'last_synced_at'}),
        'capacities': dump(FleetCapacity, session.scalars(select(FleetCapacity).where(
            FleetCapacity.terminal_id.in_(terminal_ids)).order_by(FleetCapacity.terminal_id)), {'checked_at'})
        if terminal_ids else [],
        'assignments': dump(Assignment, session.scalars(select(Assignment).where(
            Assignment.unit_id.in_(unit_ids)).order_by(Assignment.assignment_id))) if unit_ids else [],
        'assignment_holds': dump(AssignmentReservation, session.scalars(select(AssignmentReservation).where(
            AssignmentReservation.unit_id.in_(unit_ids)).order_by(AssignmentReservation.action_id)))
        if unit_ids else [],
        'actions': dump(DispatchAction, session.scalars(select(DispatchAction).where(
            DispatchAction.shipment_id.in_(shipment_ids)).order_by(DispatchAction.action_id)))
        if shipment_ids else [],
    }
    holds = select(AllocationReservation).where(
        AllocationReservation.status.in_(('reserved', 'submitting')),
        (AllocationReservation.shipment_id.in_(shipment_ids))
        | (AllocationReservation.unit_id.in_(unit_ids)))
    if exclude_plan_id is not None:
        holds = holds.where(AllocationReservation.plan_id != exclude_plan_id)
    data['allocation_holds'] = dump(AllocationReservation, session.scalars(
        holds.order_by(AllocationReservation.plan_id, AllocationReservation.shipment_id)))
    clock = session.get(SimulationClock, 1)
    data['clock'] = ({'clock_id': clock.clock_id, 'simulation_speed': clock.simulation_speed}
                     if clock else None)
    data['policy'] = dispatch_policy().model_dump(mode='json')
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def available_orders(session, settings, external_ids=None, run_id=None):
    types = [kind for kind, enabled in (('fixed_rate', settings.enable_fixed_rate), ('open_bid', settings.enable_open_bid)) if enabled]
    statement = select(Shipment).where(Shipment.status == 'open', Shipment.board_missing_at.is_(None),
                                       Shipment.board_retired_at.is_(None), Shipment.order_type.in_(types))
    if external_ids is not None:
        statement = statement.where(Shipment.external_id.in_(external_ids))
    if run_id is not None:
        done = select(AutopilotAttempt.shipment_id).where(AutopilotAttempt.run_id == run_id,
            (AutopilotAttempt.action_id.is_not(None)) | (AutopilotAttempt.status == 'shadow'))
        statement = statement.where(Shipment.shipment_id.not_in(done))
    # An order that expires before its decision can be used is not actionable.
    # The model correctly reports a quote window that ends at the order's own
    # expiry, so bidding on a nearly-expired order produces a decision that is
    # already stale when the dispatcher tries to submit it.
    clock = session.get(SimulationClock, 1)
    if clock is not None and clock.sim_time_utc is not None:
        horizon = timedelta(seconds=float(settings.max_decision_age_seconds) * float(clock.simulation_speed))
        statement = statement.where(
            Shipment.expires_at.is_not(None),
            Shipment.expires_at > simulated_now(clock) + horizon,
        )
    return list(session.scalars(statement.order_by(Shipment.expires_at, Shipment.external_id)))


def candidate_orders(orders, settings):
    def potential(order):
        bid = settings.max_bid_rate if order.order_type == 'open_bid' else None
        economics = expected_economics(order, Decimal('0'), bid)
        return max(Decimal('0'), (economics.payout or 0) - (economics.loaded_cost or 0))
    high = sorted(orders, key=lambda row: (-potential(row), row.external_id))
    urgent = sorted(orders, key=lambda row: (utc(row.expires_at) if row.expires_at else utc(wall_now()), row.external_id))
    chosen = {}
    for pair in zip(high, urgent):
        for row in pair:
            if len(chosen) < settings.allocation_max_candidates:
                chosen[row.external_id] = row
    return list(chosen.values())


def next_commitment_rejections(session, shipment, checked):
    """A new delivery must also leave the unit able to reach its next booking."""
    if checked.occupied_window is None:
        return []
    following = session.execute(select(Assignment, Shipment).join(Shipment).where(
        Assignment.unit_id == checked.candidate_unit_id, Assignment.lock_retired_at.is_(None),
        Assignment.assignment_status.in_(('confirmed', 'accepted', 'in_transit', 'active')),
        Assignment.busy_from >= shipment.delivery_at,
    ).order_by(Assignment.busy_from)).first()
    if following is None:
        return []
    assignment, next_order = following
    if not all(valid_coordinates(getattr(item, f'{side}_latitude'), getattr(item, f'{side}_longitude'))
               for item, side in ((shipment, 'destination'), (next_order, 'origin'))):
        return ['next_commitment_location_unknown']
    terminals = list(session.scalars(select(Terminal)))
    by_code = {row.code.upper(): row for row in terminals}
    destination = by_code.get((shipment.destination_terminal_code or '').upper()) or nearest_terminal(
        terminals, shipment.destination_latitude, shipment.destination_longitude)
    pickup = by_code.get((next_order.origin_terminal_code or '').upper()) or nearest_terminal(
        terminals, next_order.origin_latitude, next_order.origin_longitude)
    miles = pickup_distance_miles(shipment.destination_latitude, shipment.destination_longitude,
                                 next_order.origin_latitude, next_order.origin_longitude)
    arrival = checked.occupied_window.occupied_until + timedelta(
        hours=float(miles / checked.policy.assumed_travel_speed_mph),
        minutes=float(checked.policy.pickup_buffer_minutes))
    if destination is None or pickup is None or destination.terminal_id != pickup.terminal_id:
        return ['next_commitment_terminal_unreachable']
    if arrival > utc(assignment.busy_from) or miles > checked.policy.max_deadhead_miles:
        return ['next_commitment_pickup_unreachable']
    return []


def virtual_booking(session, shipment, checked, bid):
    """Only mutate the isolated SQLite copy; never create a hypothetical win."""
    window = checked.occupied_window
    unit = session.get(FleetUnit, checked.candidate_unit_id)
    if shipment.order_type == 'open_bid':
        action = DispatchAction(shipment_id=shipment.shipment_id, action_type='bid', bid_rate=bid,
                                request_status='unknown', requested_at=wall_now())
        session.add(action)
        session.flush()
        session.add(AssignmentReservation(action_id=action.action_id, unit_id=unit.unit_id,
            busy_from=window.occupied_from, busy_until=window.occupied_until, created_at=wall_now()))
    else:
        terminals = list(session.scalars(select(Terminal)))
        by_code = {row.code.upper(): row for row in terminals}
        origin = by_code.get((shipment.origin_terminal_code or '').upper()) or nearest_terminal(
            terminals, shipment.origin_latitude, shipment.origin_longitude)
        destination = by_code.get((shipment.destination_terminal_code or '').upper()) or nearest_terminal(
            terminals, shipment.destination_latitude, shipment.destination_longitude)
        if origin is None or destination is None:
            raise ProductError(409, 'Delivery terminal could not be projected')
        shipment.status, shipment.board_assigned_unit_id = 'accepted', unit.board_unit_id
        session.add(Assignment(shipment_id=shipment.shipment_id, unit_id=unit.unit_id,
            origin_terminal_id=origin.terminal_id, destination_terminal_id=destination.terminal_id,
            busy_from=shipment.pickup_at, busy_until=shipment.delivery_at,
            occupied_from=window.occupied_from, occupied_until=window.occupied_until,
            assignment_status='confirmed', confirmed_at=checked.evaluated_at_sim))
        unit.status, unit.available_at = 'assigned', shipment.delivery_at
        session.flush()
        capacity = session.get(FleetCapacity, unit.home_terminal_id)
        roster = list(session.scalars(select(FleetUnit).where(FleetUnit.home_terminal_id == unit.home_terminal_id,
                                                             FleetUnit.board_missing_at.is_(None))))
        capacity.board_available_units = sum(row.status not in ('maintenance', 'off_duty') and (
            row.status == 'available' or row.available_at is not None and utc(row.available_at) <= utc(capacity.as_of_sim_time)) for row in roster)
        capacity.board_assigned_units = sum(row.status == 'assigned' and not (
            row.available_at is not None and utc(row.available_at) <= utc(capacity.as_of_sim_time)) for row in roster)
    session.flush()


def solve_snapshot(engine, settings, bid_rates=None, *, external_ids=None, run_id=None, models=None):
    bid_rates, models = bid_rates or {}, models or {}
    with Session(engine) as session:
        clock = session.get(SimulationClock, 1)
        if clock is None:
            raise ProductError(503, 'Simulation clock has not been synced yet')
        wall, sim = wall_now(), simulated_now(clock)
        result = AllocationResult(snapshot_fingerprint='pending', evaluated_at_wall=wall,
                                  evaluated_at_sim=sim, available_order_count=0, candidate_count=0)
        run = session.get(AutopilotRun, run_id) if run_id else AutopilotRun(
            settings=settings.model_dump(mode='json'), estimated_loss_usd=Decimal('0'), pending_estimated_loss_usd=Decimal('0'))
        remaining_slots = max(0, settings.max_orders - (run.orders_reserved if run_id else 0))
        remaining_duration = remaining_seconds(session, run, wall) if run_id else settings.max_duration_seconds
        orders = available_orders(session, settings, external_ids, run_id)
        result.available_order_count = len(orders)
        chosen = candidate_orders(orders, settings)
        result.candidate_count = len(chosen)
        ids = {row.external_id for row in chosen}
        result.omitted_external_ids = [row.external_id for row in orders if row.external_id not in ids]
        upper = {}
        priced = []
        terminals = list(session.scalars(select(Terminal)))
        by_code = {row.code.upper(): row for row in terminals}
        protected = {hold.shipment_id for hold in current_holds(session)}
        unknown_pending = list(session.scalars(select(Shipment).where(
            Shipment.status == 'bid_submitted', Shipment.board_retired_at.is_(None),
            Shipment.shipment_id.not_in(protected))))
        blocked_hubs = set()
        for pending in unknown_pending:
            hub = by_code.get((pending.origin_terminal_code or '').upper()) or nearest_terminal(
                terminals, pending.origin_latitude, pending.origin_longitude)
            blocked_hubs.add(hub.terminal_id if hub else None)
        if unknown_pending:
            result.warnings.append('Pending bids without a known reserved unit block their origin terminal until reconciled; no capacity was invented.')
        for order in chosen:
            origin = by_code.get((order.origin_terminal_code or '').upper()) or nearest_terminal(
                terminals, order.origin_latitude, order.origin_longitude)
            if None in blocked_hubs or origin is not None and origin.terminal_id in blocked_hubs:
                result.rejected[order.external_id] = ['pending_bid_capacity_unknown']
                continue
            bid = bid_rates.get(order.external_id)
            if order.order_type == 'open_bid' and bid is None:
                result.rejected[order.external_id] = ['bid_rate_required']
                continue
            economics = expected_economics(order, Decimal('0'), bid)
            if economics.loaded_cost is None or economics.payout is None:
                result.rejected[order.external_id] = ['cash_economics_unknown']
                continue
            upper[order.external_id] = max(Decimal('0'), economics.payout - economics.loaded_cost)
            priced.append(order)
        units = list(session.scalars(select(FleetUnit).where(FleetUnit.board_missing_at.is_(None))))
        for order in priced:
            routing = evaluate_pickup_routing(session, order)
            checked = evaluate_dispatch(session, order, bid_rates.get(order.external_id), routing=routing, at_sim_time=sim)
            planned_by_unit = {row.projection.unit_id: row for row in routing.chaining_units}
            for unit in units:
                board_selectable = routing.routing_candidate_unit_id == unit.unit_id
                if board_selectable:
                    reasons = list(dict.fromkeys([*checked.rejection_reasons,
                        *candidate_rejections(session, run, order, checked, bid_rates.get(order.external_id))]))
                    econ = checked.economics
                else:
                    planned = planned_by_unit.get(unit.unit_id)
                    econ = expected_economics(order, planned.projected_deadhead_miles if planned else None,
                        bid_rates.get(order.external_id), projection=planned.projection if planned else None,
                        timing_policy=routing.policy, window=planned.occupied_window if planned else None)
                    reasons = ['unit_not_board_selectable', *(planned.rejection_reasons if planned else ['unit_schedule_unavailable'])]
                result.combinations.append(AllocationCombination(external_id=order.external_id, unit_id=unit.unit_id,
                    board_selectable=board_selectable, feasible=not reasons,
                    rejection_reasons=reasons, loaded_cost=econ.loaded_cost, deadhead_miles=econ.projected_deadhead_miles,
                    deadhead_cost=econ.projected_deadhead_cost, total_cost=econ.total_cost, projected_profit=econ.gross_profit))
        slot = max(settings.min_action_interval_seconds, settings.allocation_submission_seconds)
        speed = float(clock.simulation_speed)
        root_bound = sum(sorted(upper.values(), reverse=True)[:remaining_slots], Decimal('0'))
        deadline, best, best_profit, seen = time.monotonic() + settings.allocation_max_seconds, [], Decimal('0'), {}
        exhausted = False

        def search(path, pending, profit):
            nonlocal best, best_profit, exhausted
            if profit > best_profit or profit == best_profit and len(path) < len(best):
                best, best_profit = list(path), profit
            if result.search_nodes >= settings.allocation_max_nodes or time.monotonic() >= deadline:
                exhausted = True
                return
            result.search_nodes += 1
            slots = remaining_slots - len(path)
            if slots <= 0 or profit + sum(sorted((upper[row.external_id] for row in pending), reverse=True)[:slots], Decimal('0')) <= best_profit:
                return
            state = tuple(sorted((row.external_id, str(row.unit_id)) for row in path))
            if state in seen and seen[state] >= profit:
                return
            seen[state] = profit
            elapsed = len(path) * slot
            submit_sim = sim + timedelta(seconds=elapsed + settings.allocation_expiry_buffer_seconds)
            if elapsed + settings.allocation_expiry_buffer_seconds >= remaining_duration:
                return
            for order in sorted(pending, key=lambda row: (utc(row.expires_at) if row.expires_at else sim,
                                                         -upper[row.external_id], row.external_id)):
                if exhausted:
                    break
                if order.expires_at is None or utc(order.expires_at) <= submit_sim:
                    result.rejected.setdefault(order.external_id, ['submission_deadline_unreachable'])
                    continue
                model_until = models.get(order.external_id, {}).get('response', {}).get('valid_until')
                elapsed_wall = elapsed / speed if speed > 0 else float('inf')
                if model_until is not None and (speed <= 0 or wall + timedelta(seconds=elapsed_wall) >= utc(datetime.fromisoformat(model_until))):
                    result.rejected.setdefault(order.external_id, ['MODEL_DECISION_EXPIRES_BEFORE_SUBMISSION'])
                    continue
                bid = bid_rates.get(order.external_id)
                checked = evaluate_dispatch(session, order, bid, at_sim_time=submit_sim)
                reasons = [*checked.rejection_reasons, *candidate_rejections(session, run, order, checked, bid),
                           *next_commitment_rejections(session, order, checked)]
                if models.get(order.external_id, {}).get('kind') == 'batch':
                    from service.autopilot.model_autopilot import decision_rejections, matches_evaluation
                    try:
                        reasons.extend(decision_rejections(models[order.external_id], run.mode))
                        if not matches_evaluation(models[order.external_id], checked):
                            reasons.append('MODEL_BATCH_SELECTED_PAIR_CHANGED')
                    except (ValueError, TypeError, KeyError):
                        reasons.append('MODEL_BATCH_DECISION_INVALID')
                if reasons:
                    result.rejected.setdefault(order.external_id, list(dict.fromkeys(reasons)))
                    continue
                selection = AllocationSelection(external_id=order.external_id, shipment_id=order.shipment_id,
                    order_type=order.order_type, unit_id=checked.candidate_unit_id,
                    bid_rate=bid if order.order_type == 'open_bid' else None,
                    expires_at=utc(order.expires_at), submission_position=len(path) + 1,
                    planned_submission_at_wall=wall + timedelta(seconds=elapsed / speed if speed > 0 else 0),
                    planned_submission_at_sim=sim + timedelta(seconds=elapsed), evaluation=checked,
                    model_snapshot=models.get(order.external_id, {}))
                savepoint = session.begin_nested()
                try:
                    virtual_booking(session, order, checked, bid)
                    search([*path, selection], [row for row in pending if row.external_id != order.external_id],
                           profit + checked.economics.gross_profit)
                finally:
                    savepoint.rollback()

        search([], priced, Decimal('0'))
        result.selected, result.projected_profit = best, best_profit
        result.upper_bound_profit = root_bound if exhausted else best_profit
        result.optimality = 'bounded' if exhausted or result.omitted_external_ids else 'optimal'
        for item in best:
            result.rejected.pop(item.external_id, None)
            result.loaded_cost += item.evaluation.economics.loaded_cost
            result.deadhead_cost += item.evaluation.economics.projected_deadhead_cost
            result.deadhead_miles += item.evaluation.economics.projected_deadhead_miles
        if result.optimality == 'bounded':
            result.warnings.append('Candidate or search limits reached; this is the best feasible plan found, not a proven global optimum.')
        if any(row.order_type == 'open_bid' for row in best):
            result.warnings.append('Open-bid profit is conditional on awards; bids reserve local capacity without creating confirmed deliveries.')
        result.warnings.append('The board chooses actual units. Replan after each response; future arrivals and bid wins are unknown.')
        result.snapshot_fingerprint = selection_fingerprint(session, result.selected)
        return result


def preview_allocation(engine, request):
    with snapshot_engine(engine) as snapshot:
        result = solve_snapshot(snapshot, request.settings, request.bid_rates, external_ids=request.external_ids)
        grouped = {}
        with Session(snapshot) as session:
            for selection in result.selected:
                order = session.get(Shipment, selection.shipment_id)
                group = grouped.setdefault(selection.unit_id, {'unit_id': str(selection.unit_id), 'jobs': [],
                    'existing_commitments': [], 'planning_only': True, 'submission_enabled': False})
                group['jobs'].append({'external_id': order.external_id, 'order_type': order.order_type,
                    'pickup_at': utc(order.pickup_at).isoformat(), 'delivery_at': utc(order.delivery_at).isoformat(),
                    'expires_at_sim': utc(order.expires_at).isoformat(), 'submission_position': selection.submission_position,
                    'occupied_window': selection.evaluation.occupied_window.model_dump(mode='json'),
                    'projected_position': selection.evaluation.projection.model_dump(mode='json'),
                    'economics': selection.evaluation.economics.model_dump(mode='json'),
                    'conditional_on_prior_confirmed_delivery': len(group['jobs']) > 0})
            for unit_id, group in grouped.items():
                for assignment, order in session.execute(select(Assignment, Shipment).join(Shipment).where(
                    Assignment.unit_id == unit_id, Assignment.lock_retired_at.is_(None),
                    Assignment.assignment_status.in_(('confirmed', 'accepted', 'in_transit', 'active'))).order_by(Assignment.busy_from)):
                    group['existing_commitments'].append({'assignment_id': str(assignment.assignment_id),
                        'external_id': order.external_id, 'pickup_at': utc(assignment.busy_from).isoformat(),
                        'delivery_at': utc(assignment.busy_until).isoformat(),
                        'occupied_from': utc(assignment.occupied_from or assignment.busy_from).isoformat(),
                        'occupied_until': utc(assignment.occupied_until or assignment.busy_until).isoformat()})
        result.truck_sequences = list(grouped.values())
        return result
