from datetime import datetime

from pydantic import BaseModel


class OrderExpiration(BaseModel):
    external_id: str
    status: str
    expires_at: datetime | None
    seconds_remaining: int | None
    sim_time_utc: datetime
    board_missing_at: datetime | None = None
