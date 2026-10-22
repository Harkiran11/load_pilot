import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.orm import Session

from controller.shared.dependencies import Limit, Offset
from schema.autopilot.autopilot_run import AutopilotRun
from schema.autopilot.autopilot_attempt import AutopilotAttempt
from schema.audit.live_audit import LiveBatchAudit, LiveSubmissionAudit
from schema.audit.prediction_observation import PredictionObservation
from schema.audit.live_audit_export import LiveAuditExport
from service.shared.database import get_engine
from service.shared.errors import ProductError
from service.audit.live_audit import export_live_audit, json_value

router = APIRouter(prefix='/api/autopilot/live-audit', tags=['Live audit'])
DatabaseEngine = Annotated[Engine, Depends(get_engine)]


@router.get('/export', response_model=LiveAuditExport)
def export(engine: DatabaseEngine, run_id: UUID | None = None, synthetic: bool = False):
    payload, coverage = export_live_audit(engine, run_id, synthetic=synthetic)
    return JSONResponse(payload, headers={'X-Audit-Captured-Batches': str(coverage['captured_batch_count']),
        'X-Audit-Skipped-Captures': str(len(coverage['skipped_captures']))})


@router.get('/status')
def status(engine: DatabaseEngine, run_id: UUID | None = None, synthetic: bool = False):
    _, coverage = export_live_audit(engine, run_id, synthetic=synthetic)
    return coverage


@router.get('/records')
def records(engine: DatabaseEngine, run_id: UUID | None = None, synthetic: bool = False,
            limit: Limit = 50, offset: Offset = 0):
    with Session(engine) as session:
        if run_id is not None and session.get(AutopilotRun, run_id) is None:
            raise ProductError(404, 'Autopilot run not found')
        query = select(LiveBatchAudit).where(LiveBatchAudit.synthetic == synthetic)
        if run_id is not None:
            query = query.where(LiveBatchAudit.run_id == run_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(LiveBatchAudit.requested_at_wall.desc(), LiveBatchAudit.audit_id).limit(limit).offset(offset))
        items = [{column.name: getattr(row, column.name) for column in LiveBatchAudit.__table__.columns} for row in rows]
        return json.loads(json.dumps({'items': items, 'total': total, 'limit': limit, 'offset': offset}, default=json_value))


@router.get('/submissions')
def submission_records(engine: DatabaseEngine, run_id: UUID | None = None, synthetic: bool = False,
                       limit: Limit = 50, offset: Offset = 0):
    with Session(engine) as session:
        if run_id is not None and session.get(AutopilotRun, run_id) is None:
            raise ProductError(404, 'Autopilot run not found')
        batches = select(LiveBatchAudit.audit_id).where(LiveBatchAudit.synthetic == synthetic)
        observations = [str(identity) for identity in session.scalars(select(PredictionObservation.observation_id).where(
            PredictionObservation.synthetic == synthetic))]
        individual_attempts = select(AutopilotAttempt.attempt_id).where(
            AutopilotAttempt.model_snapshot['observation_id'].as_string().in_(observations))
        query = select(LiveSubmissionAudit).join(AutopilotAttempt).where(or_(
            LiveSubmissionAudit.batch_audit_id.in_(batches), and_(LiveSubmissionAudit.batch_audit_id.is_(None),
            LiveSubmissionAudit.attempt_id.in_(individual_attempts))))
        if run_id is not None:
            query = query.where(AutopilotAttempt.run_id == run_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(query.order_by(LiveSubmissionAudit.attempted_at_wall.desc()).limit(limit).offset(offset))
        items = [{column.name: getattr(row, column.name) for column in LiveSubmissionAudit.__table__.columns} for row in rows]
        return json.loads(json.dumps({'items': items, 'total': total, 'limit': limit, 'offset': offset}, default=json_value))
