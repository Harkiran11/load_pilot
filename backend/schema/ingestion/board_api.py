"""Incoming payloads from the board API, separate from database models."""

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class BoardPayload(BaseModel):
    @field_validator('*')
    @classmethod
    def normalize_timestamps(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class BoardTerminal(BoardPayload):
    id: int
    code: str = Field(min_length=1)
    name: str
    city: str
    state: str
    latitude: Decimal
    longitude: Decimal
    unit_capacity: int


class BoardFleetUnit(BoardPayload):
    id: int
    employee_code: str
    unit_number: str
    first_name: str
    last_name: str
    home_terminal_id: int
    status: str
    current_latitude: Decimal
    current_longitude: Decimal
    available_at: datetime | None = None


class BoardFleetSnapshot(BoardPayload):
    terminal: BoardTerminal
    total_units: int
    available_units: int
    assigned_units: int
    units: list[BoardFleetUnit]


class BoardClock(BoardPayload):
    wall_time_utc: datetime
    sim_time_utc: datetime
    simulation_speed: Decimal = Field(gt=0)
    note: str | None = None


class BoardOrder(BoardPayload):
    id: int
    external_id: str = Field(min_length=1)
    order_type: str
    status: str
    origin_terminal_code: str | None = None
    destination_terminal_code: str | None = None
    origin_city: str | None = None
    origin_state: str | None = None
    origin_latitude: Decimal | None = None
    origin_longitude: Decimal | None = None
    destination_city: str | None = None
    destination_state: str | None = None
    destination_latitude: Decimal | None = None
    destination_longitude: Decimal | None = None
    distance_miles: Decimal | None = None
    weight_lbs: int | None = None
    offered_rate: Decimal | None = None
    cost_per_mile: Decimal | None = None
    estimated_cost: Decimal | None = None
    estimated_deadhead_miles: Decimal | None = None
    estimated_deadhead_cost: Decimal | None = None
    estimated_total_cost: Decimal | None = None
    pickup_at: datetime | None = None
    delivery_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    bid_decision_at: datetime | None = None
    winning_bid_rate: Decimal | None = None
    assigned_driver_id: int | None = None
    assigned_terminal_code: str | None = None
