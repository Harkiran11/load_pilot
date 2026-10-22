"""Persist reported actuals on shipments and review comparable observed cohorts."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select, text

from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.dispatch.dispatch_action import DispatchAction
from schema.analytics.financials import (
    ObservedCohortView, ObservedComparisonView, ShipmentFinancialRequest, ShipmentFinancialView,
)
from schema.analytics.observed_cohort import ObservedCohort, ObservedComparison
from schema.analytics.recommendation_comparison import RecommendationComparisonRequest
from schema.orders.shipment import Shipment
from service.audit.activity import add_activity
from service.autopilot.autopilot_guards import wall_now
from service.pricing.economics import money
from service.shared.errors import ProductError
from service.orders.expiration import utc
from service.analytics.recommendation_comparison import compare_recommendations


@contextmanager
def write(session):
    with session.begin():
        if session.get_bind().dialect.name != 'sqlite':
            raise ProductError(503, 'Atomic financial review currently requires SQLite')
        session.execute(text('BEGIN IMMEDIATE'))
        yield


def shipment_record(session, external_id):
    row = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
    if row is None:
        raise ProductError(404, 'Shipment not found')
    return row


def financial_view(row):
    outcome = row.financial_outcome or {}
    actuals = ShipmentFinancialRequest.model_validate(outcome['actuals']) if outcome else None
    total = actuals.loaded_cost_usd + actuals.deadhead_cost_usd + actuals.other_cost_usd if actuals else None
    profit = actuals.payout_usd - total if actuals else None
    return ShipmentFinancialView(
        external_id=row.external_id, status=outcome.get('status', 'missing'), actuals=actuals,
        total_cost_usd=money(total) if total is not None else None,
        realized_profit_usd=money(profit) if profit is not None and actuals.actual_cost_scope_complete else None,
        margin_pct=money(profit / actuals.payout_usd * 100) if actuals and actuals.payout_usd and actuals.actual_cost_scope_complete else None,
        **{key: outcome.get(key) for key in ('booking_evaluation_id', 'model_analysis_id', 'recorded_at_wall',
                                           'reviewed_at_wall', 'reviewer', 'review_notes')})


def get_financials(session, external_id):
    return financial_view(shipment_record(session, external_id))


def record_financials(session, external_id, request):
    with write(session):
        row = shipment_record(session, external_id)
        if row.status != 'completed' or row.board_assigned_unit_id is None:
            raise ProductError(409, 'Record final actuals only for a completed shipment with a confirmed assigned unit')
        if request.completed_at_wall is not None and request.completed_at_wall > wall_now():
            raise ProductError(422, 'Actual wall-clock completion cannot be in the future; do not supply simulated delivery time')
        data = request.model_dump(mode='json')
        original = ShipmentFinancialRequest.model_validate(row.financial_outcome['actuals']).model_dump(mode='json') if row.financial_outcome else None
        if original == data:
            return financial_view(row)
        if row.financial_outcome and row.financial_outcome.get('status') == 'reviewed':
            raise ProductError(409, 'Reviewed actuals are immutable; retain the evidence and resolve corrections explicitly')
        if request.cohort_id:
            cohort = session.get(ObservedCohort, request.cohort_id)
            if cohort is None:
                raise ProductError(404, 'Observed cohort not found')
            if row.delivery_at is None or not utc(cohort.period_start_sim) <= utc(row.delivery_at) < utc(cohort.period_end_sim):
                raise ProductError(422, 'Shipment delivery must fall within the cohort simulated period')
        action = session.scalar(select(DispatchAction).where(DispatchAction.shipment_id == row.shipment_id,
            DispatchAction.request_status == 'succeeded', DispatchAction.action_type.in_(('accept', 'bid'))).order_by(
            DispatchAction.requested_at.desc(), DispatchAction.action_id.desc()))
        attempt = session.scalar(select(AutopilotAttempt).where(AutopilotAttempt.action_id == action.action_id)) if action else None
        snapshot = attempt.model_snapshot if attempt else {}
        row.observed_cohort_id = request.cohort_id
        row.financial_outcome = {
            'status': 'reported', 'actuals': data, 'recorded_at_wall': wall_now().isoformat(),
            'booking_evaluation_id': str(action.evaluation_id) if action and action.evaluation_id else None,
            'model_analysis_id': snapshot.get('batch_response', {}).get('analysis_id') or snapshot.get('response', {}).get('decision_id'),
            'assigned_board_unit_id': row.board_assigned_unit_id,
        }
        totals = financial_view(row)
        row.financial_outcome = row.financial_outcome | totals.model_dump(mode='json', include={
            'total_cost_usd', 'realized_profit_usd', 'margin_pct'})
        add_activity(session, 'shipment.actuals_reported', 'Reported actual shipment financial outcome',
                     shipment_id=row.shipment_id, details={'source': 'reported_actuals', 'reported_by': request.reported_by})
        return totals


def review_financials(session, external_id, request):
    with write(session):
        row = shipment_record(session, external_id)
        if not row.financial_outcome:
            raise ProductError(409, 'Report actual amounts and evidence before reviewing this shipment')
        if (row.status != 'completed' or row.board_assigned_unit_id != row.financial_outcome.get('assigned_board_unit_id')):
            raise ProductError(409, 'Shipment lifecycle or assigned unit changed; resolve the evidence before review')
        if row.financial_outcome.get('status') == 'reviewed':
            return financial_view(row)
        row.financial_outcome = row.financial_outcome | {'status': 'reviewed',
            'reviewed_at_wall': wall_now().isoformat(), 'reviewer': request.reviewer, 'review_notes': request.notes}
        add_activity(session, 'shipment.actuals_reviewed', 'Reviewed reported actual financial evidence',
                     shipment_id=row.shipment_id, details={'reviewer': request.reviewer, 'notes': request.notes})
        return financial_view(row)


def create_cohort(session, request):
    with write(session):
        row = ObservedCohort(**request.model_dump(), created_at_wall=wall_now())
        session.add(row)
        session.flush()
        return ObservedCohortView.model_validate(row)


def get_cohort(session, cohort_id):
    row = session.get(ObservedCohort, cohort_id)
    if row is None:
        raise ProductError(404, 'Observed cohort not found')
    return row


def cohort_evidence(session, cohort):
    shipments = list(session.scalars(select(Shipment).where(Shipment.observed_cohort_id == cohort.cohort_id).order_by(Shipment.external_id)))
    if not shipments:
        raise ProductError(409, 'The cohort has no reported actual shipment outcomes')
    views = [financial_view(row) for row in shipments]
    if any(view.status != 'reviewed' for view in views):
        raise ProductError(409, 'Review every shipment actual outcome before comparing the cohort')
    if any(not view.actuals.actual_cost_scope_complete for view in views):
        raise ProductError(409, 'Explicitly confirm complete actual cost scope for every observed shipment')
    if any(row.status != 'completed' or row.board_assigned_unit_id != row.financial_outcome.get('assigned_board_unit_id') for row in shipments):
        raise ProductError(409, 'Every observed shipment must remain completed')
    occupied = sum((view.actuals.occupied_hours for view in views), Decimal(0))
    if occupied > cohort.available_unit_hours:
        raise ProductError(422, 'Observed occupied hours exceed measured fleet exposure')
    metrics = {'evidence_reference': cohort.exposure_evidence, 'completed_loads': len(views),
        'realized_profit': sum((view.realized_profit_usd for view in views), Decimal(0)),
        'empty_miles': sum((view.actuals.empty_miles for view in views), Decimal(0)),
        'occupied_unit_hours': occupied, 'available_unit_hours': cohort.available_unit_hours}
    evidence = {'cohort': ObservedCohortView.model_validate(cohort).model_dump(mode='json'),
                'shipments': [view.model_dump(mode='json') for view in views]}
    return metrics, evidence


def comparison_metrics(session, baseline_id, candidate_id, notes):
    if baseline_id == candidate_id:
        raise ProductError(422, 'Baseline and candidate cohorts must be different')
    baseline, candidate = get_cohort(session, baseline_id), get_cohort(session, candidate_id)
    if baseline.strategy != 'baseline' or candidate.strategy != 'candidate':
        raise ProductError(422, 'Select a baseline cohort and a candidate cohort')
    if (utc(baseline.period_end_sim) - utc(baseline.period_start_sim)
            != utc(candidate.period_end_sim) - utc(candidate.period_start_sim)
            or baseline.available_unit_hours != candidate.available_unit_hours
            or baseline.fleet_context != candidate.fleet_context):
        raise ProductError(422, 'Cohorts need equal simulated duration, measured fleet exposure, and matching fleet context')
    base_metrics, base_evidence = cohort_evidence(session, baseline)
    candidate_metrics, candidate_evidence = cohort_evidence(session, candidate)
    request = RecommendationComparisonRequest(comparable_period_description=notes,
                                              baseline=base_metrics, candidate=candidate_metrics)
    result = compare_recommendations(request).model_dump(mode='json')
    result.pop('automation_ready', None)
    result.pop('explanation', None)
    evidence = {'baseline': base_evidence, 'candidate': candidate_evidence}
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return result | {'source': 'reviewed_reported_actuals', 'evidence': evidence}, fingerprint


def comparison_view(session, row):
    result = ObservedComparisonView.model_validate(row)
    try:
        _, current = comparison_metrics(session, row.baseline_id, row.candidate_id, row.comparability_notes)
        unchanged = current == row.evidence_fingerprint
    except ProductError:
        unchanged = False
    result.automation_ready = row.status == 'reviewed' and unchanged
    if not unchanged:
        result.status = 'evidence_changed'
    return result


def create_comparison(session, request):
    with write(session):
        metrics, fingerprint = comparison_metrics(session, request.baseline_id, request.candidate_id, request.comparability_notes)
        row = ObservedComparison(**request.model_dump(), metrics=metrics, evidence_fingerprint=fingerprint,
                                 status='pending_review', created_at_wall=wall_now())
        session.add(row)
        session.flush()
        return comparison_view(session, row)


def get_comparison(session, comparison_id):
    row = session.get(ObservedComparison, comparison_id)
    if row is None:
        raise ProductError(404, 'Observed comparison not found')
    return row


def review_comparison(session, comparison_id, request):
    with write(session):
        row = get_comparison(session, comparison_id)
        if comparison_view(session, row).status == 'evidence_changed':
            raise ProductError(409, 'Cohort evidence changed; create a fresh comparison before review')
        if row.status != 'reviewed':
            row.status, row.reviewed_at_wall = 'reviewed', wall_now()
            row.reviewer, row.review_notes = request.reviewer, request.notes
            add_activity(session, 'analytics.comparison_reviewed', 'Observed cohort comparison reviewed',
                         details={'comparison_id': str(row.comparison_id), 'reviewer': request.reviewer})
        return comparison_view(session, row)
