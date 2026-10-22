from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.shared.api import View
from schema.shared.money import PositiveBid


class AutopilotSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enable_fixed_rate: bool = True
    enable_open_bid: bool = True
    max_orders: int = Field(default=10, ge=1, le=10000, strict=True)
    max_duration_seconds: int = Field(default=1800, ge=1, le=86400, strict=True, description='Run duration in simulated seconds')
    max_estimated_loss_usd: Decimal = Field(default=Decimal('0'), ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    min_expected_profit_usd: Decimal = Field(default=Decimal('0'), ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    min_expected_margin_pct: Decimal = Field(default=Decimal('15'), ge=0, lt=100, allow_inf_nan=False)
    max_deadhead_miles: Decimal = Field(default=Decimal('100'), ge=0, allow_inf_nan=False)
    max_bid_rate: PositiveBid | None = None
    min_action_interval_seconds: float = Field(default=5, ge=1, le=3600, allow_inf_nan=False, description='Minimum submission spacing in simulated seconds')
    max_input_age_seconds: int = Field(default=120, ge=1, le=3600, strict=True, description='Operational snapshot freshness in real UTC seconds')
    max_decision_age_seconds: int = Field(default=30, ge=1, le=120, strict=True, description='External model approval freshness in real UTC seconds')
    max_consecutive_errors: int = Field(default=3, ge=1, le=100, strict=True)
    allow_baseline: bool = False
    model_baseline_id: str | None = Field(default=None, min_length=1)
    model_baseline_version: str | None = Field(default=None, min_length=1)
    model_min_lane_samples: int | None = Field(default=None, ge=1, strict=True)
    allocation_enabled: bool = True
    model_batch_enabled: bool = True
    model_batch_strategy: Literal['best_order', 'fleet_allocation'] = 'fleet_allocation'
    collection_window_seconds: float = Field(default=0, ge=0, le=300, allow_inf_nan=False, description='Order collection window in simulated seconds')
    allocation_max_candidates: int = Field(default=12, ge=1, le=30, strict=True)
    allocation_max_nodes: int = Field(default=2000, ge=1, le=100000, strict=True)
    allocation_max_seconds: float = Field(default=2, ge=.05, le=10, allow_inf_nan=False, description='CPU search budget in real seconds')
    allocation_submission_seconds: float = Field(default=20, ge=1, le=120, allow_inf_nan=False, description='Planned submission slot in simulated seconds')
    allocation_expiry_buffer_seconds: float = Field(default=1, ge=0, le=60, allow_inf_nan=False, description='Offer expiry buffer in simulated seconds')
    allocation_reservation_ttl_seconds: int = Field(default=30, ge=1, le=120, strict=True, description='Unsent capacity lease in simulated seconds')

    @model_validator(mode='after')
    def valid_types(self):
        if not self.enable_fixed_rate and not self.enable_open_bid:
            raise ValueError('Enable at least one booking type')
        if self.enable_open_bid and self.max_bid_rate is None:
            raise ValueError('max_bid_rate is required when open bids are enabled')
        if self.collection_window_seconds > 0 and not self.allocation_enabled:
            raise ValueError('collection_window_seconds requires allocation_enabled=true')
        if self.model_batch_enabled and not self.allocation_enabled:
            raise ValueError('model_batch_enabled requires allocation_enabled=true')
        return self


class StartAutopilotRequest(AutopilotSettings):
    run_id: UUID = Field(default_factory=uuid4)
    mode: Literal['live'] = 'live'


class AutopilotRunView(View):
    time_basis: Literal['simulation_clock', 'wall_clock_legacy']
    started_at_sim: datetime | None
    deadline_at_sim: datetime | None
    stopped_at_sim: datetime | None
    sim_time_utc: datetime | None
    simulation_speed: Decimal | None
    estimated_deadline_at_wall: datetime | None
    run_id: UUID
    status: str
    mode: str
    settings: AutopilotSettings
    started_at_wall: datetime
    deadline_at_wall: datetime | None
    stopped_at_wall: datetime | None
    stop_reason: str | None
    orders_reserved: int
    estimated_loss_usd: Decimal
    pending_estimated_loss_usd: Decimal
    consecutive_errors: int
    loss_basis: Literal['booking_time_estimates', 'shadow_decision_estimates']
    seconds_remaining: float
    inflight_attempt_ids: list[UUID]
    submissions_enabled: bool
    model_decision_mode: Literal['shadow', 'live']
    model_live_submission_supported: bool
    model_live_auth_configured: bool
    open_bid_submissions_enabled: bool
    fixed_rate_submissions_enabled: bool
    collection_plan_id: UUID | None
    collection_seconds_remaining: float
    collected_order_count: int


class AutopilotAttemptView(View):
    attempt_id: UUID
    run_id: UUID
    shipment_id: UUID
    external_id: str
    order_type: str
    status: str
    decision: str | None
    action_id: UUID | None
    candidate_unit_id: UUID | None
    bid_rate: Decimal | None
    model_snapshot: dict
    evaluation_snapshot: dict
    reason_codes: list[str]
    message: str | None
    created_at_wall: datetime
    decided_at_wall: datetime | None
    finished_at_wall: datetime | None
    allocation_plan_id: UUID | None = None
