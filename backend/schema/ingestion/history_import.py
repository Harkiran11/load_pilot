from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from schema.ingestion.board_api import BoardPayload


class HistoricalCsvRow(BoardPayload):
    source_record_key: str = Field(validation_alias='shipment_id', min_length=1)
    lane_id: str = Field(min_length=1)
    origin_state: str = Field(min_length=1)
    destination_state: str = Field(min_length=1)
    origin_latitude: Decimal | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    origin_longitude: Decimal | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    destination_latitude: Decimal | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    destination_longitude: Decimal | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    order_type: Literal['fixed_rate', 'open_bid']
    distance_miles: Decimal = Field(ge=0, allow_inf_nan=False)
    weight_lbs: int = Field(gt=0)
    offered_rate: Decimal = Field(ge=0, allow_inf_nan=False)
    bid_rate: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    won: bool
    cost_per_mile: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    estimated_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    margin_estimate: Decimal | None = Field(default=None, allow_inf_nan=False)
    shipped_at: datetime
