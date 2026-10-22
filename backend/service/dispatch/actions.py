import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from schema.shared.api import ActionRequest, ActionResult, ActionView, BoardActionResponse
from schema.dispatch.dispatch_action import DispatchAction
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.audit.activity import add_activity, record_system_event
from service.analytics.dashboard import load_detail
from service.shared.database import PROJECT_ROOT
from service.shared.errors import ProductError
from service.orders.expiration import seconds_remaining, simulated_now
from service.ingestion.ingest import ingest, persist_orders
from service.dispatch.dispatch_policy import evaluate_dispatch, save_evaluation
from service.dispatch.reservations import release_resolved_reservations


def board_url() -> str:
    config = dotenv_values(PROJECT_ROOT / '.env')
    url = os.environ.get('EXTERNAL_API_URL', config.get('EXTERNAL_API_URL', ''))
    if not url or not url.strip():
        raise ProductError(503, 'Board API is not configured')
    try:
        parsed = urlsplit(url.strip())
        if parsed.port == 0:
            raise ValueError('Invalid port')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Invalid base URL')
    except ValueError as exc:
        raise ProductError(503, 'Board API must have an absolute HTTP(S) base URL') from exc
    return url.strip().rstrip('/') + '/'


def make_board_client(url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=url, timeout=20.0)


def begin_action(engine: Engine, external_id: str, kind: str, request: ActionRequest, *, autopilot_attempt_id=None):
    if autopilot_attempt_id is not None and kind not in ('accept', 'bid'):
        raise ProductError(409, 'Autopilot supports fixed-rate accepts and open bids only')
    denial = None
    action_id = None
    try:
        with Session(engine) as session, session.begin():
            if engine.dialect.name != 'sqlite':
                raise ProductError(503, 'Atomic dispatcher reservations require SQLite')
            # Acquire the database write lock before reading schedules. Commit
            # the reservation before HTTP; no database lock spans network I/O.
            session.execute(text('BEGIN IMMEDIATE'))
            from service.allocation.allocation_holds import reap_plans
            reap_plans(session)
            if autopilot_attempt_id is not None:
                from schema.autopilot.autopilot_attempt import AutopilotAttempt
                attempt = session.get(AutopilotAttempt, autopilot_attempt_id)
                if attempt is None:
                    raise ProductError(409, 'Autopilot attempt not found')
                session.info['allocation_plan_id'] = attempt.allocation_plan_id if attempt else None
            shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
            if shipment is None:
                raise ProductError(404, 'Order not found')
            if kind == 'decline' and shipment.board_missing_at is not None:
                raise ProductError(409, 'Order is no longer present on the board')
            if kind == 'decline' and shipment.status != 'open':
                raise ProductError(409, f'Order is {shipment.status}, not open')
            if (kind == 'accept' and shipment.order_type != 'fixed_rate') or (kind == 'bid' and shipment.order_type != 'open_bid'):
                raise ProductError(409, 'Action does not match the order type')
            clock = session.get(SimulationClock, 1)
            if clock is None:
                raise ProductError(503, 'Simulation clock has not been synced yet')
            if kind == 'decline' and seconds_remaining(shipment.expires_at, simulated_now(clock)) == 0:
                raise ProductError(409, 'Order has expired')
            evaluation = None
            submission_audit = None
            if kind in ('accept', 'bid'):
                checked = evaluate_dispatch(session, shipment, getattr(request, 'bid_rate', None))
                evaluation = save_evaluation(session, shipment, checked)
                if autopilot_attempt_id is not None:
                    from service.audit.live_audit import start_submission
                    submission_audit = start_submission(session, attempt, shipment, request, checked)
                    from service.autopilot.autopilot_guards import authorize_attempt
                    denial = authorize_attempt(session, autopilot_attempt_id, shipment, checked, request)
                if denial is None and not checked.eligible:
                    denial = ProductError(
                        409, f'Dispatch rejected: {checked.primary_rejection.message} ({checked.primary_rejection.code})',
                        evaluation=checked.model_dump(mode='json'),
                    )
                    add_activity(session, 'dispatch.rejected', denial.detail,
                                 shipment_id=shipment.shipment_id,
                                 details={'evaluation_id': str(evaluation.evaluation_id)})
                if denial is not None and submission_audit is not None:
                    submission_audit.status = 'rejected'
                    submission_audit.authorization_passed = False
                    submission_audit.operational_checks_passed = checked.eligible
                    submission_audit.atomic_reservation_acquired = False
                    submission_audit.result = {'message': denial.detail, 'evaluation': checked.model_dump(mode='json'),
                                               'sent_to_board': False}
            if denial is None:
                if kind == 'bid':
                    shipment.bid_history_deleted_at = None
                action = DispatchAction(
                    shipment_id=shipment.shipment_id, action_type=kind,
                    evaluation_id=evaluation.evaluation_id if evaluation else None,
                    bid_rate=getattr(request, 'bid_rate', None), carrier_note=request.carrier_note,
                    request_status='submitting', requested_at=datetime.now(timezone.utc),
                )
                session.add(action)
                session.flush()
                if kind == 'bid' and getattr(request, 'price_analysis_id', None) is not None:
                    from service.audit.prediction_capture import attach_execution
                    attach_execution(session, request.price_analysis_id, shipment, request, action)
                if submission_audit is not None:
                    submission_audit.action_id = action.action_id
                if autopilot_attempt_id is not None:
                    from service.autopilot.autopilot_guards import attach_action
                    attach_action(session, autopilot_attempt_id, action.action_id)
                    identity = attempt.model_snapshot.get('observation_id')
                    if identity:
                        from schema.audit.prediction_observation import PredictionObservation
                        original = session.get(PredictionObservation, UUID(identity))
                        original.executions = original.executions + [{'action_id': str(action.action_id),
                            'requested_at_wall': action.requested_at.isoformat(), 'bid_rate': str(action.bid_rate),
                            'evaluation_id': str(action.evaluation_id), 'request_status': action.request_status}]
                if evaluation is not None:
                    session.add(AssignmentReservation(
                        action_id=action.action_id, unit_id=evaluation.candidate_unit_id,
                        busy_from=checked.occupied_window.occupied_from, busy_until=checked.occupied_window.occupied_until,
                        created_at=datetime.now(timezone.utc),
                    ))
                    session.flush()
                    if submission_audit is not None:
                        submission_audit.atomic_reservation_acquired = True
                add_activity(session, 'action.requested', f'{kind} requested',
                             shipment_id=shipment.shipment_id, action_id=action.action_id)
                action_id = action.action_id
        # Raise after commit so rejected policy evaluations remain reviewable.
        if denial is not None:
            raise denial
        return action_id
    except IntegrityError as exc:
        raise ProductError(409, 'An unresolved submission or conflicting unit reservation already exists') from exc
    except OperationalError as exc:
        if engine.dialect.name == 'sqlite' and (getattr(exc.orig, 'sqlite_errorcode', 0) & 255) in (5, 6):
            raise ProductError(503, 'Dispatcher database is busy; retry before submitting') from exc
        raise


