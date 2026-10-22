from decimal import Decimal

from pydantic import BaseModel, Field


class ProfitAdjustmentPolicy(BaseModel):
    opportunity_cost_enabled: bool = True
    opportunity_cost_per_hour: Decimal = Field(default=Decimal('25'), ge=0, le=10000, allow_inf_nan=False)
    positioning_value_enabled: bool = True
    positioning_credit_fraction: Decimal = Field(default=Decimal('0.25'), ge=0, le=1, allow_inf_nan=False)
    positioning_max_credit: Decimal = Field(default=Decimal('500'), ge=0, le=100000, allow_inf_nan=False)
    historical_min_samples: int = Field(default=5, ge=1, le=10000)
    historical_lookback_days: int = Field(default=365, ge=1, le=3650)
    historical_origin_radius_miles: Decimal = Field(default=Decimal('100'), ge=0, le=1000, allow_inf_nan=False)


class ProfitAdjustmentDetails(BaseModel):
    calculation_version: str = 'capacity_positioning_v1'
    policy: ProfitAdjustmentPolicy = Field(default_factory=ProfitAdjustmentPolicy)
    opportunity_cost: dict = Field(default_factory=dict)
    positioning_value: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
