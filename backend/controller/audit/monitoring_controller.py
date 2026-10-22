import json
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from loadpilot_model.evaluation.monitoring import MonitoringExport

from controller.shared.dependencies import Limit, Offset
from schema.audit.prediction_observation import PredictionObservation
from service.shared.database import get_engine
from service.shared.errors import ProductError
from service.audit.live_audit import json_value
from service.audit.prediction_monitoring import build_report, export_observations, monitoring_settings

router = APIRouter(prefix='/api/analytics/model-monitoring', tags=['Model monitoring'])
DatabaseEngine = Annotated[Engine, Depends(get_engine)]


@router.get('/export', response_model=MonitoringExport)
def export(engine: DatabaseEngine, synthetic: bool = False):
    return export_observations(engine, synthetic=synthetic)[0]


@router.get('/status')
def status(engine: DatabaseEngine, synthetic: bool = False):
    return export_observations(engine, synthetic=synthetic)[1]


@router.get('/report')
def report(engine: DatabaseEngine, synthetic: bool = False):
    try:
        bundle, thresholds = monitoring_settings()
        payload, coverage = export_observations(engine, synthetic=synthetic)
        return build_report(payload, coverage, bundle, thresholds)
    except (ValueError, OSError, KeyError) as exc:
        raise ProductError(503, 'Configure a valid frozen MODEL_MONITORING_BUNDLE and monitoring thresholds; check its integrity') from exc


@router.get('/observations')
def observations(engine: DatabaseEngine, synthetic: bool = False, limit: Limit = 50, offset: Offset = 0):
    with Session(engine) as session:
        query = select(PredictionObservation).where(PredictionObservation.synthetic == synthetic)
        count = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(PredictionObservation.started_at_wall.desc()).limit(limit).offset(offset))
        items = [{column.name: getattr(row, column.name) for column in PredictionObservation.__table__.columns} for row in rows]
        return json.loads(json.dumps(dict(items=items, total=count, limit=limit, offset=offset), default=json_value))
