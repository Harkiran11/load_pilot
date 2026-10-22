import asyncio
import time
from contextlib import contextmanager
from datetime import timedelta
import logging

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from schema.shared.api import ActionRequest, BidRequest, Page
from schema.autopilot.autopilot import AutopilotAttemptView, AutopilotRunView, AutopilotSettings
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.autopilot.autopilot_run import AutopilotRun
from schema.dispatch.dispatch_action import DispatchAction
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.dispatch.actions import execute_action
from service.audit.activity import add_activity
from service.autopilot.autopilot_guards import (
    INFLIGHT, candidate_rejections, check_run_limits, live_enabled, stop_run_record, wall_now,
)
from service.shared.database import get_engine, is_locked_error, write_retry_attempts
from service.dispatch.dispatch_policy import evaluate_dispatch
from service.shared.errors import ProductError
from service.orders.expiration import utc
from service.pricing.price_analysis import analysis_settings, make_model_client
from service.dispatch.ranking import recommendations
from service.pricing.model_lane import model_shipment_features
from service.simulation.simulation_time import attempt_due, current_sim, guard_run_clock, remaining_seconds, wall_estimate

logger = logging.getLogger(__name__)


@contextmanager
def transaction(engine):
    if engine.dialect.name != 'sqlite':
        raise ProductError(503, 'Atomic autopilot control currently requires SQLite')
    last_error: OperationalError | None = None
    for attempt in range(write_retry_attempts()):
        try:
            with Session(engine) as session, session.begin():
                session.execute(text('BEGIN IMMEDIATE'))
                yield session
            return
        except OperationalError as error:
            # A checkpoint or a long reader can outlast busy_timeout. Retry the
            # whole control transaction so "Start" does not surface as a 500
            # that leaves the caller unsure whether the run was created.
            if not is_locked_error(error):
                raise
            last_error = error
            time.sleep(min(0.25 * (attempt + 1), 2.0))
    raise ProductError(503, 'The dispatcher database is busy; retry in a moment.') from last_error


def run_view(session, run):
    wall = wall_now()
    clock = session.get(SimulationClock, 1)
    try:
        sim = current_sim(session, wall)
        remaining = remaining_seconds(session, run, wall)
    except ProductError:
        sim, remaining = None, 0
    from service.autopilot.model_autopilot import live_model_token
    try:
        live_model_token()
        auth_configured = True
    except ValueError:
        auth_configured = False
    config = AutopilotSettings.model_validate(run.settings)
    fresh = clock is not None and clock.last_synced_at is not None and -5 <= (wall - utc(clock.last_synced_at)).total_seconds() <= config.max_input_age_seconds
    live = run.mode == 'live' and run.status == 'running' and remaining > 0 and sim is not None and fresh and clock.simulation_speed > 0 and live_enabled()
    fixed_enabled = live and config.enable_fixed_rate and (auth_configured or not config.model_batch_enabled)
    bids_enabled = live and config.enable_open_bid and auth_configured
    from service.autopilot.order_collection import collection_view
    return AutopilotRunView(
        **{field: getattr(run, field) for field in (
            'run_id', 'status', 'mode', 'settings', 'started_at_wall',
            'stopped_at_wall', 'stop_reason', 'orders_reserved', 'estimated_loss_usd',
            'pending_estimated_loss_usd', 'consecutive_errors',
        )},
        time_basis='simulation_clock' if run.deadline_at_sim is not None else 'wall_clock_legacy',
        started_at_sim=run.started_at_sim, deadline_at_sim=run.deadline_at_sim, stopped_at_sim=run.stopped_at_sim,
        sim_time_utc=sim, simulation_speed=clock.simulation_speed if clock is not None else None,
        deadline_at_wall=wall_estimate(session, run.deadline_at_sim, wall) if sim is not None and run.deadline_at_sim else run.deadline_at_wall,
        estimated_deadline_at_wall=wall_estimate(session, run.deadline_at_sim, wall) if sim is not None else None,
        loss_basis='booking_time_estimates' if run.mode == 'live' else 'shadow_decision_estimates',
        seconds_remaining=remaining,
        inflight_attempt_ids=list(session.scalars(select(AutopilotAttempt.attempt_id).where(
            AutopilotAttempt.run_id == run.run_id, AutopilotAttempt.status.in_(INFLIGHT),
        ))),
        submissions_enabled=fixed_enabled or bids_enabled,
        model_decision_mode=run.mode, model_live_submission_supported=True,
        model_live_auth_configured=auth_configured,
        open_bid_submissions_enabled=bids_enabled, fixed_rate_submissions_enabled=fixed_enabled,
        **collection_view(session, run),
    )


