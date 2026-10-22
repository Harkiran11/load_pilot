from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from schema.pricing.economics import ExpectedEconomics
from schema.dispatch.occupancy import OccupiedWindow
from schema.fleet.unit_projection import UnitProjection
from schema.pricing.profit_adjustment import ProfitAdjustmentPolicy
from schema.dispatch.scheduling import ReservationConflict, ScheduleConflict


class DispatchPolicy(BaseModel):
    profit_adjustments: ProfitAdjustmentPolicy = Field(default_factory=ProfitAdjustmentPolicy)
    enforce_margin: bool = True
    min_expected_margin_pct: Decimal = Field(default=Decimal('15'), ge=0, le=100, allow_inf_nan=False)
    min_expected_margin_usd: Decimal = Field(default=Decimal('0'), ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    max_deadhead_miles: Decimal = Field(default=Decimal('100'), ge=0, allow_inf_nan=False)
    max_weight_lbs: int = Field(default=44000, gt=0)
    min_pickup_lead_minutes: Decimal = Field(default=Decimal('0'), ge=0, le=10080, allow_inf_nan=False)
    pickup_buffer_minutes: Decimal = Field(default=Decimal('0'), ge=0, le=1440, allow_inf_nan=False)
    assumed_travel_speed_mph: Decimal = Field(default=Decimal('45'), ge=1, le=90, allow_inf_nan=False)
    turnaround_minutes: Decimal = Field(default=Decimal('30'), ge=0, le=1440, allow_inf_nan=False)
    max_fleet_snapshot_age_seconds: int = Field(default=120, gt=0)


class DispatchRejection(BaseModel):
    code: str
    check: str
    message: str
    suggested_action: str
    context: dict = Field(default_factory=dict)


class DispatchUnitRejection(BaseModel):
    unit_id: UUID
    board_unit_id: int
    unit_number: str
    primary_rejection: DispatchRejection
    rejection_reasons: list[str]
    distance_miles: Decimal | None = None
    available_at_pickup: bool
    conflicting_assignments: list[ScheduleConflict] = Field(default_factory=list)
    conflicting_reservations: list[ReservationConflict] = Field(default_factory=list)


class DispatchEvaluation(BaseModel):
    external_id: str
    candidate_unit_id: UUID | None
    evaluated_at_sim: datetime
    policy: DispatchPolicy
    eligible: bool = False
    checks: dict[str, bool]
    skipped_checks: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    rejection_details: list[DispatchRejection] = Field(default_factory=list)
    primary_rejection: DispatchRejection | None = None
    unit_rejections: list[DispatchUnitRejection] = Field(default_factory=list)
    capacity_warnings: list[dict] = Field(default_factory=list)
    capacity_units_for_window: int = 0
    economics: ExpectedEconomics
    deadhead_miles: Decimal | None = None
    margin_pct_for_policy: Decimal | None = None
    occupied_window: OccupiedWindow | None = None
    earliest_pickup_at: datetime | None = None
    projection: UnitProjection | None = None
