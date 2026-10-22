from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from schema.orders.order_expiration import OrderExpiration
from service.shared.database import get_session
from service.orders.expiration import SimulationClockUnavailable, get_order_expiration

router = APIRouter(prefix='/orders', tags=['Orders'])


@router.get('/{external_id}/expiration', response_model=OrderExpiration)
def order_expiration(
    external_id: str, session: Annotated[Session, Depends(get_session)],
) -> OrderExpiration:
    try:
        return get_order_expiration(session, external_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SimulationClockUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