def start_run(engine, request):
    if request.mode != 'live':
        raise ProductError(422, 'Autopilot runs are live-only; use allocation preview for read-only analysis')
    settings = AutopilotSettings.model_validate(request.model_dump(exclude={'run_id', 'mode'}))
    try:
        with transaction(engine) as session:
            existing = session.get(AutopilotRun, request.run_id)
            if existing:
                if AutopilotSettings.model_validate(existing.settings) != settings or existing.mode != request.mode:
                    raise ProductError(409, 'This run_id already has different settings')
                check_run_limits(session, existing)
                return run_view(session, existing)
            if request.mode == 'live' and not live_enabled():
                raise ProductError(409, 'Live autopilot is disabled; set AUTOPILOT_LIVE_ENABLED=true to allow execution')
            if session.scalar(select(AutopilotRun).where(AutopilotRun.status == 'running')):
                raise ProductError(409, 'An autopilot run is already active; stop it before starting another')
            if request.mode == 'live' and session.scalar(select(AutopilotAttempt.attempt_id).join(
                DispatchAction, DispatchAction.action_id == AutopilotAttempt.action_id,
            ).where(DispatchAction.request_status.in_(('submitting', 'unknown')))):
                raise ProductError(409, 'Reconcile the previous autopilot submission before starting a new live run')
            now = wall_now()
            sim = current_sim(session, now)
            clock = session.get(SimulationClock, 1)
            if not -5 <= (now - utc(clock.last_synced_at)).total_seconds() <= settings.max_input_age_seconds:
                raise ProductError(503, 'Simulation clock is stale; synchronize the board before starting autopilot')
            deadline_sim = sim + timedelta(seconds=settings.max_duration_seconds)
            run = AutopilotRun(
                run_id=request.run_id, mode=request.mode, status='running', settings=settings.model_dump(mode='json'),
                started_at_wall=now, deadline_at_wall=wall_estimate(session, deadline_sim, now) or now + timedelta(seconds=settings.max_duration_seconds),
                started_at_sim=sim, deadline_at_sim=deadline_sim,
                last_clock_sim_at=clock.sim_time_utc, last_clock_synced_at_wall=clock.last_synced_at,
                orders_reserved=0, estimated_loss_usd=0, pending_estimated_loss_usd=0, consecutive_errors=0,
            )
            session.add(run)
            session.flush()
            add_activity(session, 'autopilot.started', f'{run.mode} autopilot started', details={
                'run_id': str(run.run_id), 'settings': run.settings, 'mode': run.mode,
                'time_basis': 'simulation_clock', 'started_at_sim': sim.isoformat(), 'deadline_at_sim': deadline_sim.isoformat(),
            })
            return run_view(session, run)
    except IntegrityError as exc:
        raise ProductError(409, 'An autopilot run is already active') from exc


def _apply_limits_if_running(engine, run_ids: list) -> None:
    """Take the writer lock only when a running row may actually need a limit decision."""
    if not run_ids:
        return
    with transaction(engine) as session:
        for run in session.scalars(select(AutopilotRun).where(AutopilotRun.run_id.in_(run_ids))):
            check_run_limits(session, run)


def get_run(engine, run_id, *, stop=False):
    if stop:
        with transaction(engine) as session:
            run = session.get(AutopilotRun, run_id)
            if run is None:
                raise ProductError(404, 'Autopilot run not found')
            stop_run_record(session, run, 'user_stopped')
            for attempt in session.scalars(select(AutopilotAttempt).where(
                AutopilotAttempt.run_id == run_id, AutopilotAttempt.status.in_(('evaluating', 'ready')),
                AutopilotAttempt.action_id.is_(None),
            )):
                attempt.status, attempt.finished_at_wall, attempt.reason_codes = 'cancelled', wall_now(), ['user_stopped']
            check_run_limits(session, run)
            session.flush()
            return run_view(session, run)
    with Session(engine) as session:
        run = session.get(AutopilotRun, run_id)
        if run is None:
            raise ProductError(404, 'Autopilot run not found')
        running = run.status == 'running'
    _apply_limits_if_running(engine, [run_id] if running else [])
    with Session(engine) as session:
        run = session.get(AutopilotRun, run_id)
        if run is None:
            raise ProductError(404, 'Autopilot run not found')
        return run_view(session, run)


