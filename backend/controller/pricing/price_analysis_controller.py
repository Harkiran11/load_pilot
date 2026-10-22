from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from schema.pricing.price_analysis import PriceAnalysisRequest, PriceAnalysisResult
from service.shared.database import get_engine
from service.pricing.price_analysis import analyze_price

router = APIRouter(prefix='/api/loads', tags=['On-demand price analysis'])


@router.post('/{external_id}/price-analysis', response_model=PriceAnalysisResult)
async def price_analysis(
    external_id: str, body: PriceAnalysisRequest,
    engine: Annotated[Engine, Depends(get_engine)],
):
    return await analyze_price(engine, external_id, body)
