"""Original live evidence and the model's offline export contract."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from schema.allocation.allocation_plan import AllocationPlan, AllocationReservation
from schema.dispatch.assignment import Assignment
from schema.dispatch.assignment_reservation import AssignmentReservation
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.dispatch.dispatch_action import DispatchAction
from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.audit.live_audit import LiveBatchAudit, LiveSubmissionAudit
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from schema.terminal.terminal import Terminal
from service.shared.errors import ProductError
from service.audit.audit_source import synthetic_source
from service.orders.expiration import utc
from service.pricing.price_analysis import model_number


def now():
    return datetime.now(timezone.utc)


def json_value(value):
    if isinstance(value, datetime):
        return utc(value).isoformat()
    return str(value)


# Operational tables captured for audit. Shipments are excluded on purpose: the
# board holds thousands of unrelated orders, so a full dump made every audit row
# tens of megabytes and held the SQLite write lock long enough to fail unrelated
# writes. The candidate shipment is captured separately as `shipment`.
SNAPSHOT_MODELS = (
    Terminal, FleetUnit, FleetCapacity, SimulationClock,
    Assignment, AssignmentReservation, AllocationReservation,
)
# Boards can be large; cap the captured rows so one audit row stays small.
SNAPSHOT_ROW_LIMIT = 200


def operational_snapshot(session, config, shipment=None):
    result = {'captured_at_wall': now().isoformat(), 'run_settings': config.model_dump(mode='json')}
    for model in SNAPSHOT_MODELS:
        result[model.__tablename__] = [dict(row) for row in session.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)
            .limit(SNAPSHOT_ROW_LIMIT)).mappings()]
    # Capture only the order under analysis; a whole-table shipment dump is what
    # previously bloated each row past 20 MB.
    if shipment is not None:
        result['shipment'] = dict(session.execute(
            select(Shipment.__table__).where(Shipment.external_id == shipment)
            .limit(1)).mappings().one_or_none() or {})
    return json.loads(json.dumps(result, default=json_value, allow_nan=False))


def begin_capture(engine, prepared):
    with Session(engine) as session, session.begin():
        session.execute(text('BEGIN IMMEDIATE'))
        plan = session.get(AllocationPlan, UUID(prepared['request']['batch_id']))
        if plan is None or plan.status != 'planning':
            raise ProductError(409, 'Batch plan is no longer active; analysis was not sent')
        row = LiveBatchAudit(plan_id=plan.plan_id, run_id=plan.run_id,
            synthetic=synthetic_source(),
            request=prepared['request'], operational_snapshot=prepared['operational_snapshot'],
            requested_at_wall=now())
        session.add(row)
        session.flush()
        return row.audit_id


def finish_capture(engine, audit_id, *, response=None, received_at=None, error=None):
    with Session(engine) as session, session.begin():
        row = session.get(LiveBatchAudit, audit_id)
        if response is not None and row.received_at_wall is None:
            row.received_at_wall, row.http_status = received_at, response.status_code
            if len(response.content) <= 4_194_304:
                row.response_body = response.text
                try:
                    data = response.json()
                    json.dumps(data, allow_nan=False)
                    row.response = data if isinstance(data, dict) else None
                except (ValueError, TypeError):
                    pass
        if error:
            row.error_code = error


def start_submission(session, attempt, shipment, request, checked):
    batch = None
    if attempt.model_snapshot.get('kind') == 'batch':
        identity = attempt.model_snapshot.get('audit_id')
        batch = session.get(LiveBatchAudit, UUID(identity)) if identity else None
        response = attempt.model_snapshot.get('batch_response', {})
        if (batch is None or batch.response is None or batch.received_at_wall is None
                or batch.response.get('analysis_id') != response.get('analysis_id')):
            raise ProductError(409, 'Original live batch capture is missing; reanalyze before submitting')
        from schema.autopilot.model_batch import ModelBatchResponse
        original_response = ModelBatchResponse.model_validate_json(json.dumps(batch.response)).model_dump(mode='json')
        if original_response != response or batch.request != attempt.model_snapshot.get('batch_request'):
            raise ProductError(409, 'Submission approval differs from the original captured batch; reanalyze')
    elif attempt.model_snapshot.get('request', {}).get('mode') == 'live':
        from schema.audit.prediction_observation import PredictionObservation
        identity = attempt.model_snapshot.get('observation_id')
        original = session.get(PredictionObservation, UUID(identity)) if identity else None
        if (original is None or original.error_code or original.finished_at_wall is None
                or original.request != attempt.model_snapshot['request']
                or (original.result or {}).get('response') != attempt.model_snapshot.get('response')):
            raise ProductError(409, 'Original model observation is missing or changed; reanalyze before submitting')
    amount = getattr(request, 'bid_rate', None) if shipment.order_type == 'open_bid' else checked.economics.payout
    if amount is None:
        amount = attempt.model_snapshot.get('response', {}).get('candidate_bid_dollars')
    unit_id = attempt.candidate_unit_id or checked.candidate_unit_id
    if amount is None or unit_id is None:
        return None  # No quote/unit exists; this is not a submission attempt.
    identity = uuid4()
    row = LiveSubmissionAudit(submission_id=identity, batch_audit_id=batch.audit_id if batch else None,
        attempt_id=attempt.attempt_id, decision_id=attempt.model_snapshot.get('response', {}).get('decision_id'),
        external_id=shipment.external_id, unit_id=unit_id,
        idempotency_key=str(identity), amount_usd=Decimal(str(amount)), attempted_at_wall=now(), status='unknown', result={})
    session.add(row)
    session.flush()
    return row


def submission_record(session, action_id):
    return session.scalar(select(LiveSubmissionAudit).where(LiveSubmissionAudit.action_id == action_id))


def rate_limit_ready(session, config, at):
    from service.simulation.simulation_time import current_sim
    previous = session.scalar(select(LiveSubmissionAudit).where(
        LiveSubmissionAudit.submitted_at_wall.is_not(None)).order_by(LiveSubmissionAudit.submitted_at_wall.desc()).limit(1))
    if previous is None:
        return True
    sim = current_sim(session, at)
    if previous.submitted_at_sim is None:
        # Legacy handoffs retain their original wall evidence; never backfill a
        # fictitious simulated timestamp.
        return (at - utc(previous.submitted_at_wall)).total_seconds() >= config.min_action_interval_seconds
    if sim < utc(previous.submitted_at_sim):
        return (at - utc(previous.submitted_at_wall)).total_seconds() >= config.min_action_interval_seconds
    return (sim - utc(previous.submitted_at_sim)).total_seconds() >= config.min_action_interval_seconds


def update_submission_result(session, action, response=None):
    row = submission_record(session, action.action_id)
    if row is None:
        return
    row.status = {'succeeded': 'accepted', 'failed': 'rejected'}.get(action.request_status, 'unknown')
    details = {'board_http_status': action.board_http_status, 'message': action.board_message,
               'request_status': action.request_status, 'recorded_at_wall': now().isoformat()}
    if response is not None:
        details['response'] = response
        details['sent_to_board'] = True
    row.result = row.result | details


def observe_outcome(session, shipment, observed_at):
    records = session.scalars(select(LiveSubmissionAudit).where(
        LiveSubmissionAudit.external_id == shipment.external_id,
        LiveSubmissionAudit.submitted_at_wall.is_not(None)))
    for row in records:
        # Preserve uncertainty and board resets; a missing order is not a loss.
        previous = row.result.get('outcome', {})
        status = shipment.status
        award = 'awarded' if status in ('accepted', 'in_transit', 'completed') else 'lost' if status == 'bid_lost' else 'unknown'
        state = 'completed' if status == 'completed' else 'cancelled' if status in ('cancelled', 'declined', 'expired') else 'in_progress' if status in ('accepted', 'in_transit') else 'unknown'
        completion_observed = previous.get('completion_observed_at')
        if state == 'completed' and completion_observed is None:
            completion_observed = utc(observed_at).isoformat()
        outcome = {'observed_at': utc(observed_at).isoformat(),
            'award_status': award, 'shipment_status': state, 'completed_at': None,
            'completion_observed_at': completion_observed,
            'board_status': status, 'actual_board_unit_id': shipment.board_assigned_unit_id}
        history = row.result.get('outcome_history', [])
        if not history or any(history[-1].get(key) != outcome.get(key) for key in (
                'award_status', 'shipment_status', 'board_status', 'actual_board_unit_id')):
            history = history + [outcome]
        row.result = row.result | {'outcome': outcome, 'outcome_history': history}


def export_live_audit(engine, run_id=None, *, synthetic=False):
    """Use original captures, never today's model, policies or reconstructed times."""
    from loadpilot_model.evaluation.live_audit import LiveAuditExport
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        if run_id is not None:
            from schema.autopilot.autopilot_run import AutopilotRun
            if session.get(AutopilotRun, run_id) is None:
                raise ProductError(404, 'Autopilot run not found')
        query = select(LiveBatchAudit).where(LiveBatchAudit.synthetic == synthetic)
        if run_id is not None:
            query = query.where(LiveBatchAudit.run_id == run_id)
        captures = list(session.scalars(query.order_by(LiveBatchAudit.requested_at_wall, LiveBatchAudit.audit_id)))
        decisions, retained, skipped = [], {}, []
        from loadpilot_model.evaluation.live_audit import DecisionRecord
        for row in captures:
            if row.http_status != 200 or row.response is None or row.received_at_wall is None:
                skipped.append({'audit_id': str(row.audit_id), 'reason': row.error_code or 'NO_BATCH_RESPONSE'})
                continue
            record = {'request': row.request, 'response': row.response, 'received_at': utc(row.received_at_wall).isoformat()}
            try:
                DecisionRecord.model_validate_json(json.dumps(record, allow_nan=False))
            except ValueError:
                skipped.append({'audit_id': str(row.audit_id), 'reason': 'MODEL_RESPONSE_SCHEMA_INVALID'})
                continue
            decisions.append(record)
            retained[row.audit_id] = row
        submissions, latest_outcomes = [], {}
        query = select(LiveSubmissionAudit).where(LiveSubmissionAudit.batch_audit_id.in_(retained)).order_by(
            LiveSubmissionAudit.attempted_at_wall, LiveSubmissionAudit.submission_id)
        for row in session.scalars(query):
            action = session.get(DispatchAction, row.action_id) if row.action_id else None
            status = ({'succeeded': 'accepted', 'failed': 'rejected'}.get(action.request_status, 'unknown')
                      if action is not None and row.submitted_at_wall is not None else row.status)
            item = {'submission_id': str(row.submission_id), 'decision_id': row.decision_id,
                'shipment_id': row.external_id, 'unit_id': str(row.unit_id), 'idempotency_key': row.idempotency_key,
                'submitted_at': utc(row.submitted_at_wall or row.attempted_at_wall).isoformat(),
                'amount_dollars': model_number(row.amount_usd), 'status': status,
                'revalidated_at': utc(row.revalidated_at_wall).isoformat() if row.revalidated_at_wall else None,
                **{key: getattr(row, key) for key in ('authorization_passed', 'operational_checks_passed',
                                                     'atomic_reservation_acquired', 'rate_limit_passed')}}
            submissions.append(item)
            observation = row.result.get('outcome')
            if observation is not None:
                shipment = session.scalar(select(Shipment).where(Shipment.external_id == row.external_id))
                actual = shipment.financial_outcome or {}
                amounts = actual.get('actuals', {})
                outcome = {'submission_id': str(row.submission_id), 'shipment_id': row.external_id,
                    **{key: observation.get(key) for key in ('observed_at', 'award_status', 'shipment_status', 'completed_at')},
                    'actual_cost_scope_complete': actual.get('status') == 'reviewed' and amounts.get('actual_cost_scope_complete') is True}
                for source, target in [('payout_usd', 'actual_payout_dollars'), ('loaded_cost_usd', 'actual_loaded_cost_dollars'),
                                       ('deadhead_cost_usd', 'actual_deadhead_cost_dollars'), ('other_cost_usd', 'actual_other_cost_dollars')]:
                    outcome[target] = model_number(Decimal(amounts[source])) if source in amounts else None
                if amounts.get('completed_at_wall'):
                    outcome['completed_at'] = amounts['completed_at_wall']
                # Financial evidence may have arrived after the last board sync.
                stamps = [datetime.fromisoformat(observation['observed_at']),
                          *[datetime.fromisoformat(actual[key]) for key in ('recorded_at_wall', 'reviewed_at_wall') if actual.get(key)]]
                outcome['observed_at'] = max(map(utc, stamps)).isoformat()
                if row.external_id not in latest_outcomes or status == 'accepted':
                    latest_outcomes[row.external_id] = outcome
        payload = {'schema_version': 1, 'currency': 'USD', 'synthetic': synthetic,
            'exported_at': now().isoformat(), 'decisions': decisions, 'submissions': submissions,
            'outcomes': list(latest_outcomes.values())}
        # Validate shape/duplicates, but preserve original request/response JSON.
        LiveAuditExport.model_validate_json(json.dumps(payload, allow_nan=False))
        coverage = {'captured_batch_count': len(captures), 'exported_batch_count': len(decisions),
            'skipped_captures': skipped, 'submission_count': len(submissions), 'outcome_count': len(latest_outcomes),
            'has_genuine_observations': not synthetic and bool(decisions),
            'note': 'No old snapshots, receive times, safeguard flags or actual costs were reconstructed.'}
        coverage.update(
            accepted_submission_count=sum(row['status'] == 'accepted' for row in submissions),
            unknown_submission_count=sum(row['status'] == 'unknown' for row in submissions),
            completed_without_full_actuals=sum(row['shipment_status'] == 'completed' and not row['actual_cost_scope_complete']
                                               for row in latest_outcomes.values()),
            backend_rejected_captures=[{'audit_id': str(row.audit_id), 'error': row.error_code}
                                       for row in captures if row.error_code])
        captured_plans = set(session.scalars(select(LiveBatchAudit.plan_id)))
        old_plans = select(AllocationPlan)
        if run_id is not None:
            old_plans = old_plans.where(AllocationPlan.run_id == run_id)
        coverage['legacy_batches_without_original_capture'] = sum(
            bool(row.result.get('batch_analysis', {}).get('response')) and row.plan_id not in captured_plans
            for row in session.scalars(old_plans))
        return payload, coverage