def list_runs(engine, limit, offset):
    # Polling must never queue on the writer: read the page first and only
    # enter an IMMEDIATE transaction when a running row needs a limit check.
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(AutopilotRun)) or 0
        page = list(session.scalars(
            select(AutopilotRun)
            .order_by(AutopilotRun.started_at_wall.desc(), AutopilotRun.run_id)
            .limit(limit).offset(offset)
        ))
        running = [run.run_id for run in page if run.status == 'running']
    _apply_limits_if_running(engine, running)
    with Session(engine) as session:
        page = list(session.scalars(
            select(AutopilotRun)
            .order_by(AutopilotRun.started_at_wall.desc(), AutopilotRun.run_id)
            .limit(limit).offset(offset)
        ))
        return Page(items=[run_view(session, row) for row in page], total=total, limit=limit, offset=offset)


def list_attempts(engine, run_id, limit, offset):
    from sqlalchemy import func
    with Session(engine) as session:
        if session.get(AutopilotRun, run_id) is None:
            raise ProductError(404, 'Autopilot run not found')
        total = session.scalar(select(func.count()).select_from(AutopilotAttempt).where(
            AutopilotAttempt.run_id == run_id)) or 0
        rows = session.scalars(select(AutopilotAttempt).where(AutopilotAttempt.run_id == run_id).order_by(
            AutopilotAttempt.created_at_wall.desc(), AutopilotAttempt.attempt_id,
        ).limit(limit).offset(offset))
        items = []
        for row in rows:
            view = AutopilotAttemptView.model_validate(row)
            # Polling clients only need the table fields. Full model/evaluation
            # snapshots are hundreds of kilobytes each; fetch them on demand.
            items.append(view.model_copy(update={
                'model_snapshot': {},
                'evaluation_snapshot': {
                    'economics': (view.evaluation_snapshot or {}).get('economics'),
                },
            }))
        return Page(items=items, total=total, limit=limit, offset=offset)


def get_attempt(engine, run_id, attempt_id):
    with Session(engine) as session:
        if session.get(AutopilotRun, run_id) is None:
            raise ProductError(404, 'Autopilot run not found')
        attempt = session.get(AutopilotAttempt, attempt_id)
        if attempt is None or attempt.run_id != run_id:
            raise ProductError(404, 'Autopilot attempt not found')
        return AutopilotAttemptView.model_validate(attempt)


def recover_runs(engine, reason='backend_restarted'):
    with transaction(engine) as session:
        for run in session.scalars(select(AutopilotRun).where(AutopilotRun.status == 'running')):
            stop_run_record(session, run, reason)
            for attempt in session.scalars(select(AutopilotAttempt).where(
                AutopilotAttempt.run_id == run.run_id, AutopilotAttempt.status.in_(INFLIGHT),
            )):
                # A committed action may have reached the board. Keep its locks
                # and action status for the ordinary reconciliation pipeline.
                action = session.get(DispatchAction, attempt.action_id) if attempt.action_id else None
                attempt.status = ('submitted' if action.request_status == 'succeeded' else
                                  'rejected' if action.request_status == 'failed' else 'unknown') if action else 'cancelled'
                attempt.reason_codes, attempt.finished_at_wall = [reason], wall_now()


def claim_order(engine):
    with transaction(engine) as session:
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
            AutopilotAttempt.run_id == run.run_id, AutopilotAttempt.status.in_(INFLIGHT),
        )):
            return None
        if not attempt_due(session, run, config.min_action_interval_seconds, wall_now()):
            return None
        types = [kind for kind, enabled in (('fixed_rate', config.enable_fixed_rate), ('open_bid', config.enable_open_bid)) if enabled]
        attempted = select(AutopilotAttempt.shipment_id).where(AutopilotAttempt.run_id == run.run_id)
        pending = list(session.scalars(select(Shipment).where(
            Shipment.status == 'open', Shipment.board_missing_at.is_(None), Shipment.board_retired_at.is_(None),
            Shipment.order_type.in_(types), Shipment.shipment_id.not_in(attempted),
        ).order_by(Shipment.expires_at, Shipment.external_id)))
        if not pending:
            return None
        by_external_id = {row.external_id: row for row in pending}
        selected = pending[0]
        # Prefer the existing cash/continuity/empty-mile ranking. Only matches
        # compatible with the actual board-style selection can be automated.
        try:
            ranked_items = recommendations(session, limit=200).items
        except ProductError:
            ranked_items = []  # Persist a REVIEW attempt for missing clock/input data.
        for item in ranked_items:
            if item.external_id in by_external_id and item.dispatch_rejection_reasons != ['unit_not_board_selectable']:
                selected = by_external_id[item.external_id]
                break
        now = wall_now()
        attempt = AutopilotAttempt(
            run_id=run.run_id, shipment_id=selected.shipment_id, external_id=selected.external_id,
            order_type=selected.order_type, status='evaluating', created_at_wall=now,
            model_snapshot={}, evaluation_snapshot={}, reason_codes=[],
        )
        run.last_attempt_at_wall = now
        run.last_attempt_at_sim = current_sim(session, now)
        session.add(attempt)
        session.flush()
        add_activity(session, 'autopilot.evaluating', 'Autopilot claimed an order for evaluation',
                     shipment_id=selected.shipment_id, details={'run_id': str(run.run_id), 'attempt_id': str(attempt.attempt_id)})
        return attempt.attempt_id, run.run_id, utc(run.deadline_at_wall)


