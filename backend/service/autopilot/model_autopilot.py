"""Bind model BID/SKIP/REVIEW decisions to backend fleet/economics snapshots."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
import re

import httpx
from dotenv import dotenv_values

from schema.fleet.fleet_capacity import FleetCapacity
from schema.fleet.fleet_unit import FleetUnit
from schema.autopilot.model_autopilot import (
    DecisionChecks, DecisionClock, DecisionContext, DecisionControl, DecisionInputAges, DecisionPolicy,
    DecisionPosition, DecisionShipment, ModelAutopilotRequest, ModelAutopilotResponse,
    ModelLiveAutopilotRequest, ModelLiveAutopilotResponse,
)
from schema.orders.shipment import Shipment
from schema.simulation.simulation_clock import SimulationClock
from service.autopilot.autopilot_guards import live_enabled, wall_now
from service.orders.expiration import utc
from service.shared.database import PROJECT_ROOT
from service.pricing.model_lane import model_shipment_features
from service.pricing.price_analysis import analysis_settings, model_number

MODEL_PATH = 'api/v1/autopilot/evaluate'
LIVE_MODEL_PATH = 'api/v1/autopilot/live/evaluate'


def live_model_token():
    values = dotenv_values(PROJECT_ROOT / '.env')
    token = os.environ.get('MODEL_AUTOPILOT_LIVE_TOKEN', values.get('MODEL_AUTOPILOT_LIVE_TOKEN', '')) or ''
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError('MODEL_LIVE_AUTH_TOKEN_MISSING_OR_INVALID')
    return token


def context_fingerprint(request):
    def serialize(value):
        if isinstance(value, datetime):
            return utc(value).isoformat()
        raise TypeError('Unsupported model context value')
    return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True, default=serialize,
                                     allow_nan=False).encode()).hexdigest()


def oldest(*stamps):
    return min(map(utc, stamps)) if stamps and all(stamp is not None for stamp in stamps) else None


def prepare_decision(session, shipment, checked, config, snapshot_version, *, run=None, computed_at=None, capture_snapshot=False):
    if run is not None and run.mode != 'live':
        raise ValueError('MODEL_LIVE_MODE_REQUIRED')
    features, lane = model_shipment_features(session, shipment)
    unit = session.get(FleetUnit, checked.candidate_unit_id) if checked.candidate_unit_id else None
    if unit is None:
        raise ValueError('MODEL_SELECTED_UNIT_MISSING')
    clock = session.get(SimulationClock, 1)
    capacity = session.get(FleetCapacity, unit.home_terminal_id)
    now = computed_at or wall_now()
    _, timeout, deviation = analysis_settings()
    source_stamps = [shipment.last_synced_at, unit.last_synced_at,
                     capacity.checked_at if capacity else None, clock.last_synced_at if clock else None]
    projection = checked.projection
    if projection:
        for delivery in projection.deliveries:
            source = session.get(Shipment, delivery.shipment_id)
            source_stamps.append(source.last_synced_at if source else None)
    observed = oldest(*source_stamps)
    wall_expiry = None
    if clock and shipment.expires_at and clock.simulation_speed > 0:
        # Keep the original simulation deadline in audit metadata. The model
        # server compares wall UTC, so convert using the clock's actual epoch.
        simulated_seconds = Decimal(str((utc(shipment.expires_at) - utc(clock.sim_time_utc)).total_seconds()))
        wall_expiry = utc(clock.last_synced_at) + timedelta(seconds=float(simulated_seconds / clock.simulation_speed))
    global_policy = checked.policy
    profit = max(config.min_expected_profit_usd, global_policy.min_expected_margin_usd if global_policy.enforce_margin else 0)
    margin = max(config.min_expected_margin_pct, global_policy.min_expected_margin_pct if global_policy.enforce_margin else 0)
    age = float(min(config.max_input_age_seconds, global_policy.max_fleet_snapshot_age_seconds))
    clock_input = None
    if clock is not None and clock.last_synced_at is not None and clock.sim_time_utc is not None:
        observed_at = utc(clock.last_synced_at)
        speed = float(clock.simulation_speed)
        clock_input = DecisionClock(
            clock_id=str(clock.clock_id),
            revision=observed_at.strftime('%Y%m%dT%H%M%S%f'),
            simulated_at=utc(clock.sim_time_utc),
            observed_at=observed_at,
            speed=speed,
            max_snapshot_age_seconds=max(1.0, age),
            max_simulated_input_age_seconds=max(1.0, age) * max(1.0, speed),
        )
    policy_values = dict(
        currency='USD', min_profit_dollars=model_number(profit), min_margin_percent=model_number(margin),
        # This cap is unused for fixed-rate offers; keep the required model
        # policy field positive when only fixed-rate trading is enabled.
        max_bid_dollars=model_number(config.max_bid_rate or Decimal('1')),
        max_deadhead_miles=model_number(min(config.max_deadhead_miles, global_policy.max_deadhead_miles)),
        max_input_age_seconds=DecisionInputAges(unit_position=age, unit_availability=age, costs=age, order_status=age),
        max_decision_age_seconds=float(config.max_decision_age_seconds),
        prediction_timeout_seconds=max(.001, timeout - min(.5, timeout / 2)),
        max_reference_deviation_percent=deviation,
        fallback_mode='baseline' if config.allow_baseline else 'review',
        unseen_category_action='baseline' if config.allow_baseline else 'review',
        baseline_id=config.model_baseline_id, baseline_version=config.model_baseline_version,
        min_lane_samples=config.model_min_lane_samples,
    )
    policy_hash = hashlib.sha256(json.dumps(
        {**policy_values, 'max_input_age_seconds': policy_values['max_input_age_seconds'].model_dump()},
        sort_keys=True).encode()).hexdigest()[:32]
    reasons = set(checked.rejection_reasons)
    known_unit = (unit.last_synced_at is not None and projection is not None
                  and projection.status != 'unknown')
    checks = DecisionChecks(
        unit_available=(projection.status == 'available') if known_unit else None,
        commitments_clear=checked.checks['schedule_passed'] if known_unit else None,
        capacity_compatible=checked.checks['capacity_passed'] if (
            capacity and capacity.board_total_units is not None and 'terminal_snapshot_stale' not in reasons) else None,
        equipment_compatible=checked.checks['weight_passed'] if shipment.weight_lbs is not None else None,
        location_verified=checked.checks['location_passed'] if (
            projection and projection.latitude is not None and projection.longitude is not None
            and 'unit_snapshot_stale_or_unknown' not in reasons) else None,
        timing_feasible=checked.checks['timing_passed'] if checked.occupied_window and checked.earliest_pickup_at else None,
        order_open=shipment.status == 'open' and shipment.board_missing_at is None and shipment.board_retired_at is None,
    )
    position = DecisionPosition(latitude=float(projection.latitude), longitude=float(projection.longitude), computed_at=now) if (
        projection and projection.latitude is not None and projection.longitude is not None) else None
    request = ModelLiveAutopilotRequest(
        shipment_id=shipment.external_id, unit_id=str(unit.unit_id), shipment=DecisionShipment(**features),
        backend_context=DecisionContext(shipment_id=shipment.external_id, unit_id=str(unit.unit_id),
            snapshot_version=snapshot_version, projected_position=position,
            loaded_cost_dollars=model_number(checked.economics.loaded_cost),
            deadhead_cost_dollars=model_number(checked.economics.projected_deadhead_cost),
            deadhead_miles=float(checked.deadhead_miles) if checked.deadhead_miles is not None else None,
            costs_observed_at=observed, unit_observed_at=oldest(*source_stamps[1:]),
            order_observed_at=utc(shipment.last_synced_at) if shipment.last_synced_at else None,
            # With a simulation clock, both deadlines are domain timestamps;
            # without one the model falls back to wall UTC comparisons.
            order_expires_at=(utc(shipment.expires_at) if clock_input and shipment.expires_at else wall_expiry),
            operational_expires_at=(utc(shipment.expires_at) if clock_input and shipment.expires_at else wall_expiry),
            clock_id=clock_input.clock_id if clock_input else None,
            clock_revision=clock_input.revision if clock_input else None,
            checks=checks),
        policy=DecisionPolicy(policy_version='dispatcher-' + policy_hash, **policy_values),
        control=DecisionControl(autopilot_enabled=run is None or run.status == 'running',
            kill_switch_active=run is not None and (run.status != 'running' or run.mode == 'live' and not live_enabled())),
        clock=clock_input,
    )
    captured = {}
    if capture_snapshot:
        from service.audit.live_audit import operational_snapshot
        captured = {'operational_snapshot': operational_snapshot(session, config, shipment.external_id),
                    'backend_evaluation': checked.model_dump(mode='json')}
    return {**captured, 'request': request.model_dump(mode='json'), 'lane_resolution': lane.model_dump(mode='json'),
            'backend_mode': 'live',
            'simulation_clock_context': {
                'order_expires_at_sim': utc(shipment.expires_at).isoformat() if shipment.expires_at else None,
                'sim_time_utc': utc(clock.sim_time_utc).isoformat() if clock else None,
                'simulation_speed': str(clock.simulation_speed) if clock else None,
                'clock_last_synced_at': utc(clock.last_synced_at).isoformat() if clock else None,
                'order_expires_at_wall': wall_expiry.isoformat() if wall_expiry else None}}


def validate_decision(model, *, fresh=True):
    if model.get('kind') == 'batch':
        from service.autopilot.model_batch import validate_pair
        return validate_pair(model)
    is_live = model['request'].get('mode') == 'live'
    request_type = ModelLiveAutopilotRequest if is_live else ModelAutopilotRequest
    response_type = ModelLiveAutopilotResponse if is_live else ModelAutopilotResponse
    request = request_type.model_validate_json(json.dumps(model['request']))
    response = response_type.model_validate_json(json.dumps(model['response']))
    # Current live-only servers hash the live payload directly. Older approved
    # servers used an internal shadow adapter; retain that binding only for
    # their explicitly identified legacy response format.
    server_authorized = is_live and response.authorization_source == 'server_configuration'
    if is_live and not server_authorized and response.release_approval_id is None:
        raise ValueError('MODEL_LIVE_AUTHORIZATION_MISSING')
    deterministic_request = request if server_authorized else ModelAutopilotRequest.model_validate_json(json.dumps(
        {**model['request'], 'mode': 'shadow'}))
    context, policy = request.backend_context, request.policy
    if (response.shipment_id != request.shipment_id or response.unit_id != request.unit_id
            or response.snapshot_version != context.snapshot_version or response.policy_version != policy.policy_version
            or response.context_fingerprint != context_fingerprint(deterministic_request)):
        raise ValueError('MODEL_CONTEXT_MISMATCH')
    if is_live and response.live_request_fingerprint != context_fingerprint(request):
        raise ValueError('MODEL_LIVE_REQUEST_MISMATCH')
    source = getattr(response.advisory, 'pricing_source', None)
    baseline = getattr(response.advisory, 'baseline_reference', {}) or {}
    if not isinstance(baseline, dict):
        baseline = {}
    approved_baseline = (source == 'baseline' and policy.fallback_mode == 'baseline'
        and policy.baseline_id is not None and policy.baseline_version is not None and policy.min_lane_samples is not None
        and baseline.get('baseline_id') == policy.baseline_id
        and baseline.get('policy_version') == policy.baseline_version
        and baseline.get('min_lane_samples') == policy.min_lane_samples)
    if is_live and response.eligible_for_live_submission and (
            response.decision != 'BID' or response.requires_review_before_live_use or response.live_blockers
            or source != 'model' and not (server_authorized and approved_baseline)):
        raise ValueError('MODEL_INVALID_LIVE_ELIGIBILITY')
    if response.evaluated_at > wall_now() + timedelta(seconds=5):
        raise ValueError('MODEL_DECISION_FROM_FUTURE')
    for field in ('loaded_cost_dollars', 'deadhead_cost_dollars'):
        if getattr(response, field) != getattr(context, field):
            raise ValueError('MODEL_ECONOMICS_MISMATCH')
    total = (Decimal(str(context.loaded_cost_dollars)) + Decimal(str(context.deadhead_cost_dollars))) if (
        context.loaded_cost_dollars is not None and context.deadhead_cost_dollars is not None) else None
    if (Decimal(str(response.total_estimated_cost_dollars)) if response.total_estimated_cost_dollars is not None else None) != total:
        raise ValueError('MODEL_ECONOMICS_MISMATCH')
    if response.decision != 'BID':
        if response.valid_until is not None:
            raise ValueError('MODEL_INVALID_DECISION_LIFETIME')
        return response, None
    if (response.reasons or response.reason_codes != ['ALL_CHECKS_PASSED'] or response.valid_until is None
            or (fresh and response.valid_until <= wall_now()) or response.candidate_bid_dollars is None or response.advisory is None):
        raise ValueError('MODEL_INVALID_BID_DECISION')
    bid = Decimal(str(response.candidate_bid_dollars))
    if bid <= 0 or bid != bid.quantize(Decimal('.01')):
        raise ValueError('MODEL_CANDIDATE_FRACTIONAL_CENTS')
    predicted = getattr(response.advisory, 'predicted_bid_dollars', None)
    if predicted is None or Decimal(str(predicted)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) != bid:
        raise ValueError('MODEL_CANDIDATE_MISMATCH')
    expected_rpm = bid / Decimal(str(request.shipment.distance_miles))
    if response.candidate_bid_rpm is None or abs(Decimal(str(response.candidate_bid_rpm)) - expected_rpm) > max(Decimal('0.00000001'), abs(expected_rpm) * Decimal('1e-9')):
        raise ValueError('MODEL_CANDIDATE_RPM_MISMATCH')
    if total is None or response.estimated_profit_dollars is None or Decimal(str(response.estimated_profit_dollars)) != bid - total:
        raise ValueError('MODEL_ECONOMICS_MISMATCH')
    latest = min(response.evaluated_at + timedelta(seconds=policy.max_decision_age_seconds),
                 context.order_expires_at or response.evaluated_at,
                 *[stamp + timedelta(seconds=getattr(policy.max_input_age_seconds, field)) for field, stamp in (
                     ('unit_position', context.projected_position.computed_at if context.projected_position else None),
                     ('unit_availability', context.unit_observed_at), ('costs', context.costs_observed_at),
                     ('order_status', context.order_observed_at)) if stamp])
    if response.valid_until > latest:
        raise ValueError('MODEL_INVALID_DECISION_LIFETIME')
    return response, bid


async def evaluate_model(prepared, *, audit_engine=None):
    from service.audit import prediction_capture
    identity = await asyncio.to_thread(prediction_capture.begin_autopilot, audit_engine, prepared) if audit_engine is not None else None
    try:
        model, bid = await _evaluate_model(prepared, audit_engine=audit_engine, observation_id=identity)
        if identity is not None:
            model['observation_id'] = str(identity)
            await asyncio.shield(asyncio.to_thread(prediction_capture.finish_autopilot, audit_engine, identity, model))
        return model, bid
    except BaseException as exc:
        if identity is not None:
            await asyncio.shield(asyncio.to_thread(prediction_capture.finish_autopilot, audit_engine, identity,
                {**prepared, 'error': 'MODEL_REQUEST_CANCELLED' if isinstance(exc, asyncio.CancelledError)
                 else 'MODEL_CAPTURE_FAILED', 'decision': 'REVIEW'}))
        raise


async def _evaluate_model(prepared, *, audit_engine=None, observation_id=None):
    from service.autopilot import autopilot
    model = dict(prepared)
    try:
        url, timeout, _ = autopilot.analysis_settings()
        is_live = model['request'].get('mode') == 'live'
        headers = {'Authorization': 'Bearer ' + live_model_token()} if is_live else {}
        async with asyncio.timeout(timeout), autopilot.make_model_client(url, timeout) as client:
            response = await client.post(LIVE_MODEL_PATH if is_live else MODEL_PATH,
                                         json=model['request'], headers=headers)
            from service.audit.prediction_capture import now as capture_now
            received_at = capture_now()
        if observation_id is not None:
            from service.audit.prediction_capture import response as capture_response
            await asyncio.shield(asyncio.to_thread(capture_response, audit_engine, observation_id, response, received_at=received_at))
        if response.is_error:
            try:
                code = response.json().get('detail', {}).get('code', '')
            except (ValueError, AttributeError):
                code = ''
            if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
                model.update(http_status=response.status_code, error=code, decision='REVIEW', reason_codes=[code])
                return model, None
        response.raise_for_status()
        if len(response.content) > 1_048_576:
            raise ValueError('MODEL_RESPONSE_TOO_LARGE')
        data = response.json()
        json.dumps(data, allow_nan=False)
        model.update(response=data, prediction_id=response.headers.get('X-Prediction-Id'), checked_at_wall=wall_now().isoformat())
        decision, bid = validate_decision(model)
        model['decision'], model['reason_codes'] = decision.decision, decision.reason_codes
        return model, bid
    except Exception as exc:
        model.update(error=str(exc) if isinstance(exc, ValueError) else f'MODEL_UNAVAILABLE:{type(exc).__name__}',
                     decision='REVIEW', reason_codes=['MODEL_EVALUATION_FAILED'])
        if isinstance(exc, httpx.HTTPStatusError):
            model['http_status'] = exc.response.status_code
        return model, None


def matches_evaluation(model, checked):
    try:
        response, bid = validate_decision(model, fresh=False)
        context = model['request']['backend_context']
        position = context['projected_position']
        projection = checked.projection
        return (bid is not None and checked.economics.payout == bid and response.unit_id == str(checked.candidate_unit_id)
                and context['loaded_cost_dollars'] == model_number(checked.economics.loaded_cost)
                and context['deadhead_cost_dollars'] == model_number(checked.economics.projected_deadhead_cost)
                and context['deadhead_miles'] == float(checked.deadhead_miles)
                and projection is not None and position is not None
                and position['latitude'] == float(projection.latitude)
                and position['longitude'] == float(projection.longitude))
    except (ValueError, TypeError, KeyError):
        return False


def decision_rejections(model, mode=None):
    if 'error' in model:
        return [model['error']]
    response, _ = validate_decision(model, fresh=False)
    if response.decision != 'BID':
        return [f'MODEL_{response.decision}', *response.reason_codes]
    if (mode or model.get('backend_mode')) == 'live':
        if response.mode != 'live':
            return ['MODEL_SHADOW_ONLY', *response.live_blockers]
        if not response.eligible_for_live_submission or response.requires_review_before_live_use or response.live_blockers:
            return ['MODEL_LIVE_NOT_ELIGIBLE', *response.live_blockers]
    return []
