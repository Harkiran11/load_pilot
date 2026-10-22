"""Export captured attempts using the model's published monitoring contract."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re

from dotenv import dotenv_values
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from loadpilot_model.evaluation.monitoring import MonitoringExport, monitor

from schema.audit.live_audit import LiveBatchAudit, LiveSubmissionAudit
from schema.audit.prediction_observation import PredictionObservation
from service.shared.database import PROJECT_ROOT
from service.orders.expiration import utc
from service.audit.prediction_capture import FEATURES


def error_code(value):
    if not value:
        return None
    return value if re.fullmatch(r'[A-Z0-9_]{1,80}', value) else 'MODEL_ANALYSIS_FAILED'


def export_observations(engine, *, synthetic=False):
    records, skipped = [], []
    with Session(engine) as session:
        session.execute(text('BEGIN'))
        for row in session.scalars(select(PredictionObservation).where(
                PredictionObservation.synthetic == synthetic).order_by(PredictionObservation.started_at_wall)):
            context, result = row.context, row.result or {}
            prepared = result or context.get('prepared', {})
            if context.get('mode') == 'autopilot':
                reply = result.get('response', {}) if not row.error_code else {}
                advice = reply.get('advisory') or {}
                costs = row.request.get('backend_context') or {}
                submission = session.scalar(select(LiveSubmissionAudit).where(
                    LiveSubmissionAudit.decision_id == reply.get('decision_id'),
                    LiveSubmissionAudit.submitted_at_wall.is_not(None))) if reply.get('decision_id') else None
                outcome = submission.result.get('outcome', {}) if submission else {}
                award = outcome.get('award_status', 'unknown')
                records.append(dict(event_id=str(row.observation_id), shipment_id=row.external_id,
                    observed_at=utc(row.started_at_wall).isoformat(), order_type=context['order_type'], mode='autopilot',
                    features=context['features'], pricing_source=advice.get('pricing_source', 'unavailable'),
                    model_version=advice.get('model_version'), suggested_bid_dollars=advice.get('predicted_bid_dollars'),
                    final_bid_dollars=float(submission.amount_usd) if submission else None,
                    bid_observed_at=utc(submission.submitted_at_wall).isoformat() if submission else None,
                    estimated_cost_dollars=(costs['loaded_cost_dollars'] + costs['deadhead_cost_dollars']
                        if costs.get('loaded_cost_dollars') is not None and costs.get('deadhead_cost_dollars') is not None else None),
                    estimated_margin_percent=reply.get('estimated_margin_percent'), award_status=award,
                    award_observed_at=outcome.get('observed_at') if award != 'unknown' else None,
                    error_code=error_code(row.error_code or ('ANALYSIS_INCOMPLETE' if row.finished_at_wall is None else None))))
                continue
            request = prepared.get('model_request') or {}
            features = request.get('shipment') or context.get('features', {})
            if context.get('order_type') not in ('open_bid', 'fixed_rate'):
                skipped.append({'event_id': str(row.observation_id), 'reason': 'ORDER_TYPE_UNKNOWN'})
                continue
            advice = (result.get('model_response') or {}).get('advisory', {})
            economics = prepared.get('deterministic_evaluation', {}).get('economics', {})
            outcomes = [item['outcome'] for item in row.executions if item.get('outcome')]
            latest = max(outcomes, key=lambda item: item['observed_at_wall']) if outcomes else {}
            award = latest.get('award_status', 'unknown')
            records.append(dict(event_id=str(row.observation_id), shipment_id=row.external_id,
                observed_at=utc(row.started_at_wall).isoformat(), order_type=context['order_type'], mode='manual',
                features={name: features.get(name) for name in FEATURES},
                pricing_source=result.get('advisory_status', 'unavailable'), model_version=advice.get('model_version'),
                suggested_bid_dollars=advice.get('predicted_bid_dollars') if not row.error_code else None,
                # This is the manual price entered for analysis, not a claim of booking.
                final_bid_dollars=float(row.request['bid_rate']), bid_observed_at=utc(row.started_at_wall).isoformat(),
                estimated_cost_dollars=float(economics['total_cost']) if economics.get('total_cost') is not None else None,
                estimated_margin_percent=float(economics['margin_pct']) if economics.get('margin_pct') is not None else None,
                award_status=award, award_observed_at=latest.get('observed_at_wall') if award != 'unknown' else None,
                error_code=error_code(row.error_code or ('ANALYSIS_INCOMPLETE' if row.finished_at_wall is None else None))))
        captures = list(session.scalars(select(LiveBatchAudit).where(LiveBatchAudit.synthetic == synthetic)))
        for row in captures:
            evaluations = {}
            if row.response and not row.error_code:
                from schema.autopilot.model_batch import ModelBatchResponse
                try:
                    parsed = ModelBatchResponse.model_validate_json(json.dumps(row.response))
                    evaluations = {(item.evaluation.shipment_id, item.evaluation.unit_id):
                                   item.evaluation.model_dump(mode='json') for item in parsed.evaluations}
                except ValueError:
                    pass
            submissions = list(session.scalars(select(LiveSubmissionAudit).where(
                LiveSubmissionAudit.batch_audit_id == row.audit_id).order_by(LiveSubmissionAudit.attempted_at_wall)))
            for index, item in enumerate(row.request.get('orders', [])):
                pair = evaluations.get((item['shipment_id'], item['unit_id']), {})
                advice = pair.get('advisory') or {}
                context = item.get('backend_context') or {}
                # Only explicit native handoffs are observations of submitted prices.
                sent = next((submission for submission in submissions if submission.external_id == item['shipment_id']
                    and str(submission.unit_id) == item['unit_id'] and submission.submitted_at_wall is not None
                    and utc(submission.submitted_at_wall) >= utc(row.requested_at_wall)), None)
                outcome = sent.result.get('outcome', {}) if sent else {}
                award = outcome.get('award_status', 'unknown')
                source = advice.get('pricing_source', 'unavailable')
                records.append(dict(event_id=f'{row.audit_id}:{index}', shipment_id=item['shipment_id'],
                    observed_at=utc(row.requested_at_wall).isoformat(), order_type=item['shipment']['order_type'], mode='autopilot',
                    features={name: item['shipment'].get(name) for name in FEATURES}, pricing_source=source,
                    model_version=advice.get('model_version'),
                    suggested_bid_dollars=advice.get('predicted_bid_dollars') if source != 'unavailable' else None,
                    final_bid_dollars=float(sent.amount_usd) if sent else None,
                    bid_observed_at=utc(sent.submitted_at_wall).isoformat() if sent else None,
                    estimated_cost_dollars=(context['loaded_cost_dollars'] + context['deadhead_cost_dollars']
                        if context.get('loaded_cost_dollars') is not None and context.get('deadhead_cost_dollars') is not None else None),
                    estimated_margin_percent=pair.get('estimated_margin_percent'), award_status=award,
                    award_observed_at=outcome.get('observed_at') if award != 'unknown' else None,
                    error_code=error_code(row.error_code or ('MODEL_RESPONSE_UNAVAILABLE' if not pair else None))))
        payload = MonitoringExport.model_validate_json(json.dumps(dict(synthetic=synthetic,
            exported_at=datetime.now(timezone.utc).isoformat(), records=records))).model_dump(mode='json')
        coverage = dict(record_count=len(records), captured_batch_count=len(captures), skipped=skipped,
            has_genuine_observations=not synthetic and bool(records),
            note='Manual final prices are entered prices. Batch final prices require a recorded handoff; unknown outcomes remain unknown.')
        return payload, coverage


def monitoring_settings():
    config = dotenv_values(PROJECT_ROOT / '.env')
    def setting(key):
        return os.environ.get(key, config.get(key))
    bundle = setting('MODEL_MONITORING_BUNDLE')
    if not bundle:
        raise ValueError('Configure MODEL_MONITORING_BUNDLE with the frozen model bundle directory')
    thresholds = {}
    for key, env in (('min_records', 'MONITORING_MIN_RECORDS'), ('max_error_rate', 'MONITORING_MAX_ERROR_RATE'),
                     ('max_invalid_feature_rate', 'MONITORING_MAX_INVALID_FEATURE_RATE'),
                     ('max_unseen_lane_rate', 'MONITORING_MAX_UNSEEN_LANE_RATE')):
        raw = setting(env)
        if raw is None or raw == '':
            continue
        value = int(raw) if key == 'min_records' else float(raw)
        if not math.isfinite(value) or (value < 1 if key == 'min_records' else not 0 <= value <= 1):
            raise ValueError(f'{env} is invalid')
        thresholds[key] = value
    directory = Path(bundle)
    return directory if directory.is_absolute() else PROJECT_ROOT / directory, thresholds


def build_report(payload, coverage, bundle, thresholds):
    report = monitor(MonitoringExport.model_validate_json(json.dumps(payload)), bundle)
    count = report['record_count']
    measurements = {'min_records': count,
        'max_error_rate': sum(bool(row.get('error_code')) for row in payload['records']) / count if count else None,
        'max_invalid_feature_rate': report['invalid_feature_record_count'] / count if count else None,
        'max_unseen_lane_rate': report['unseen_category_counts'].get('lane_id', 0) / count if count else None}
    alerts = [{'code': key.upper(), 'observed': measurements[key], 'threshold': value}
        for key, value in thresholds.items() if measurements.get(key) is not None
        and (measurements[key] < value if key == 'min_records' else measurements[key] > value)]
    return dict(report=report, coverage=coverage, thresholds=thresholds, measurements=measurements, alerts=alerts,
        status='insufficient_evidence' if not count else 'alerts' if alerts else 'unconfigured' if not thresholds else 'within_configured_limits',
        automation_approved=False,
        note='Operational alerts use configured thresholds; this report neither validates accuracy nor enables automation.')