def finish_action(engine, action_id, status, message, board_http_status=None, board_response=None):
    with Session(engine) as session, session.begin():
        action = session.get(DispatchAction, action_id)
        if session.get(Shipment, action.shipment_id).board_retired_at is not None:
            return
        # A concurrent board sync may already have established the outcome.
        if action.request_status == 'succeeded' and status != 'succeeded':
            release_resolved_reservations(session)
            return
        action.request_status = status
        action.board_message = message
        action.board_http_status = board_http_status
        action.resolved_at = datetime.now(timezone.utc) if status != 'unknown' else None
        from service.audit.live_audit import update_submission_result
        update_submission_result(session, action, board_response)
        from service.audit.prediction_capture import execution_result
        execution_result(session, action, session.get(Shipment, action.shipment_id))
        add_activity(session, f'action.{status}', message,
                     shipment_id=action.shipment_id, action_id=action_id,
                     details={'board_http_status': board_http_status})
        release_resolved_reservations(session)


def result(engine, external_id, action_id, sync_pending):
    with Session(engine) as session:
        stored = session.get(DispatchAction, action_id)
        if session.get(Shipment, stored.shipment_id).board_retired_at is not None:
            raise ProductError(409, 'Board reset superseded this submission; refresh the live queue')
        return ActionResult(
            action=ActionView.model_validate(session.get(DispatchAction, action_id)),
            order=load_detail(session, external_id),
            sync_pending=sync_pending or session.get(AssignmentReservation, action_id) is not None,
        )


