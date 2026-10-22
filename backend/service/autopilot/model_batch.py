"""Live batch transport and authorization, bound to one immutable product snapshot."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

import httpx
from sqlalchemy.orm import Session

from schema.autopilot.autopilot_run import AutopilotRun
from schema.autopilot.model_autopilot import ModelLiveAutopilotResponse
from schema.autopilot.model_batch import ModelBatchRequest, ModelBatchResponse, PairRequest
from service.autopilot.autopilot_guards import wall_now
from service.dispatch.dispatch_policy import evaluate_dispatch
from service.orders.expiration import utc
from service.allocation.fleet_allocation import available_orders, candidate_orders, next_commitment_rejections
from service.autopilot.model_autopilot import context_fingerprint, live_model_token, prepare_decision
from service.pricing.price_analysis import model_number
from service.audit.live_audit import operational_snapshot

MODEL_BATCH_PATH = 'api/v1/autopilot/live/analyze-batch'


def prepare_batch(engine, config, run_id, plan_id, external_ids=None):
    prepared, errors, rows, unit_states = {}, {}, [], {}
    with Session(engine) as session:
        run = session.get(AutopilotRun, run_id)
        computed_at = wall_now()
        # Opaque, per-batch snapshot token. The model echoes it to bind every
        # pair to one request; hashing the full database here only multiplied
        # plan latency and memory without adding a safety property.
        version = hashlib.sha256(
            f'{run_id}:{plan_id}:{computed_at.isoformat()}'.encode()).hexdigest()
        all_orders = available_orders(session, config, external_ids, run_id)
        candidates = candidate_orders(all_orders, config)
        for order in candidates:
            try:
                checked = evaluate_dispatch(session, order)
                if next_commitment_rejections(session, order, checked):
                    raise ValueError('MODEL_NEXT_COMMITMENT_UNREACHABLE')
                item = prepare_decision(session, order, checked, config, version, run=run, computed_at=computed_at)
                item['backend_evaluation'] = checked.model_dump(mode='json')
                request = item['request']
                request['shipment']['order_type'] = order.order_type
                context = request['backend_context']
                # A shared computation epoch is separate from the unchanged
                # source timestamps. Different future positions cannot be
                # represented as one truck state in this model contract.
                state = (context['projected_position'], context['unit_observed_at'],
                         context['checks']['unit_available'], context['checks']['commitments_clear'])
                if request['unit_id'] in unit_states and unit_states[request['unit_id']] != state:
                    raise ValueError('MODEL_BATCH_UNIT_CONTEXT_CONFLICT')
                row = {key: request[key] for key in ('shipment_id', 'unit_id', 'shipment', 'backend_context')}
                if order.order_type == 'fixed_rate':
                    offer = order.offered_rate
                    if offer is None or offer <= 0 or offer != offer.quantize(Decimal('.01')):
                        raise ValueError('MODEL_FIXED_OFFER_INVALID')
                    row['offered_rate_dollars'] = model_number(offer)
                unit_states[request['unit_id']] = state
                rows.append(row)
                prepared[order.external_id] = item
            except (ValueError, TypeError) as exc:
                errors[order.external_id] = {'error': str(exc) if str(exc).startswith('MODEL_') else 'MODEL_INPUTS_MISSING_OR_INVALID',
                                             'decision': 'REVIEW'}
        if not rows:
            return None, prepared, errors
        first = next(iter(prepared.values()))['request']
        from service.pricing.price_analysis import analysis_settings
        _, timeout, _ = analysis_settings()
        envelope = ModelBatchRequest.model_validate({
            'mode': 'live', 'batch_id': str(plan_id), 'fleet_snapshot_version': version,
            'strategy': config.model_batch_strategy, 'orders': rows, 'policy': first['policy'], 'control': first['control'],
            'clock': first.get('clock'),
            'analysis_timeout_seconds': min(60, max(.001, timeout - min(.5, timeout / 2))),
        })
        payload = envelope.model_dump(mode='json', by_alias=True)
        for row in payload['orders']:
            if row['shipment']['order_type'] == 'open_bid':
                row.pop('offered_rate_dollars', None)
        snapshot = operational_snapshot(session, config)
        snapshot['evaluations'] = {key: value['backend_evaluation'] for key, value in prepared.items()}
        return {'request': payload, 'scope': {
            'available_order_count': len(all_orders), 'submitted_pair_count': len(rows),
            'omitted_external_ids': [order.external_id for order in all_orders if order.external_id not in prepared],
            'candidate_limit': config.allocation_max_candidates,
            'unit_scope': 'currently_board_selectable_units',
            'position_scope': 'coherent_pickup_projections_with_actual_source_timestamps',
        }, 'operational_snapshot': snapshot}, prepared, errors


def validate_batch(request_data, response_data, *, fresh=True):
    """Never treat an individually eligible but unselected pair as permission."""
    try:
        return _validate_batch(request_data, response_data, fresh=fresh)
    except (ArithmeticError, KeyError, TypeError) as exc:
        raise ValueError('MODEL_BATCH_INVALID_RESPONSE') from exc


def _validate_batch(request_data, response_data, *, fresh=True):
    json.dumps(response_data, allow_nan=False)
    request = ModelBatchRequest.model_validate_json(json.dumps(request_data))
    response = ModelBatchResponse.model_validate_json(json.dumps(response_data))
    expected = context_fingerprint(request)
    if (response.batch_id != request.batch_id or response.fleet_snapshot_version != request.fleet_snapshot_version
            or response.strategy != request.strategy or response.context_fingerprint != expected
            or response.live_request_fingerprint != expected or response.authorization_source != 'server_configuration'):
        raise ValueError('MODEL_BATCH_CONTEXT_MISMATCH')
    if response.evaluated_at.tzinfo is None or response.evaluated_at > wall_now() + timedelta(seconds=5):
        raise ValueError('MODEL_BATCH_TIME_INVALID')
    if not response.live_ready or response.submission_enabled or response.reservations_created or response.journal_recorded:
        raise ValueError('MODEL_BATCH_EXECUTION_OWNER_INVALID')
    if response_data.get('mode') != 'live' or any(type(response_data.get(key)) is not bool for key in (
            'live_ready', 'eligible_for_live_submission', 'requires_review_before_live_use')):
        raise ValueError('MODEL_BATCH_LIVE_FLAGS_INVALID')
    if request.control.autopilot_enabled is not True or request.control.kill_switch_active is not False:
        raise ValueError('MODEL_BATCH_CONTROLS_DISABLED')
    if any(type(row.get('selected')) is not bool or type(row.get('requires_review')) is not bool
           for row in response_data['evaluations']):
        raise ValueError('MODEL_BATCH_SELECTION_INVALID')
    known = {(item.shipment_id, item.unit_id): item for item in request.opportunities}
    seen, selected, units, orders = set(), [], set(), set()
    for row in response.evaluations:
        pair = row.evaluation
        if pair.evaluated_at.tzinfo is None or pair.evaluated_at != response.evaluated_at:
            raise ValueError('MODEL_BATCH_TIME_INVALID')
        key = pair.shipment_id, pair.unit_id
        if key not in known or key in seen:
            raise ValueError('MODEL_BATCH_PAIR_MISMATCH')
        seen.add(key)
        item = known[key]
        single = PairRequest(shipment_id=item.shipment_id, unit_id=item.unit_id, shipment=item.shipment,
                             backend_context=item.backend_context, policy=request.policy, control=request.control,
                             clock=request.clock).model_dump()
        if item.offered_rate_dollars is not None:
            single['offered_rate_dollars'] = item.offered_rate_dollars
        # The single-pair engine hashes offered prices too. The live wrapper
        # separately hashes the entire batch for every pair.
        from loadpilot_model.decision.engine import fingerprint as engine_fingerprint
        if (pair.context_fingerprint != engine_fingerprint(single) or pair.live_request_fingerprint != expected
                or pair.snapshot_version != request.fleet_snapshot_version
                or pair.policy_version != request.policy.policy_version):
            raise ValueError('MODEL_BATCH_PAIR_CONTEXT_MISMATCH')
        if row.selected:
            if (row.selection_reason != 'SELECTED' or row.requires_review or pair.decision != 'BID'
                    or not pair.eligible_for_live_submission or pair.requires_review_before_live_use or pair.live_blockers
                    or pair.authorization_source != 'server_configuration' or pair.execution_owner != 'backend'
                    or not pair.backend_revalidation_required or pair.submission_enabled or pair.journal_recorded
                    or pair.unit_id in units or pair.shipment_id in orders):
                raise ValueError('MODEL_BATCH_SELECTION_INVALID')
            units.add(pair.unit_id)
            orders.add(pair.shipment_id)
            selected.append(pair)
            validate_economics(request, item, pair, fresh=fresh)
        elif pair.eligible_for_live_submission:
            raise ValueError('MODEL_BATCH_UNSELECTED_ELIGIBLE')
    if response.analysis_status != 'unavailable' and seen != set(known):
        raise ValueError('MODEL_BATCH_PAIR_MISSING')
    if type(response_data['selected_count']) is not int or response.selected_count != len(selected):
        raise ValueError('MODEL_BATCH_TOTALS_INVALID')
    if selected:
        if (response.decision != 'BID' or response.analysis_status == 'unavailable'
                or not response.eligible_for_live_submission or response.requires_review_before_live_use
                or response.valid_until is None or (fresh and utc(response.valid_until) <= wall_now())
                or response.valid_until > min(pair.valid_until for pair in selected)):
            raise ValueError('MODEL_BATCH_NOT_AUTHORIZED')
        for field, pair_field in [('total_proposed_revenue_dollars', 'candidate_bid_dollars'),
                                  ('total_estimated_cost_dollars', 'total_estimated_cost_dollars'),
                                  ('total_projected_profit_dollars', 'estimated_profit_dollars')]:
            total = sum((Decimal(str(getattr(pair, pair_field))) for pair in selected), Decimal(0))
            if getattr(response, field) is None or Decimal(str(getattr(response, field))) != total:
                raise ValueError('MODEL_BATCH_TOTALS_INVALID')
        margin = Decimal(str(response.total_projected_profit_dollars)) * 100 / Decimal(str(response.total_proposed_revenue_dollars))
        if response.projected_margin_percent is None or abs(Decimal(str(response.projected_margin_percent)) - margin) > Decimal('1e-8'):
            raise ValueError('MODEL_BATCH_TOTALS_INVALID')
    elif response.decision != 'SKIP' or response.eligible_for_live_submission or response.valid_until is not None:
        raise ValueError('MODEL_BATCH_SKIP_INVALID')
    else:
        expected_total = None if response.analysis_status == 'unavailable' else 0
        if any(getattr(response, key) != expected_total for key in (
                'total_proposed_revenue_dollars', 'total_estimated_cost_dollars', 'total_projected_profit_dollars')):
            raise ValueError('MODEL_BATCH_TOTALS_INVALID')
        if response.projected_margin_percent is not None:
            raise ValueError('MODEL_BATCH_TOTALS_INVALID')
    return request, response


def validate_economics(request, item, response, *, fresh=True):
    context, policy = item.backend_context, request.policy
    if context is None or not all(value is True for value in context.checks.model_dump().values()):
        raise ValueError('MODEL_BATCH_OPERATIONAL_CHECK_FAILED')
    bid = Decimal(str(response.candidate_bid_dollars))
    if response.reasons or response.reason_codes != ['ALL_CHECKS_PASSED']:
        raise ValueError('MODEL_INVALID_BID_DECISION')
    if bid <= 0 or bid != bid.quantize(Decimal('.01')):
        raise ValueError('MODEL_CANDIDATE_FRACTIONAL_CENTS')
    if response.advisory is None or response.advisory.pricing_source not in ('model', 'baseline'):
        raise ValueError('MODEL_BATCH_REFERENCE_MISSING')
    if response.advisory.pricing_source == 'baseline':
        baseline = response.advisory.baseline_reference.model_dump()
        if (policy.fallback_mode != 'baseline' or policy.baseline_id is None or policy.baseline_version is None
                or policy.min_lane_samples is None or baseline.get('baseline_id') != policy.baseline_id
                or baseline.get('policy_version') != policy.baseline_version
                or baseline.get('min_lane_samples') != policy.min_lane_samples):
            raise ValueError('MODEL_BATCH_BASELINE_NOT_AUTHORIZED')
    if item.shipment.order_type == 'fixed_rate':
        if bid != Decimal(str(item.offered_rate_dollars)):
            raise ValueError('MODEL_FIXED_OFFER_CHANGED')
    elif (bid > Decimal(str(policy.max_bid_dollars)) or
          Decimal(str(response.advisory.predicted_bid_dollars)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) != bid):
        raise ValueError('MODEL_CANDIDATE_MISMATCH')
    total = Decimal(str(context.loaded_cost_dollars)) + Decimal(str(context.deadhead_cost_dollars))
    profit = bid - total
    if (response.loaded_cost_dollars != context.loaded_cost_dollars
            or response.deadhead_cost_dollars != context.deadhead_cost_dollars
            or Decimal(str(response.total_estimated_cost_dollars)) != total
            or Decimal(str(response.estimated_profit_dollars)) != profit
            or profit < Decimal(str(policy.min_profit_dollars))
            or profit * 100 < Decimal(str(policy.min_margin_percent)) * bid
            or context.deadhead_miles is None or context.deadhead_miles > policy.max_deadhead_miles):
        raise ValueError('MODEL_ECONOMICS_MISMATCH')
    margin = profit * 100 / bid
    if response.estimated_margin_percent is None or abs(Decimal(str(response.estimated_margin_percent)) - margin) > Decimal('1e-8'):
        raise ValueError('MODEL_ECONOMICS_MISMATCH')
    rpm = bid / Decimal(str(item.shipment.distance_miles))
    if response.candidate_bid_rpm is None or abs(Decimal(str(response.candidate_bid_rpm)) - rpm) > max(Decimal('1e-8'), abs(rpm) * Decimal('1e-9')):
        raise ValueError('MODEL_CANDIDATE_RPM_MISMATCH')
    deadlines = [utc(response.evaluated_at) + timedelta(seconds=policy.max_decision_age_seconds)]
    for name, stamp in [('unit_position', context.projected_position.computed_at if context.projected_position else None),
                        ('unit_availability', context.unit_observed_at), ('costs', context.costs_observed_at),
                        ('order_status', context.order_observed_at)]:
        if stamp is None:
            raise ValueError('MODEL_BATCH_SOURCE_TIMESTAMP_MISSING')
        limit = getattr(policy.max_input_age_seconds, name)
        if not -5 <= (wall_now() - utc(stamp)).total_seconds() <= limit:
            raise ValueError('MODEL_BATCH_SOURCE_STALE')
        deadlines.append(utc(stamp) + timedelta(seconds=limit))
    if context.order_expires_at is None:
        raise ValueError('MODEL_BATCH_EXPIRY_MISSING')
    deadlines.append(utc(context.order_expires_at))
    # Immediate analysis rejects an already-expired quote. Re-validating a
    # stored plan snapshot later checks the authorization bound only; the
    # reservation path enforces its own max_decision_age quote window.
    if (response.valid_until is None or (fresh and utc(response.valid_until) <= wall_now())
            or utc(response.valid_until) > min(deadlines)):
        raise ValueError('MODEL_INVALID_DECISION_LIFETIME')


def validate_pair(model):
    request, response = validate_batch(model['batch_request'], model['batch_response'], fresh=False)
    identity = model['request']['shipment_id'], model['request']['unit_id']
    row = next((row for row in response.evaluations
                if (row.evaluation.shipment_id, row.evaluation.unit_id) == identity), None)
    item = next((item for item in request.opportunities if (item.shipment_id, item.unit_id) == identity), None)
    if row is None or item is None or row.evaluation.model_dump(mode='json') != model['response']:
        raise ValueError('MODEL_BATCH_PAIR_MISMATCH')
    expected = PairRequest(shipment_id=item.shipment_id, unit_id=item.unit_id, shipment=item.shipment,
                           backend_context=item.backend_context, policy=request.policy, control=request.control,
                           clock=request.clock).model_dump(mode='json')
    if expected != model['request']:
        raise ValueError('MODEL_BATCH_PAIR_CONTEXT_MISMATCH')
    pair = ModelLiveAutopilotResponse.model_validate_json(json.dumps(model['response']))
    if not row.selected:
        return pair, None
    return pair, Decimal(str(pair.candidate_bid_dollars))


async def analyze_batch(prepared, inputs, errors, *, audit_engine=None):
    from service.autopilot import autopilot
    models = dict(errors)
    if prepared is None:
        return {}, models, {'error': 'MODEL_BATCH_NO_USABLE_PAIRS'}
    audit = dict(prepared)
    capture_id = None
    try:
        if audit_engine is not None:
            from service.audit.live_audit import begin_capture
            capture_id = await asyncio.to_thread(begin_capture, audit_engine, prepared)
            audit['audit_id'] = str(capture_id)
        url, timeout, _ = autopilot.analysis_settings()
        async with asyncio.timeout(timeout), autopilot.make_model_client(url, timeout) as client:
            response = await client.post(MODEL_BATCH_PATH, json=prepared['request'],
                                         headers={'Authorization': 'Bearer ' + live_model_token()})
            from service.audit.live_audit import now
            received_at = now()
        if capture_id is not None:
            from service.audit.live_audit import finish_capture
            await asyncio.shield(asyncio.to_thread(finish_capture, audit_engine, capture_id,
                                                  response=response, received_at=received_at))
        response.raise_for_status()
        if len(response.content) > 4_194_304:
            raise ValueError('MODEL_BATCH_RESPONSE_TOO_LARGE')
        data = response.json()
        json.dumps(data, allow_nan=False)
        _, validated = validate_batch(prepared['request'], data)
        audit.update(response=validated.model_dump(mode='json'), checked_at_wall=wall_now().isoformat())
        bids = {}
        for row in validated.evaluations:
            pair = row.evaluation
            item = inputs[pair.shipment_id]
            models[pair.shipment_id] = {**item, 'kind': 'batch', 'batch_request': prepared['request'],
                'batch_response': audit['response'], 'response': pair.model_dump(mode='json'),
                'selected': row.selected, 'decision': pair.decision, 'reason_codes': pair.reason_codes,
                'audit_id': str(capture_id) if capture_id else None,
                'comparison': row.comparison.model_dump(mode='json'), 'checked_at_wall': audit['checked_at_wall']}
            if row.selected:
                bids[pair.shipment_id] = Decimal(str(pair.candidate_bid_dollars))
        for external_id in inputs.keys() - models.keys():
            models[external_id] = {'kind': 'batch', 'error': 'MODEL_BATCH_ANALYSIS_UNAVAILABLE',
                                   'decision': 'REVIEW', 'reason_codes': validated.reason_codes}
        return bids, models, audit
    except asyncio.CancelledError:
        if capture_id is not None:
            from service.audit.live_audit import finish_capture
            await asyncio.shield(asyncio.to_thread(finish_capture, audit_engine, capture_id, error='MODEL_REQUEST_CANCELLED'))
        raise
    except (ValueError, KeyError, TypeError, ArithmeticError, httpx.HTTPError, TimeoutError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('MODEL_') else 'MODEL_BATCH_UNAVAILABLE'
        audit.update(error=code, http_status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None)
        if capture_id is not None:
            from service.audit.live_audit import finish_capture
            await asyncio.to_thread(finish_capture, audit_engine, capture_id, error=code)
        for external_id in inputs:
            models[external_id] = {'kind': 'batch', 'error': code, 'decision': 'REVIEW'}
        return {}, models, audit