def candidate_snapshot(engine, attempt_id, bid_rate=None, unit_id=None):
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        attempt = session.get(AutopilotAttempt, attempt_id)
        run = session.get(AutopilotRun, attempt.run_id)
        shipment = session.get(Shipment, attempt.shipment_id)
        checked = evaluate_dispatch(session, shipment, bid_rate, unit_id)
        features = None
        lane = None
        if shipment.order_type == 'open_bid':
            features, lane = model_shipment_features(session, shipment)
        reasons = candidate_rejections(session, run, shipment, checked, bid_rate)
        return checked, reasons, features, AutopilotSettings.model_validate(run.settings), lane


def decision_snapshot(engine, attempt_id):
    from service.allocation.fleet_allocation import fingerprint
    from service.autopilot.model_autopilot import prepare_decision
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        attempt = session.get(AutopilotAttempt, attempt_id)
        run = session.get(AutopilotRun, attempt.run_id)
        shipment = session.get(Shipment, attempt.shipment_id)
        checked = evaluate_dispatch(session, shipment)
        return checked, prepare_decision(session, shipment, checked,
            AutopilotSettings.model_validate(run.settings), fingerprint(session), run=run, capture_snapshot=True)


def store_decision(engine, attempt_id, checked, reasons, *, model=None, bid_rate=None, review=False):
    with transaction(engine) as session:
        attempt = session.get(AutopilotAttempt, attempt_id)
        run = session.get(AutopilotRun, attempt.run_id)
        if check_run_limits(session, run) or attempt.status != 'evaluating':
            if attempt.status == 'evaluating':
                attempt.status, attempt.finished_at_wall = 'cancelled', wall_now()
                attempt.reason_codes = [run.stop_reason or 'autopilot_not_authorized']
            return False
        attempt.candidate_unit_id = checked.candidate_unit_id if checked else None
        attempt.evaluation_snapshot = checked.model_dump(mode='json') if checked else {}
        attempt.model_snapshot, attempt.bid_rate = model or {}, bid_rate
        attempt.reason_codes, attempt.decided_at_wall = list(dict.fromkeys(reasons)), wall_now()
        attempt.decision = ('REVIEW' if review else 'SKIP') if reasons else ('BID' if attempt.order_type == 'open_bid' else 'ACCEPT')
        attempt.status = 'rejected' if reasons else 'ready'
        if reasons:
            attempt.finished_at_wall = wall_now()
            run.consecutive_errors = run.consecutive_errors + 1 if review else 0
        add_activity(session, 'autopilot.decision', f'Autopilot decision: {attempt.decision}', shipment_id=attempt.shipment_id,
                     details={'run_id': str(run.run_id), 'attempt_id': str(attempt_id), 'decision': attempt.decision,
                              'reason_codes': attempt.reason_codes, 'mode': run.mode})
        check_run_limits(session, run)
        return not reasons


def finish_attempt(engine, attempt_id, error=None):
    with transaction(engine) as session:
        attempt = session.get(AutopilotAttempt, attempt_id)
        run = session.get(AutopilotRun, attempt.run_id)
        action = session.get(DispatchAction, attempt.action_id) if attempt.action_id else None
        if action is not None:
            attempt.status = 'submitted' if action.request_status == 'succeeded' else 'rejected' if action.request_status == 'failed' else 'unknown'
        elif attempt.status not in ('cancelled', 'shadow'):
            attempt.status = 'cancelled' if run.status == 'stopped' else 'rejected'
        attempt.finished_at_wall = wall_now()
        if error:
            attempt.message = str(error)[:2000]
            if not attempt.reason_codes:
                attempt.reason_codes = ['action_error']
            if attempt.decision is None:
                attempt.decision = 'REVIEW'
        if attempt.status == 'unknown':
            stop_run_record(session, run, 'submission_outcome_unknown')
        run.consecutive_errors = run.consecutive_errors + 1 if error else 0
        add_activity(session, 'autopilot.attempt_finished', f'Autopilot attempt {attempt.status}',
                     shipment_id=attempt.shipment_id, action_id=attempt.action_id,
                     details={'run_id': str(run.run_id), 'attempt_id': str(attempt_id), 'status': attempt.status})
        check_run_limits(session, run)


