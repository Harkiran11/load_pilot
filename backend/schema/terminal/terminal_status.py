from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TerminalStatus(BaseModel):
    terminal_id: UUID
    code: str
    name: str
    city: str
    state: str
    latitude: Decimal | None
    longitude: Decimal | None
    status: Literal['available', 'unavailable', 'empty', 'attention', 'unknown']
    total_units: int | None
    available_units: int | None
    assigned_units: int | None
    board_total_units: int | None
    board_available_units: int | None
    board_assigned_units: int | None
    utilization_pct: Decimal | None = None
    available_pct: Decimal | None = None
    consistent: bool | None
    discrepancies: list[dict]
    as_of_sim_time: datetime | None
    last_synced_at: datetime | None


class TerminalStatusMessage(BaseModel):
    event: Literal['terminals.snapshot', 'terminals.updated', 'terminals.heartbeat']
    revision: int
    sent_at: datetime
    items: list[TerminalStatus]
