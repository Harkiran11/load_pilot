from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from schema.allocation.fleet_allocation import AllocationPreviewRequest, AllocationResult
from service.shared.database import get_engine
from service.allocation.fleet_allocation import preview_allocation

router = APIRouter(prefix='/api/allocations', tags=['Fleet allocation'])
DatabaseEngine = Annotated[Engine, Depends(get_engine)]


@router.post('/preview', response_model=AllocationResult)
def preview(body: AllocationPreviewRequest, engine: DatabaseEngine):
    return preview_allocation(engine, body)
