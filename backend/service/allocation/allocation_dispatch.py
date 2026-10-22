"""Connect fleet plans to the existing authorized autopilot action pipeline."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import copy
import hashlib
import json
import logging

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from schema.allocation.allocation_plan import AllocationPlan, AllocationReservation
from schema.shared.api import ActionRequest, BidRequest, Page
from schema.autopilot.autopilot import AutopilotSettings
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.autopilot.autopilot_run import AutopilotRun
from schema.allocation.fleet_allocation import AllocationPlanView
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.audit.activity import add_activity
from service.allocation.allocation_holds import cancel_plans, reap_plans
from service.autopilot.autopilot_guards import INFLIGHT, check_run_limits, wall_now
from service.dispatch.dispatch_policy import evaluate_dispatch
from service.shared.errors import ProductError
from service.orders.expiration import simulated_now, utc
from service.simulation.simulation_time import attempt_due, current_sim, guard_run_clock, wall_estimate
from service.allocation.fleet_allocation import available_orders, candidate_orders, fingerprint, snapshot_engine, solve_snapshot
from service.autopilot.model_autopilot import (
    decision_rejections, evaluate_model, matches_evaluation, prepare_decision, validate_decision,
)
from service.allocation.fleet_allocation import virtual_booking


logger = logging.getLogger(__name__)


class PlanInvalidated(Exception):
    pass


async def price_while_current(engine, plan_id, inputs, errors, cache, config, *, batch=None):
    def current():
        with Session(engine) as session:
            return session.get(AllocationPlan, plan_id).status == 'planning'

    async def watch():
        while True:
            await asyncio.sleep(.25)
            if not await asyncio.to_thread(current):
                raise PlanInvalidated()

    if batch is not None:
        from service.autopilot.model_batch import analyze_batch
        pricing = analyze_batch(batch, inputs, errors, audit_engine=engine)
    else:
        pricing = price_batch(inputs, errors, cache, config, audit_engine=engine)
    quote, monitor = asyncio.create_task(pricing), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((quote, monitor), return_when=asyncio.FIRST_COMPLETED)
        if monitor in done:
            await monitor
        return await quote
    finally:
        for task in (quote, monitor):
            task.cancel()
        for task in (quote, monitor):
            with suppress(asyncio.CancelledError, PlanInvalidated):
                await task


def claim_plan(engine):
    from service.autopilot.autopilot import transaction
    with transaction(engine) as session:
        reap_plans(session)
        run = session.scalar(select(AutopilotRun).where(AutopilotRun.status == 'running'))
        if run is None or check_run_limits(session, run):
            return None
        config = AutopilotSettings.model_validate(run.settings)
        if session.get(SimulationClock, 1).simulation_speed <= 0:
            return None
        from service.audit.live_audit import now as submission_now, rate_limit_ready
        if not rate_limit_ready(session, config, submission_now()):
            return None
        if session.scalar(select(AutopilotAttempt.attempt_id).where(
            AutopilotAttempt.run_id == run.run_id, AutopilotAttempt.status.in_(INFLIGHT))):
            return None
        if session.scalar(select(AllocationPlan.plan_id).where(
            AllocationPlan.run_id == run.run_id, AllocationPlan.status.in_(('planning', 'reserved')))):
            return None
        now = wall_now()
        from service.autopilot.order_collection import collect_or_close, collecting_plan, continue_collection
        pending_collection = collecting_plan(session, run.run_id)
        if not attempt_due(session, run, config.min_action_interval_seconds, now):
            return None
        if config.collection_window_seconds > 0 or pending_collection is not None:
            plan = continue_collection(session, run, config, now) if pending_collection is None else None
            plan = plan or collect_or_close(session, run, config, now)
            if plan is None:
                return None
            session.flush()
            return plan.plan_id, run.run_id, config, utc(run.deadline_at_wall), plan.result.get('collection')
        if not available_orders(session, config, run_id=run.run_id):
            return None
        sim = current_sim(session, now)
        expiry_sim = min(utc(run.deadline_at_sim), sim + timedelta(seconds=120))
        plan = AllocationPlan(run_id=run.run_id, status='planning', result={}, created_at_wall=now,
                              expires_at_sim=expiry_sim,
                              expires_at_wall=wall_estimate(session, expiry_sim, now) or now + timedelta(seconds=120))
        session.add(plan)
        run.last_attempt_at_wall = now
        run.last_attempt_at_sim = sim
        session.flush()
        return plan.plan_id, run.run_id, config, utc(run.deadline_at_wall), plan.result.get('collection')


def price_inputs(engine, config, run_id, external_ids=None):
    inputs, errors, cache = {}, {}, {}
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        run = session.get(AutopilotRun, run_id)
        version = fingerprint(session)
        previous = session.scalar(select(AllocationPlan).where(AllocationPlan.run_id == run_id,
            AllocationPlan.status.not_in(('planning', 'reserved'))).order_by(AllocationPlan.created_at_wall.desc()))
        if previous:
            cache = previous.result.get('models', {})
        for order in candidate_orders(available_orders(session, config, external_ids, run_id=run_id), config):
            if order.order_type == 'open_bid':
                try:
                    checked = evaluate_dispatch(session, order)
                    inputs[order.external_id] = prepare_decision(session, order, checked, config, version, run=run, capture_snapshot=True)
                except (ValueError, TypeError) as exc:
                    errors[order.external_id] = {'error': str(exc) if str(exc).startswith('MODEL_') else 'MODEL_INPUTS_MISSING_OR_INVALID',
                                                 'decision': 'REVIEW'}
    return inputs, errors, cache


async def price_batch(inputs, errors, cache, config, *, audit_engine=None):
    models, bids = dict(errors), {}
    semaphore = asyncio.Semaphore(4)

    async def quote(external_id, prepared):
        previous = cache.get(external_id, {})
        comparable = copy.deepcopy(prepared['request'])
        old_position = previous.get('request', {}).get('backend_context', {}).get('projected_position')
        position = comparable['backend_context']['projected_position']
        if position and old_position:
            position['computed_at'] = old_position['computed_at']
        model, amount = None, None
        # Live approval/kill controls are checked by the model service on every
        # new plan. Never reuse a previous plan's live preflight result.
        if comparable['mode'] == 'shadow' and previous.get('request') == comparable and 'error' not in previous:
            try:
                response, amount = validate_decision(previous)
                if response.decision == 'BID':
                    model = previous
            except (ValueError, KeyError, TypeError):
                pass
        if model is None:
            async with semaphore:
                model, amount = await evaluate_model(prepared, audit_engine=audit_engine)
        models[external_id] = model
        if amount is not None and not decision_rejections(model):
            bids[external_id] = amount

    await asyncio.gather(*(quote(external_id, prepared) for external_id, prepared in inputs.items()))
    return bids, models


def changed_decision_inputs(engine, result, config, run_id):
    inputs, prefix = {}, []
    with Session(engine) as session:
        run = session.get(AutopilotRun, run_id)
        rollback = session.begin_nested()
        try:
            for item in result.selected:
                order = session.get(Shipment, item.shipment_id)
                if item.order_type == 'open_bid' and not matches_evaluation(item.model_snapshot, item.evaluation):
                    version = hashlib.sha256(json.dumps([result.snapshot_fingerprint, prefix], sort_keys=True).encode()).hexdigest()
                    inputs[item.external_id] = prepare_decision(session, order, item.evaluation, config, version, run=run, capture_snapshot=True)
                prefix.append([item.external_id, str(item.unit_id), str(item.bid_rate)])
                virtual_booking(session, order, item.evaluation, item.bid_rate)
        finally:
            rollback.rollback()
    return inputs


async def approved_allocation(engine, snapshot, plan_id, run_id, config, bids, models, external_ids=None):
    if config.model_batch_enabled:
        # Model selection is authoritative. The local search may remove unsafe
        # selections, but cannot add a pair or change its unit/economics.
        selected_ids = [key for key, model in models.items() if model.get('selected') is True]
        return await asyncio.to_thread(solve_snapshot, snapshot, config, bids, run_id=run_id,
                                       models=models, external_ids=selected_ids), models
    for _ in range(3):
        solving = asyncio.create_task(asyncio.to_thread(solve_snapshot, snapshot, config, bids, run_id=run_id,
                                                       models=models, external_ids=external_ids))
        try:
            result = await asyncio.shield(solving)
        except asyncio.CancelledError:
            with suppress(Exception):
                await solving
            raise
        changed = await asyncio.to_thread(changed_decision_inputs, snapshot, result, config, run_id)
        if not changed:
            return result, models
        updated_bids, updated_models = await price_while_current(engine, plan_id, changed, {}, {}, config)
        for external_id in changed:
            bids.pop(external_id, None)
            history = models.get(external_id, {}).get('prior_evaluations', [])
            previous = {key: value for key, value in models.get(external_id, {}).items() if key != 'prior_evaluations'}
            updated_models[external_id]['prior_evaluations'] = [*history, previous]
        bids.update(updated_bids)
        models.update(updated_models)
    # No unstable model context is allowed to authorize a booking.
    for external_id in list(bids):
        bids.pop(external_id)
        models[external_id] = models[external_id] | {'error': 'MODEL_CONTEXT_REEVALUATION_LIMIT'}
    solving = asyncio.create_task(asyncio.to_thread(solve_snapshot, snapshot, config, bids, run_id=run_id,
                                                   models=models, external_ids=external_ids))
    try:
        result = await asyncio.shield(solving)
    except asyncio.CancelledError:
        with suppress(Exception):
            await solving
        raise
    return result, models


def reserve_plan(engine, plan_id, result, models, batch_analysis=None):
    from service.autopilot.autopilot import transaction
    try:
        with transaction(engine) as session:
            reap_plans(session)
            plan = session.get(AllocationPlan, plan_id)
            run = session.get(AutopilotRun, plan.run_id)
            plan.result = plan.result | result.model_dump(mode='json') | {'models': models}
            if batch_analysis is not None:
                plan.result = plan.result | {'batch_analysis': batch_analysis}
            plan.snapshot_fingerprint = result.snapshot_fingerprint
            if plan.status != 'planning' or check_run_limits(session, run):
                return None
            config = AutopilotSettings.model_validate(run.settings)
            # Unit positions and terminal counts move every board refresh, so a
            # whole-snapshot hash can never match by reservation time. The
            # submitted candidate is re-evaluated below against live state.
            now = wall_now()
            clock = session.get(SimulationClock, 1)
            sim = current_sim(session, now)
            slot = max(config.min_action_interval_seconds, config.allocation_submission_seconds)
            if len(result.selected) + run.orders_reserved > config.max_orders:
                cancel_plans(session, 'order_limit_changed', plan_id=plan_id)
                return None
            for index, item in enumerate(result.selected):
                if config.model_batch_enabled and item.model_snapshot.get('kind') != 'batch':
                    cancel_plans(session, 'model_batch_selection_missing', plan_id=plan_id)
                    return None
                quoted_at = item.model_snapshot.get('checked_at_wall')
                needs_model = item.order_type == 'open_bid' or item.model_snapshot.get('kind') == 'batch'
                if needs_model and (quoted_at is None or not -5 <= (
                    now - utc(datetime.fromisoformat(quoted_at))).total_seconds() <= config.max_decision_age_seconds):
                    cancel_plans(session, 'model_quote_stale', plan_id=plan_id)
                    return None
                if needs_model and (decision_rejections(item.model_snapshot, run.mode)
                        or not matches_evaluation(item.model_snapshot, item.evaluation)):
                    cancel_plans(session, 'model_decision_stale_or_changed', plan_id=plan_id)
                    return None
                if (utc(item.expires_at) <= sim + timedelta(seconds=index * slot + config.allocation_expiry_buffer_seconds)
                        or sim + timedelta(seconds=index * slot + config.allocation_expiry_buffer_seconds) >= utc(run.deadline_at_sim)):
                    cancel_plans(session, 'submission_deadline_changed', plan_id=plan_id)
                    return None
            if not result.selected:
                plan.status, plan.finished_at_wall = 'completed', now
                all_failed = bool(models) and all('error' in value or value.get('decision') == 'REVIEW' for value in models.values())
                run.consecutive_errors = run.consecutive_errors + 1 if all_failed else 0
                add_activity(session, 'allocation.empty', 'No compatible profitable allocation',
                             details={'plan_id': str(plan_id), 'rejected': result.rejected})
                check_run_limits(session, run)
                return None
            first = result.selected[0]
            shipment = session.get(Shipment, first.shipment_id)
            checked = evaluate_dispatch(session, shipment, first.bid_rate)
            if not checked.eligible or checked.candidate_unit_id != first.unit_id:
                cancel_plans(session, 'first_assignment_changed', plan_id=plan_id)
                return None
            plan.status = 'reserved'
            plan.expires_at_sim = min(utc(run.deadline_at_sim), sim + timedelta(seconds=config.allocation_reservation_ttl_seconds))
            plan.expires_at_wall = wall_estimate(session, plan.expires_at_sim, now) or now + timedelta(seconds=config.allocation_reservation_ttl_seconds)
            for item in result.selected:
                session.add(AllocationReservation(plan_id=plan_id, shipment_id=item.shipment_id, unit_id=item.unit_id,
                    status='reserved', busy_from=item.evaluation.occupied_window.occupied_from,
                    busy_until=item.evaluation.occupied_window.occupied_until, created_at_wall=now,
                    expires_at_wall=plan.expires_at_wall, expires_at_sim=plan.expires_at_sim))
                if item is first:
                    attempt = session.scalar(select(AutopilotAttempt).where(
                        AutopilotAttempt.run_id == run.run_id, AutopilotAttempt.shipment_id == item.shipment_id))
                    if attempt is None:
                        attempt = AutopilotAttempt(run_id=run.run_id, shipment_id=item.shipment_id,
                            external_id=item.external_id, order_type=item.order_type, created_at_wall=now)
                        session.add(attempt)
                    elif attempt.action_id is not None or attempt.status == 'shadow':
                        raise ProductError(409, 'Allocation order was already attempted')
                    attempt.status = 'ready'
                    attempt.decision = 'BID' if item.order_type == 'open_bid' else 'ACCEPT'
                    attempt.allocation_plan_id, attempt.candidate_unit_id = plan_id, item.unit_id
                    attempt.bid_rate, attempt.model_snapshot = item.bid_rate, item.model_snapshot
                    attempt.evaluation_snapshot, attempt.reason_codes = item.evaluation.model_dump(mode='json'), []
                    attempt.decided_at_wall, attempt.finished_at_wall = now, None
                    if item is first:
                        first_attempt = attempt
            session.flush()
            add_activity(session, 'allocation.reserved',
                         'Compatible fleet allocation recorded', details={'plan_id': str(plan_id),
                         'orders': len(result.selected), 'projected_profit': str(result.projected_profit),
                         'optimality': result.optimality})
            check_run_limits(session, run)
            return first_attempt.attempt_id, first.external_id, first.order_type, first.bid_rate
    except IntegrityError as exc:
        with transaction(engine) as session:
            cancel_plans(session, 'atomic_reservation_conflict', plan_id=plan_id)
        return None


def retire_plan(engine, plan_id, reason):
    from service.autopilot.autopilot import transaction
    with transaction(engine) as session:
        cancel_plans(session, reason, plan_id=plan_id)


async def process_allocation(engine):
    from service.autopilot import autopilot
    claimed = await asyncio.to_thread(claim_plan, engine)
    if claimed is None:
        return False
    plan_id, run_id, config, deadline, collection = claimed
    manager, snapshot, attempt_id = snapshot_engine(engine), None, None
    try:
        async with guard_run_clock(engine, run_id):
            snapshot = await asyncio.to_thread(manager.__enter__)
            external_ids = (collection.get('remaining_external_ids', collection['eligible_external_ids'])
                            if collection else None)
            batch_analysis = None
            if config.model_batch_enabled:
                from service.autopilot.model_batch import prepare_batch
                batch, inputs, errors = await asyncio.to_thread(prepare_batch, snapshot, config, run_id, plan_id, external_ids)
                if batch is None:
                    bids, models, batch_analysis = {}, errors, {'error': 'MODEL_BATCH_NO_USABLE_PAIRS'}
                else:
                    bids, models, batch_analysis = await price_while_current(engine, plan_id, inputs, errors, {}, config, batch=batch)
            else:
                inputs, errors, cache = await asyncio.to_thread(price_inputs, snapshot, config, run_id, external_ids)
                bids, models = await price_while_current(engine, plan_id, inputs, errors, cache, config)
            result, models = await approved_allocation(engine, snapshot, plan_id, run_id, config, bids, models, external_ids)
            result.plan_id = plan_id
            for external_id, model in models.items():
                rejected = decision_rejections(model)
                if rejected:
                    result.rejected[external_id] = rejected
            authorized = await asyncio.to_thread(reserve_plan, engine, plan_id, result, models, batch_analysis)
            if authorized:
                attempt_id, external_id, kind, bid = authorized
                request = BidRequest(bid_rate=bid, carrier_note=f'autopilot:{run_id}') if kind == 'open_bid' else ActionRequest(carrier_note=f'autopilot:{run_id}')
                await autopilot.execute_action(engine, external_id, 'bid' if kind == 'open_bid' else 'accept', request,
                                               autopilot_attempt_id=attempt_id)
                await asyncio.to_thread(autopilot.finish_attempt, engine, attempt_id)
    except PlanInvalidated:
        pass  # A newer snapshot or explicit stop requires a new plan, not an action retry.
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(autopilot.recover_runs, engine, 'backend_shutdown'))
        raise
    except Exception as exc:
        logger.exception('Autopilot plan %s failed: %s', plan_id, exc)
        if attempt_id:
            await asyncio.to_thread(autopilot.finish_attempt, engine, attempt_id, str(exc))
        else:
            from service.autopilot.autopilot import transaction
            with transaction(engine) as session:
                plan = session.get(AllocationPlan, plan_id)
                plan.result = plan.result | {
                    'error': type(exc).__name__, 'error_detail': str(exc)[:500],
                }
                run = session.get(AutopilotRun, run_id)
                run.consecutive_errors += 1
                check_run_limits(session, run)
    finally:
        if snapshot is not None:
            await asyncio.to_thread(manager.__exit__, None, None, None)
        await asyncio.to_thread(retire_plan, engine, plan_id, 'replan_after_action_or_evaluation')
    return True


def list_plans(engine, run_id, limit, offset):
    with Session(engine) as session:
        if session.get(AutopilotRun, run_id) is None:
            raise ProductError(404, 'Autopilot run not found')
        rows = list(session.scalars(select(AllocationPlan).where(AllocationPlan.run_id == run_id).order_by(
            AllocationPlan.created_at_wall.desc(), AllocationPlan.plan_id)))
        items = [AllocationPlanView.model_validate(row) for row in rows[offset:offset + limit]]
        return Page(items=items, total=len(rows), limit=limit, offset=offset)
