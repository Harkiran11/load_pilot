from fastapi import APIRouter

from schema.system.health_schema import HealthResponse
from service.system.health_service import get_health

router = APIRouter(prefix='/health', tags=['Health'])


@router.get('', response_model=HealthResponse)
def health_check() -> HealthResponse:
    return get_health()
