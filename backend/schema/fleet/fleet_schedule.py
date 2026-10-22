from datetime import datetime
from uuid import UUID

from pydantic import Field

from schema.shared.api import AssignmentView, View


class ScheduledAssignment(AssignmentView):
    external_id: str


class ScheduledReservation(View):
    action_id: UUID
    external_id: str
    action_type: str
    request_status: str
    busy_from: datetime
    busy_until: datetime


class UnitSchedule(View):
    unit_id: UUID
    board_unit_id: int
    unit_number: str
    terminal_code: str
    status: str
    board_missing: bool
    available_at: datetime | None
    as_of_sim_time: datetime | None
    assignments: list[ScheduledAssignment] = Field(default_factory=list)
    reservations: list[ScheduledReservation] = Field(default_factory=list)
