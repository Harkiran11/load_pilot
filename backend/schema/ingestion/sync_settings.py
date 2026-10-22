from pydantic import BaseModel, Field


class SyncSettings(BaseModel):
    interval_seconds: float = Field(default=30, ge=1, le=3600, allow_inf_nan=False)
    websocket_enabled: bool = True
