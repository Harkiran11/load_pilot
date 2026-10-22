from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class OccupancyPolicy(BaseModel):
    assumed_travel_speed_mph: Decimal = Field(default=Decimal('45'), ge=1, le=90, allow_inf_nan=False)
    pickup_buffer_minutes: Decimal = Field(default=Decimal('0'), ge=0, le=1440, allow_inf_nan=False)
    turnaround_minutes: Decimal = Field(default=Decimal('30'), ge=0, le=1440, allow_inf_nan=False)


class OccupiedWindow(BaseModel):
    occupied_from: datetime
    occupied_until: datetime
    pickup_buffer_hours: Decimal = Decimal(0)
    deadhead_travel_hours: Decimal
    shipment_duration_hours: Decimal
    turnaround_hours: Decimal
    occupied_hours: Decimal
