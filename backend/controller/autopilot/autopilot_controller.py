from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket
from sqlalchemy import Engine

from controller.shared.dependencies import Limit, Offset
from schema.shared.api import Page
from schema.autopilot.autopilot import AutopilotAttemptView, AutopilotRunView, StartAutopilotRequest
from schema.allocation.fleet_allocation import AllocationPlanView
from service.autopilot import autopilot
from service.allocation.allocation_dispatch import list_plans
from service.shared.database import get_engine

router = APIRouter(prefix='/api/autopilot', tags=['Autopilot'])
DatabaseEngine = Annotated[Engine, Depends(get_engine)]


@router.post('/runs', response_model=AutopilotRunView, summary='Start a live autopilot run')
def start_run(body: StartAutopilotRequest, engine: DatabaseEngine):
    return autopilot.start_run(engine, body)


@router.get('/runs', response_model=Page[AutopilotRunView])
def runs(engine: DatabaseEngine, limit: Limit = 50, offset: Offset = 0):
    return autopilot.list_runs(engine, limit, offset)


@router.get('/runs/{run_id}', response_model=AutopilotRunView)
def run(run_id: UUID, engine: DatabaseEngine):
    return autopilot.get_run(engine, run_id)


@router.post('/runs/{run_id}/stop', response_model=AutopilotRunView)
def stop_run(run_id: UUID, engine: DatabaseEngine):
    return autopilot.get_run(engine, run_id, stop=True)


@router.get('/runs/{run_id}/attempts', response_model=Page[AutopilotAttemptView])
def attempts(run_id: UUID, engine: DatabaseEngine, limit: Limit = 50, offset: Offset = 0):
    return autopilot.list_attempts(engine, run_id, limit, offset)


@router.get('/runs/{run_id}/attempts/{attempt_id}', response_model=AutopilotAttemptView)
def attempt(run_id: UUID, attempt_id: UUID, engine: DatabaseEngine):
    return autopilot.get_attempt(engine, run_id, attempt_id)


@router.get('/runs/{run_id}/allocations', response_model=Page[AllocationPlanView])
def allocations(run_id: UUID, engine: DatabaseEngine, limit: Limit = 50, offset: Offset = 0):
    return list_plans(engine, run_id, limit, offset)


@router.websocket('/runs/{run_id}/ws')
async def subscribe_run(websocket: WebSocket, run_id: UUID, engine: DatabaseEngine,
                        limit: Annotated[int, Query(ge=1, le=50)] = 10):
    from service.autopilot.autopilot_stream import stream_run
    await stream_run(websocket, engine, run_id, limit)
