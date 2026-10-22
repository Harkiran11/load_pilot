from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ScheduleConflict(BaseModel):
    assignment_id: UUID
    shipment_id: UUID
    busy_from: datetime
    busy_until: datetime
    assignment_status: str


class ReservationConflict(BaseModel):
    action_id: UUID | None
    allocation_reservation_id: UUID | None = None
    busy_from: datetime
    busy_until: datetime
    request_status: str


class PendingBidHold(BaseModel):
    reservation_id: UUID
    shipment_id: UUID
    unit_id: UUID
    action_id: UUID
    status: str = 'pending_bid'
    plan_id: UUID | None = None
    busy_from: datetime
    busy_until: datetime