async def execute_action(engine: Engine, external_id: str, kind: str, request: ActionRequest, *, autopilot_attempt_id=None) -> ActionResult:
    url = board_url()
    action_id = await asyncio.to_thread(begin_action, engine, external_id, kind, request, autopilot_attempt_id=autopilot_attempt_id)
    try:
        async with make_board_client(url) as client:
            if autopilot_attempt_id is not None:
                from service.autopilot.autopilot_guards import before_send
                await asyncio.to_thread(before_send, engine, autopilot_attempt_id, action_id)
            response = await client.post(
                'orders/' + quote(external_id, safe='') + '/' + kind,
                json=request.model_dump(mode='json', exclude_none=True, exclude={'price_analysis_id'}),
            )
    except httpx.HTTPError as exc:
        await asyncio.to_thread(finish_action, engine, action_id, 'unknown',
                                f'Board submission outcome unknown: {type(exc).__name__}')
        raise ProductError(504 if isinstance(exc, httpx.TimeoutException) else 502,
                           'Board submission outcome is unknown; wait for reconciliation before retrying') from exc
    def retired():
        with Session(engine) as session:
            action = session.get(DispatchAction, action_id)
            return session.get(Shipment, action.shipment_id).board_retired_at is not None
    if await asyncio.to_thread(retired):
        raise ProductError(409, 'Board reset superseded this submission; refresh the live queue')
    if not response.is_success:
        try:
            message = str(response.json().get('detail', 'Board rejected the action'))[:2000]
        except (ValueError, AttributeError):
            message = 'Board rejected the action'
        request_status = 'failed' if response.status_code < 500 else 'unknown'
        await asyncio.to_thread(finish_action, engine, action_id, request_status, message, response.status_code, response.text)
        feedback = None
        if response.status_code in (400, 404, 409):
            sync_pending = False
            try:
                await ingest(url, engine=engine, tracked_order_ids={external_id})
            except Exception as exc:
                sync_pending = True
                await asyncio.to_thread(record_system_event, engine, 'sync.error', type(exc).__name__)
            refreshed = await asyncio.to_thread(result, engine, external_id, action_id, sync_pending)
            feedback = {
                'code': 'board_rejected', 'source': 'board', 'board_http_status': response.status_code,
                'suggested_action': ('State refresh failed; restore board connectivity and sync before retrying.'
                                     if sync_pending else
                                     'Review the refreshed order, fleet capacity, and evaluation before choosing a new action.'),
                'state_refreshed': not sync_pending,
                'action_result': refreshed.model_dump(mode='json'),
            }
        raise ProductError(response.status_code if 400 <= response.status_code < 500 else 502,
                           message, feedback=feedback)
    try:
        body = BoardActionResponse.model_validate(response.json(parse_float=Decimal))
        if not body.ok or body.order.external_id != external_id:
            raise ValueError('Invalid board action response')
        expected_statuses = {
            'accept': ('accepted', 'in_transit', 'completed'),
            'decline': ('declined',),
            'bid': ('bid_submitted', 'bid_lost', 'accepted', 'in_transit', 'completed'),
        }
        if body.order.status not in expected_statuses[kind]:
            raise ValueError('Board action response did not establish the expected outcome')
    except (ValueError, ValidationError) as exc:
        await asyncio.to_thread(finish_action, engine, action_id, 'unknown', 'Invalid board action response', response.status_code, response.text)
        raise ProductError(502, 'Board action response could not be verified; reconciliation required') from exc
    try:
        await asyncio.to_thread(persist_orders, engine, [body.order])
        await asyncio.to_thread(finish_action, engine, action_id, 'succeeded', body.message, response.status_code,
                                response.json())
    except Exception as exc:
        await asyncio.to_thread(finish_action, engine, action_id, 'unknown', 'Board replied successfully but local persistence failed', response.status_code)
        raise ProductError(502, 'Board accepted the request; local reconciliation is required') from exc
    sync_pending = False
    try:
        await ingest(url, engine=engine, tracked_order_ids={external_id})
    except Exception as exc:
        sync_pending = True
        await asyncio.to_thread(record_system_event, engine, 'sync.error', type(exc).__name__)
    return await asyncio.to_thread(result, engine, external_id, action_id, sync_pending)
