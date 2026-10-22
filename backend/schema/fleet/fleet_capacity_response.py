from datetime import datetime

from pydantic import BaseModel


class CapacityCounts(BaseModel):
    total_units: int
    available_units: int
    assigned_units: int


class FleetCapacityResponse(BaseModel):
    terminal_code: str
    board: CapacityCounts | None
    local: CapacityCounts
    consistent: bool
    discrepancies: list[dict]
    as_of_sim_time: datetime
    checked_at: datetime
