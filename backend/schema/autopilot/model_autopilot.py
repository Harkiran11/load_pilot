from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from schema.pricing.price_analysis import ModelPriceAdvice, ModelShipmentFeatures, ModelUnavailableAdvice


class ModelUnits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    distance: Literal['miles'] = 'miles'
    weight: Literal['lb'] = 'lb'
    coordinates: Literal['degrees'] = 'degrees'
    currency: Literal['USD'] = 'USD'


class DecisionShipment(ModelShipmentFeatures):
    units: ModelUnits = Field(default_factory=ModelUnits)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)


class DecisionPosition(DecisionInput):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    computed_at: AwareDatetime | None


class DecisionChecks(DecisionInput):
    unit_available: bool | None
    commitments_clear: bool | None
    capacity_compatible: bool | None
    equipment_compatible: bool | None
    location_verified: bool | None
    timing_feasible: bool | None
    order_open: bool | None


class DecisionContext(DecisionInput):
    shipment_id: str
    unit_id: str
    snapshot_version: str
    projected_position: DecisionPosition | None
    loaded_cost_dollars: float | None = Field(ge=0)
    deadhead_cost_dollars: float | None = Field(ge=0)
    deadhead_miles: float | None = Field(ge=0)
    costs_observed_at: AwareDatetime | None
    unit_observed_at: AwareDatetime | None
    order_observed_at: AwareDatetime | None
    order_expires_at: AwareDatetime | None
    clock_id: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    clock_revision: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    operational_expires_at: AwareDatetime | None = Field(default=None, exclude_if=lambda value: value is None)
    checks: DecisionChecks


class DecisionClock(DecisionInput):
    """The explicit wall/simulation mapping required by live model gates."""

    clock_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._-]{1,128}$')
    revision: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._-]{1,128}$')
    simulated_at: AwareDatetime
    observed_at: AwareDatetime
    speed: float = Field(ge=0)
    max_snapshot_age_seconds: float = Field(gt=0)
    max_simulated_input_age_seconds: float = Field(gt=0)


class DecisionInputAges(DecisionInput):
    unit_position: float = Field(gt=0)
    unit_availability: float = Field(gt=0)
    costs: float = Field(gt=0)
    order_status: float = Field(gt=0)


class DecisionPolicy(DecisionInput):
    policy_version: str = Field(pattern=r'^[A-Za-z0-9._-]{1,64}$')
    currency: Literal['USD'] = 'USD'
    min_profit_dollars: float = Field(ge=0)
    min_margin_percent: float = Field(ge=0, lt=100)
    max_bid_dollars: float = Field(gt=0)
    max_deadhead_miles: float = Field(ge=0)
    max_input_age_seconds: DecisionInputAges
    max_decision_age_seconds: float = Field(gt=0)
    prediction_timeout_seconds: float = Field(gt=0, le=300)
    max_reference_deviation_percent: float = Field(ge=0)
    fallback_mode: Literal['review', 'baseline'] = 'review'
    unseen_category_action: Literal['review', 'baseline'] = 'review'
    baseline_id: str | None = None
    baseline_version: str | None = None
    min_lane_samples: int | None = Field(default=None, ge=1)


class DecisionControl(DecisionInput):
    autopilot_enabled: bool | None
    kill_switch_active: bool | None


class ModelAutopilotRequest(DecisionInput):
    mode: Literal['shadow'] = 'shadow'
    shipment_id: str
    unit_id: str
    shipment: DecisionShipment
    backend_context: DecisionContext
    policy: DecisionPolicy
    control: DecisionControl
    clock: DecisionClock | None = Field(default=None, exclude_if=lambda value: value is None)


class ModelLiveAutopilotRequest(ModelAutopilotRequest):
    mode: Literal['live'] = 'live'


class ModelDecisionReason(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True, allow_inf_nan=False)
    code: str
    severity: Literal['SKIP', 'REVIEW']
    field: str
    details: dict


class ModelAutopilotResponse(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True, allow_inf_nan=False)
    api_version: Literal['v1']
    mode: Literal['shadow']
    decision_id: str = Field(min_length=1)
    decision: Literal['BID', 'SKIP', 'REVIEW']
    reason_codes: list[str]
    reasons: list[ModelDecisionReason]
    shipment_id: str
    unit_id: str
    snapshot_version: str | None
    policy_version: str | None
    context_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    evaluated_at: AwareDatetime
    valid_until: AwareDatetime | None
    candidate_bid_dollars: float | None = Field(ge=0)
    candidate_bid_rpm: float | None = Field(ge=0)
    loaded_cost_dollars: float | None = Field(ge=0)
    deadhead_cost_dollars: float | None = Field(ge=0)
    total_estimated_cost_dollars: float | None = Field(ge=0)
    minimum_bid_dollars: float | None = Field(ge=0)
    estimated_profit_dollars: float | None
    estimated_margin_percent: float | None
    reference_deviation_percent: float | None = Field(ge=0)
    advisory: ModelPriceAdvice | ModelUnavailableAdvice | None
    currency: Literal['USD']
    submission_enabled: Literal[False]
    eligible_for_live_submission: Literal[False]
    requires_review_before_live_use: Literal[True]
    live_blockers: list[str]


class ModelLiveAutopilotResponse(ModelAutopilotResponse):
    mode: Literal['live']
    eligible_for_live_submission: bool
    requires_review_before_live_use: bool
    release_approval_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9._-]{1,64}$')
    authorization_source: Literal['server_configuration'] | None = None
    model_bundle_id: str | None = None
    live_request_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    execution_owner: Literal['backend']
    backend_revalidation_required: Literal[True]
    journal_recorded: Literal[False]
