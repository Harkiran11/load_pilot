from uuid import UUID

from pydantic import BaseModel, ConfigDict
from schema.shared.money import PositiveBid


class CostPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    unit_id: UUID | None = None
    bid_rate: PositiveBid | None = None
