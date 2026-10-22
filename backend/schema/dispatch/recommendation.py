from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from schema.pricing.economics import ExpectedEconomics
from schema.dispatch.dispatch_policy import DispatchEvaluation, DispatchRejection
from schema.pricing.market_context import HistoricalMarketContext


class RankingPolicy(BaseModel):
    profit_weight: Decimal = Field(default=Decimal('0.40'), ge=0, le=1, allow_inf_nan=False)
    profit_target_per_hour: Decimal = Field(default=Decimal('100'), gt=0, le=100000, allow_inf_nan=False)
    empty_miles_weight: Decimal = Field(default=Decimal('0.60'), ge=0, le=1, allow_inf_nan=False)
    continuity_weight: Decimal = Field(default=Decimal('0.25'), ge=0, le=1, allow_inf_nan=False)
    utilization_weight: Decimal = Field(default=Decimal('0.15'), ge=0, le=1, allow_inf_nan=False)
    utilization_horizon_hours: Decimal = Field(default=Decimal('24'), ge=1, le=168, allow_inf_nan=False)

    @model_validator(mode='after')
    def positive_weight_sum(self):
        if self.empty_miles_weight + self.continuity_weight + self.utilization_weight + self.profit_weight <= 0:
            raise ValueError('At least one ranking weight must be positive')
        return self


class RankedMatch(BaseModel):
    profit_score: Decimal | None = None
    ranking_cash_requirements_met: bool = False
    ranking_payout_basis: str = 'unknown'
    ranking_bid_rate: Decimal | None = None
    ranking_economics: ExpectedEconomics | None = None
    rank: int = 0
    external_id: str
    shipment_id: UUID
    unit_id: UUID
    board_unit_id: int
    unit_number: str
    pickup_at: datetime
    delivery_at: datetime
    pickup_terminal_code: str
    projected_latitude: Decimal
    projected_longitude: Decimal
    position_source: str
    source_assignment_id: UUID | None
    deadhead_miles: Decimal
    cost_per_mile: Decimal | None
    deadhead_cost: Decimal | None
    cost_missing_reason: str | None = None
    economics: ExpectedEconomics
    empty_miles_score: Decimal
    continues_previous_route: bool
    previous_assignment_id: UUID | None
    idle_gap_seconds: Decimal | None
    continuity_score: Decimal
    booked_hours: Decimal
    added_loaded_hours: Decimal
    utilization_before: Decimal
    utilization_after: Decimal
    score: Decimal
    booking_ready: bool
    dispatch_rejection_reasons: list[str] = Field(default_factory=list)
    dispatch_rejection_details: list[DispatchRejection] = Field(default_factory=list)
    dispatch_primary_rejection: DispatchRejection | None = None


class RejectedUnit(BaseModel):
    unit_id: UUID
    rejection_reasons: list[str]


class OrderRecommendations(BaseModel):
    market_context: HistoricalMarketContext | None = None
    external_id: str
    evaluated_at_sim: datetime
    horizon_end: datetime
    policy: RankingPolicy
    entered_bid_rate: Decimal | None = None
    dispatch_evaluation: DispatchEvaluation | None = None
    selected: RankedMatch | None = None
    ranked_units: list[RankedMatch] = Field(default_factory=list)
    rejected_units: list[RejectedUnit] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


class RecommendationsPage(BaseModel):
    items: list[RankedMatch]
    total: int
    limit: int
    offset: int
    evaluated_at_sim: datetime
    horizon_end: datetime
    policy: RankingPolicy
