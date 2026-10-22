from datetime import datetime
from uuid import UUID

from fastapi import APIRouter

from controller.shared.dependencies import Database, Limit, Offset
from schema.shared.api import Page, UnitDetail, UnitView
from schema.fleet.fleet_capacity_response import FleetCapacityResponse
from schema.fleet.unit_projection import UnitProjection
from schema.fleet.fleet_schedule import UnitSchedule
from service.fleet.fleet_schedule import schedules, unit_schedule
from service.analytics import dashboard
from service.fleet.fleet import capacity_reports
from service.fleet.unit_projection import fleet_projections, unit_projection

router = APIRouter(prefix='/api/fleet', tags=['Fleet'])


@router.get('/capacity', response_model=list[FleetCapacityResponse])
def fleet_capacity(
    session: Database, discrepancies_only: bool = False,
) -> list[FleetCapacityResponse]:
    return capacity_reports(session, discrepancies_only)


@router.get('/schedules', response_model=Page[UnitSchedule])
def fleet_schedules(
    session: Database, limit: Limit = 50, offset: Offset = 0,
    unit_id: UUID | None = None, terminal: str | None = None,
    starts_at: datetime | None = None, ends_at: datetime | None = None,
    include_history: bool = False, include_missing: bool = False,
):
    return schedules(session, limit, offset, unit_id, terminal, starts_at, ends_at,
                     include_history, include_missing)


@router.get('/units/{unit_id}/schedule', response_model=UnitSchedule)
def fleet_unit_schedule(
    unit_id: UUID, session: Database, starts_at: datetime | None = None,
    ends_at: datetime | None = None, include_history: bool = False,
):
    return unit_schedule(session, unit_id, starts_at, ends_at, include_history)


@router.get('/units', response_model=Page[UnitView])
def units(
    session: Database, limit: Limit = 50, offset: Offset = 0,
    terminal: str | None = None, status: str | None = None, include_missing: bool = False,
):
    return dashboard.list_units(session, limit, offset, terminal, status, include_missing)


@router.get('/units/{unit_id}', response_model=UnitDetail)
def unit(unit_id: UUID, session: Database):
    return dashboard.unit_detail(session, unit_id)


@router.get('/projections', response_model=list[UnitProjection])
def projections(session: Database, at: datetime | None = None):
    return fleet_projections(session, at)


@router.get('/units/{unit_id}/projection', response_model=UnitProjection)
def projected_unit(unit_id: UUID, session: Database, at: datetime | None = None):
    return unit_projection(session, unit_id, at)


legacy_router = APIRouter(prefix='/fleet', tags=['Fleet'])
legacy_router.add_api_route(
    '/capacity', fleet_capacity, methods=['GET'], response_model=list[FleetCapacityResponse],
)
