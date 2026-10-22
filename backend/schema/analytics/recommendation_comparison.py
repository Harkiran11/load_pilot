from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObservedCohort(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_reference: str = Field(min_length=1, max_length=500)
    completed_loads: int = Field(gt=0)
    realized_profit: Decimal = Field(max_digits=24, decimal_places=6, allow_inf_nan=False,
        description='Measured payouts minus actual operating costs; do not supply estimated profit.')
    empty_miles: Decimal = Field(ge=0, max_digits=24, decimal_places=6, allow_inf_nan=False)
    occupied_unit_hours: Decimal = Field(ge=0, max_digits=24, decimal_places=6, allow_inf_nan=False)
    available_unit_hours: Decimal = Field(gt=0, max_digits=24, decimal_places=6, allow_inf_nan=False,
        description='Total on-duty fleet capacity hours in the cohort, including occupied hours.')

    @model_validator(mode='after')
    def valid_hours(self):
        if self.occupied_unit_hours > self.available_unit_hours:
            raise ValueError('occupied_unit_hours cannot exceed available_unit_hours')
        return self


class RecommendationComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    comparable_period_description: str = Field(min_length=1, max_length=1000)
    baseline: ObservedCohort
    candidate: ObservedCohort


class CohortMetrics(BaseModel):
    completed_loads: int
    realized_profit: Decimal
    profit_per_completed_load: Decimal
    empty_miles: Decimal
    empty_miles_per_completed_load: Decimal
    utilization_pct: Decimal


class RecommendationComparison(BaseModel):
    evidence: RecommendationComparisonRequest
    baseline: CohortMetrics
    candidate: CohortMetrics
    candidate_minus_baseline: dict[str, Decimal]
    automation_ready: bool = False
    explanation: str = 'Measured cohort comparison using supplied evidence, not a causal backtest. Verify actual costs, comparable simulated periods/fleet exposure, and overlapping commitments before enabling automation. Board estimated costs are not realized costs.'
