"""Capture before HTTP, retaining failures and original cost/policy evidence."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from schema.audit.prediction_observation import PredictionObservation
from schema.orders.shipment import Shipment
from service.shared.errors import ProductError
from service.audit.audit_source import synthetic_source

FEATURES = ('distance_miles', 'weight_lbs', 'origin_latitude', 'origin_longitude',
            'destination_latitude', 'destination_longitude', 'origin_state', 'destination_state', 'lane_id')


def now():
    return datetime.now(timezone.utc)


def begin(engine, external_id, request):
    identity = uuid4()
    with Session(engine) as session, session.begin():
        shipment = session.scalar(select(Shipment).where(Shipment.external_id == external_id))
        features = {name: getattr(shipment, name, None) for name in FEATURES}
        features = {name: float(value) if isinstance(value, Decimal) else value for name, value in features.items()}
        session.add(PredictionObservation(observation_id=identity, request_id=request.request_id,
            external_id=external_id, synthetic=synthetic_source(),
            started_at_wall=now(), request=request.model_dump(mode='json'),
            context={'order_type': shipment.order_type if shipment else None, 'features': features}))
    return identity


def prepared(engine, identity, result):
    with Session(engine) as session, session.begin():
        row = session.get(PredictionObservation, identity)
        # The operational snapshot is written once, in `finish`, so the row is
        # not rewritten with a large payload mid-analysis (which held the SQLite
        # write lock and failed unrelated writes).
        row.context = row.context | {'prepared': result.model_dump(mode='json')}


def begin_autopilot(engine, model):
    identity = uuid4()
    payload = model['request']
    with Session(engine) as session, session.begin():
        session.add(PredictionObservation(observation_id=identity, request_id=uuid4(),
            external_id=payload['shipment_id'],
            synthetic=synthetic_source(),
            started_at_wall=now(), request=payload,
            context={'mode': 'autopilot', 'order_type': payload['shipment']['order_type'],
                     'features': {name: payload['shipment'].get(name) for name in FEATURES}, 'prepared': model}))
    return identity


def finish_autopilot(engine, identity, model):
    with Session(engine) as session, session.begin():
        row = session.get(PredictionObservation, identity)
        row.finished_at_wall, row.result, row.error_code = now(), model, model.get('error')


def response(engine, identity, reply, *, received_at=None):
    with Session(engine) as session, session.begin():
        row = session.get(PredictionObservation, identity)
        row.http_status = reply.status_code
        row.context = row.context | {'response_received_at_wall': (received_at or now()).isoformat()}
        # Bound native storage; never store headers (which may contain credentials).
        row.response_body = reply.text if len(reply.content) <= 1_048_576 else None


def finish(engine, identity, result=None, error=None):
    with Session(engine) as session, session.begin():
        row = session.get(PredictionObservation, identity)
        row.finished_at_wall = now()
        row.result = result.model_dump(mode='json') if result else None
        if result:
            row.context = row.context | {'operational_snapshot': result._operational_snapshot}
        row.error_code = result.error.code if result and result.error else error


def attach_execution(session, identity, shipment, request, action):
    row = session.get(PredictionObservation, identity)
    if row is None or row.external_id != shipment.external_id:
        raise ProductError(409, 'Price analysis does not match this order')
    if Decimal(row.request['bid_rate']) != request.bid_rate:
        raise ProductError(409, 'Bid differs from the analyzed price; request new analysis')
    row.executions = row.executions + [{'action_id': str(action.action_id),
        'requested_at_wall': action.requested_at.isoformat(), 'bid_rate': str(request.bid_rate),
        'evaluation_id': str(action.evaluation_id), 'request_status': action.request_status}]


def execution_result(session, action, shipment):
    for row in session.scalars(select(PredictionObservation).where(PredictionObservation.external_id == shipment.external_id)):
        if any(item['action_id'] == str(action.action_id) for item in row.executions):
            row.executions = [item | {'request_status': action.request_status,
                'board_http_status': action.board_http_status, 'message': action.board_message,
                'result_observed_at_wall': now().isoformat()} if item['action_id'] == str(action.action_id) else item
                for item in row.executions]


def observe_outcome(session, shipment, observed_at):
    for row in session.scalars(select(PredictionObservation).where(PredictionObservation.external_id == shipment.external_id)):
        if not row.executions:
            continue
        award = ('awarded' if shipment.status in ('accepted', 'in_transit', 'completed') else
                 'lost' if shipment.status == 'bid_lost' else 'unknown')
        executions = []
        for item in row.executions:
            outcome = {'award_status': award, 'board_status': shipment.status,
                'observed_at_wall': observed_at.isoformat(), 'actual_board_unit_id': shipment.board_assigned_unit_id}
            history = item.get('outcome_history', [])
            if not history or any(history[-1].get(key) != outcome.get(key) for key in (
                    'award_status', 'board_status', 'actual_board_unit_id')):
                history = history + [outcome]
            executions.append(item | {'outcome': outcome, 'outcome_history': history})
        row.executions = executions
