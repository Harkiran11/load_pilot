from fastapi import APIRouter

from controller.shared.dependencies import Database
from service.analytics import dashboard
from schema.shared.api import Overview

router = APIRouter(prefix='/api', tags=['Overview'])


@router.get('/overview', response_model=Overview)
def overview(session: Database):
    return dashboard.overview(session)
