from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from schema.dispatch.dispatch_policy import DispatchEvaluation
from schema.shared.money import PositiveBid


class PriceAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bid_rate: PositiveBid
    unit_id: UUID | None = None
    lane_id: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator('lane_id')
    @classmethod
    def normalize_lane(cls, value):
        if value is not None:
            value = value.strip().upper()
            if value.lower() in ('', 'na', 'n/a', 'null', 'none', 'nan'):
                raise ValueError('A nonmissing lane identifier is required')
        return value


class ModelShipmentFeatures(BaseModel):
    """Allowlist from the model contract; pricing/outcomes are never features."""
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    order_type: Literal['open_bid'] = 'open_bid'
    distance_miles: float = Field(gt=0)
    weight_lbs: float = Field(gt=0)
    origin_latitude: float = Field(ge=-90, le=90)
    origin_longitude: float = Field(ge=-180, le=180)
    destination_latitude: float = Field(ge=-90, le=90)
    destination_longitude: float = Field(ge=-180, le=180)
    lane_id: str = Field(min_length=1)
    origin_state: str = Field(min_length=1)
    destination_state: str = Field(min_length=1)

    @field_validator('lane_id', 'origin_state', 'destination_state')
    @classmethod
    def normalize_category(cls, value):
        value = value.strip().upper()
        if value.lower() in ('', 'na', 'n/a', 'null', 'none', 'nan'):
            raise ValueError('A nonmissing category is required')
        return value


class ModelBackendEconomics(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    source: Literal['backend'] = 'backend'
    total_estimated_cost_dollars: float | None = Field(ge=0)
    gross_profit_dollars: float | None
    margin_percent: float | None = Field(le=100)


class ModelWarningPolicy(BaseModel):
    minimum_profit_dollars: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    minimum_margin_percent: float | None = Field(default=None, ge=0, lt=100, allow_inf_nan=False)
    maximum_reference_deviation_percent: float = Field(default=25, ge=0, allow_inf_nan=False)


class ModelManualRequest(BaseModel):
    shipment: ModelShipmentFeatures
    manual_bid_dollars: float = Field(gt=0, allow_inf_nan=False)
    backend_economics: ModelBackendEconomics
    warning_policy: ModelWarningPolicy


class ModelUnavailableAdvice(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    status: Literal['unavailable']
    http_status: int
    error: dict


class ModelPriceAdvice(BaseModel):
    # Preserve the model's baseline, release, support and uncertainty metadata.
    model_config = ConfigDict(extra='allow', strict=True, allow_inf_nan=False)
    pricing_source: Literal['model', 'baseline']
    model_version: str | None
    predicted_bid_dollars: float = Field(gt=0)
    predicted_bid_rpm: float = Field(gt=0)
    status: Literal['fallback_advisory'] | None = None
    currency: Literal['USD']
    distance_basis: Literal['loaded_miles']
    advisory_only: Literal[True]
    submission_enabled: Literal[False]
    requires_guardrail_checks: Literal[True]
    requires_review: Literal[True]
    baseline_reference: dict
    uncertainty: dict
    warnings: list[dict]

    @model_validator(mode='after')
    def valid_source(self):
        if self.pricing_source == 'model' and (not self.model_version or self.status is not None):
            raise ValueError('Model advice requires a version and no fallback status')
        if self.pricing_source == 'baseline' and (self.model_version is not None or self.status != 'fallback_advisory'):
            raise ValueError('Baseline advice must be explicitly labeled as fallback')
        return self


class ModelComparison(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    manual_bid_rpm: float | None
    manual_minus_advisory_dollars: float | None
    absolute_reference_deviation_percent: float | None


class ModelManualResponse(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True, allow_inf_nan=False)
    api_version: Literal['v1']
    mode: Literal['manual']
    manual_bid_dollars: float
    backend_economics: ModelBackendEconomics
    economics_verified: Literal[False]
    advisory: ModelPriceAdvice | ModelUnavailableAdvice
    comparison: ModelComparison
    warnings: list[dict]
    advisory_only: Literal[True]
    submission_enabled: Literal[False]
    requires_guardrail_checks: Literal[True]


class PriceAnalysisError(BaseModel):
    code: str
    message: str
    http_status: int | None = None
    fields: list[str] = Field(default_factory=list)


class ModelLaneResolution(BaseModel):
    lane_id: str | None
    source: Literal['request', 'board_terminal_codes', 'linked_terminal_codes', 'nearest_terminal_codes', 'missing']
    origin_terminal_code: str | None = None
    destination_terminal_code: str | None = None
    terminal_sources: dict[str, Literal['board_terminal_code', 'linked_terminal', 'nearest_terminal', 'missing']] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PriceAnalysisResult(BaseModel):
    _operational_snapshot: dict = PrivateAttr(default_factory=dict)
    observation_id: UUID | None = None
    request_id: UUID
    external_id: str
    bid_rate: Decimal
    requested_unit_id: UUID | None
    order_last_synced_at: datetime | None
    analyzed_at_wall: datetime
    lane_id_source: Literal['request', 'board_terminal_codes', 'linked_terminal_codes', 'nearest_terminal_codes', 'missing']
    lane_resolution: ModelLaneResolution | None = None
    deterministic_evaluation: DispatchEvaluation
    model_request: ModelManualRequest | None = None
    model_response: ModelManualResponse | None = None
    advisory_status: Literal['model', 'baseline', 'unavailable'] = 'unavailable'
    prediction_id: str | None = None
    error: PriceAnalysisError | None = None
    warnings: list[str] = Field(default_factory=list)
    advisory_only: Literal[True] = True
    submission_enabled: Literal[False] = False
    requires_guardrail_checks: Literal[True] = True
