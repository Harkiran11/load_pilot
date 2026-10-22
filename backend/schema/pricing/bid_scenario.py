from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schema.pricing.economics import ExpectedEconomics
from schema.pricing.market_context import HistoricalMarketContext
from schema.shared.money import PositiveBid

ScenarioMetric = Literal['gross_profit', 'margin_pct', 'profit_per_hour', 'adjusted_profit']


class BidScenarioRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    unit_id: UUID | None = None
    entered_bids: list[PositiveBid] = Field(default_factory=list, max_length=20)
    include_suggestions: bool = True
    rank_by: ScenarioMetric = 'adjusted_profit'


class BidScenario(BaseModel):
    rank: int = 0
    bid_rate: Decimal
    labels: list[str]
    unit_id: UUID
    economics: ExpectedEconomics
    cash_requirements_met: bool
    booking_ready: bool
    rejection_reasons: list[str]


class BidScenarioReport(BaseModel):
    external_id: str
    rank_by: ScenarioMetric
    market_context: HistoricalMarketContext
    reference_economics: ExpectedEconomics | None = None
    scenarios: list[BidScenario] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    ranking_explanation: str = 'Cash-compliant scenarios rank first, then booking readiness and the selected metric descending; unknown metrics rank last. Higher proposed payouts are conditional on winning, not evidence of a better award probability.'
    historical_probability_label: str = 'Historical lane win fraction only, not bid-conditioned. Live sandbox awards are independent of bid amount; scenarios do not predict wins.'
