"""Reported actual outcomes belong to booked shipments, separately from estimates."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from schema.shared.api import View

ActualMoney = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)]
MeasuredQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)]
EvidenceReference = Annotated[str, Field(min_length=1, max_length=1000)]


class ActualEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    payout: EvidenceReference
    costs: EvidenceReference
    empty_miles: EvidenceReference
    occupied_hours: EvidenceReference


class ShipmentFinancialRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    payout_usd: ActualMoney
    loaded_cost_usd: ActualMoney
    deadhead_cost_usd: ActualMoney
    other_cost_usd: ActualMoney
    empty_miles: MeasuredQuantity
    occupied_hours: MeasuredQuantity
    evidence: ActualEvidence
    reported_by: str = Field(min_length=1, max_length=200)
    actual_cost_scope_complete: bool = Field(default=False, strict=True)
    completed_at_wall: AwareDatetime | None = None
    cohort_id: UUID | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reviewer: str = Field(min_length=1, max_length=200)
    notes: str = Field(min_length=1, max_length=2000)


class ShipmentFinancialView(BaseModel):
    external_id: str
    status: Literal['missing', 'reported', 'reviewed']
    actuals: ShipmentFinancialRequest | None
    total_cost_usd: Decimal | None
    realized_profit_usd: Decimal | None
    margin_pct: Decimal | None
    booking_evaluation_id: UUID | None
    model_analysis_id: str | None
    recorded_at_wall: datetime | None
    reviewed_at_wall: datetime | None
    reviewer: str | None
    review_notes: str | None
    source: Literal['reported_actuals'] = 'reported_actuals'


class CreateObservedCohort(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    strategy: Literal['baseline', 'candidate']
    period_start_sim: AwareDatetime
    period_end_sim: AwareDatetime
    fleet_context: str = Field(min_length=1, max_length=1000)
    available_unit_hours: Decimal = Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    exposure_evidence: EvidenceReference
    policy_snapshot: dict

    @model_validator(mode='after')
    def valid_period(self):
        if self.period_end_sim <= self.period_start_sim:
            raise ValueError('The cohort period must have a positive simulated duration')
        if not self.policy_snapshot:
            raise ValueError('Record the policy used for this observed cohort')
        return self


class ObservedCohortView(View):
    cohort_id: UUID
    name: str
    strategy: str
    period_start_sim: datetime
    period_end_sim: datetime
    fleet_context: str
    available_unit_hours: Decimal
    exposure_evidence: str
    policy_snapshot: dict
    created_at_wall: datetime


class CohortComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    baseline_id: UUID
    candidate_id: UUID
    comparability_notes: str = Field(min_length=1, max_length=2000)


class ObservedComparisonView(View):
    comparison_id: UUID
    baseline_id: UUID
    candidate_id: UUID
    status: str
    comparability_notes: str
    metrics: dict
    evidence_fingerprint: str
    created_at_wall: datetime
    reviewed_at_wall: datetime | None
    reviewer: str | None
    review_notes: str | None
    automation_ready: bool = False