async def process_one(engine):
    with Session(engine) as session:
        run = session.scalar(select(AutopilotRun).where(AutopilotRun.status == 'running'))
        batch = run is not None and AutopilotSettings.model_validate(run.settings).allocation_enabled
    if run is None:
        # Idle history polling must stay read-only; claiming would otherwise
        # open an IMMEDIATE transaction ten times a second for no work.
        return False
    if batch:
        from service.allocation.allocation_dispatch import process_allocation
        return await process_allocation(engine)
    claimed = await asyncio.to_thread(claim_order, engine)
    if claimed is None:
        return False
    attempt_id, run_id, deadline = claimed
    checked, model = None, {}
    try:
        async with guard_run_clock(engine, run_id):
            checked, reasons, features, config, lane = await asyncio.to_thread(candidate_snapshot, engine, attempt_id)
            bid = None
            if features is not None:
                operational = [code for code in checked.rejection_reasons
                               if next(item.check for item in checked.rejection_details if item.code == code) != 'pricing_passed']
                operational.extend(code for code in reasons if code not in ('cash_economics_unknown', 'maximum_bid_exceeded_or_unknown'))
                if operational or checked.economics.total_cost is None:
                    reasons = operational or ['cash_economics_unknown']
                else:
                    from service.autopilot.model_autopilot import decision_rejections, evaluate_model, matches_evaluation
                    checked, prepared = await asyncio.to_thread(decision_snapshot, engine, attempt_id)
                    model, bid = await evaluate_model(prepared, audit_engine=engine)
                    reasons = decision_rejections(model)
                    if bid is not None and not reasons:
                        checked, reasons, _, _, _ = await asyncio.to_thread(
                            candidate_snapshot, engine, attempt_id, bid, checked.candidate_unit_id,
                        )
                        reasons = [*checked.rejection_reasons, *reasons]
                        if not matches_evaluation(model, checked):
                            reasons.append('MODEL_CONTEXT_CHANGED')
            else:
                reasons = [*checked.rejection_reasons, *reasons]
            ready = await asyncio.to_thread(store_decision, engine, attempt_id, checked, reasons,
                                           model=model, bid_rate=bid,
                                           review=model.get('decision') == 'REVIEW'
                                           or any(code in reasons for code in ('MODEL_SHADOW_ONLY', 'MODEL_LIVE_NOT_ELIGIBLE'))
                                           or any('unknown' in code or 'stale' in code or 'baseline' in code for code in reasons))
            if not ready:
                return True
            with Session(engine) as session:
                attempt = session.get(AutopilotAttempt, attempt_id)
                run = session.get(AutopilotRun, run_id)
                external_id, kind = attempt.external_id, attempt.order_type
            request = BidRequest(bid_rate=bid, carrier_note=f'autopilot:{run_id}') if kind == 'open_bid' else ActionRequest(carrier_note=f'autopilot:{run_id}')
            await execute_action(engine, external_id, 'bid' if kind == 'open_bid' else 'accept', request,
                                 autopilot_attempt_id=attempt_id)
            await asyncio.to_thread(finish_attempt, engine, attempt_id)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(recover_runs, engine, 'backend_shutdown'))
        raise
    except TimeoutError:
        with transaction(engine) as session:
            stopped = check_run_limits(session, session.get(AutopilotRun, run_id))
        if stopped:
            await asyncio.to_thread(finish_attempt, engine, attempt_id, 'Autopilot time limit reached')
        else:
            await asyncio.to_thread(store_decision, engine, attempt_id, checked, ['MODEL_TIMEOUT'], model=model, review=True)
    except ProductError as exc:
        await asyncio.to_thread(finish_attempt, engine, attempt_id, exc.detail)
    except Exception as exc:
        with Session(engine) as session:
            status = session.get(AutopilotAttempt, attempt_id).status
        if status == 'evaluating':
            await asyncio.to_thread(store_decision, engine, attempt_id, checked,
                                   [f'evaluation_error:{type(exc).__name__}'], model=model, review=True)
        else:
            await asyncio.to_thread(finish_attempt, engine, attempt_id, type(exc).__name__)
    return True


async def autopilot_worker():
    while True:
        await asyncio.sleep(.5)
        try:
            await process_one(get_engine())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Autopilot worker failed; no unguarded submission is attempted')
