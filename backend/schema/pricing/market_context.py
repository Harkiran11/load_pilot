from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class MarketStatistics(BaseModel):
    sample_count: int = 0
    median: Decimal | None = None
    mean: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    population_stddev: Decimal | None = None


class HistoricalWinContext(BaseModel):
    sample_count: int = 0
    wins: int = 0
    probability: Decimal | None = None
    label: str = 'Historical lane win fraction only; not conditioned on the proposed bid. Live sandbox awards are independent of bid amount.'


class HistoricalMarketContext(BaseModel):
    origin_state: str | None = None
    destination_state: str | None = None
    order_type: str
    matching_method: str
    since_sim: datetime
    through_sim: datetime
    radius_miles: Decimal
    minimum_samples: int
    sample_count: int = 0
    sufficient_samples: bool = False
    lane_ids: list[str] = Field(default_factory=list)
    payout: MarketStatistics = Field(default_factory=MarketStatistics)
    rate_per_mile: MarketStatistics = Field(default_factory=MarketStatistics)
    historical_win: HistoricalWinContext = Field(default_factory=HistoricalWinContext)
    warnings: list[str] = Field(default_factory=list)
    payout_basis: str = 'Won shipments: fixed offered payout or winning entered bid; zero-mile rows excluded from rate per mile.'
