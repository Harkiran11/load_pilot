from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from schema.dispatch.occupancy import OccupiedWindow
from schema.dispatch.scheduling import ReservationConflict, ScheduleConflict


class ProjectedDelivery(BaseModel):
    assignment_id: UUID
    shipment_id: UUID
    external_id: str
    pickup_at: datetime
    delivery_at: datetime
    occupied_from: datetime | None = None
    available_at: datetime | None = None
    terminal_id: UUID | None
    terminal_code: str | None
    terminal_resolution: Literal['destination_code', 'nearest_destination', 'unresolved']
    latitude: Decimal | None
    longitude: Decimal | None


class UnitProjection(BaseModel):
    unit_id: UUID
    board_unit_id: int
    baseline_sim_time: datetime | None = None
    at: datetime | None = None
    status: Literal['available', 'busy', 'unavailable', 'unknown'] = 'unknown'
    terminal_id: UUID | None = None
    terminal_code: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    available_at: datetime | None = None
    source: Literal['snapshot', 'booked_delivery', 'in_transit', 'unknown'] = 'unknown'
    source_assignment_id: UUID | None = None
    next_terminal_id: UUID | None = None
    next_terminal_code: str | None = None
    deliveries: list[ProjectedDelivery] = Field(default_factory=list)
    unverified_assignment_ids: list[UUID] = Field(default_factory=list)
    uncertain_action_ids: list[UUID] = Field(default_factory=list)
    uncertain_allocation_ids: list[UUID] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


class ChainingUnit(BaseModel):
    projection: UnitProjection
    distance_miles: Decimal | None = None
    projected_deadhead_miles: Decimal | None = None
    occupied_window: OccupiedWindow | None = None
    eligible: bool = False
    conflicting_assignments: list[ScheduleConflict] = Field(default_factory=list)
    conflicting_reservations: list[ReservationConflict] = Field(default_factory=list)
    at_current_pickup_hub: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
