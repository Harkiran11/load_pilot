"""Backend-triggered live evaluations; no submission or fleet retrieval adapters."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from loadpilot_model.api.manual import UnavailableAdvice
from loadpilot_model.api.pricing import (
    BaselineFallbackResponse,
    BidPredictionRequest,
    BidPredictionResponse,
)
from loadpilot_model.api.telemetry import (
    PricingLog,
    current_log,
    identifier,
    note_error,
    note_prediction,
    write_event,
)
from loadpilot_model.decision.engine import evaluate


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def nonmissing_identifiers(cls, value, info):
        if info.field_name in {"shipment_id", "unit_id", "snapshot_version"} and not value.strip():
            raise ValueError("A nonmissing identifier is required")
        return value


class TimestampInput(StrictInput):
    @field_validator("*", mode="before")
    @classmethod
    def parse_timestamps(cls, value, info):
        if info.field_name.endswith("_at") and isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class AutopilotShipment(BidPredictionRequest):
    order_type: Literal["open_bid", "fixed_rate"] = "open_bid"


class InputAgePolicy(StrictInput):
    unit_position: float = Field(gt=0)
    unit_availability: float = Field(gt=0)
    costs: float = Field(gt=0)
    order_status: float = Field(gt=0)


class SimulationClock(TimestampInput):
    clock_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    revision: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    simulated_at: AwareDatetime
    observed_at: AwareDatetime
    speed: float = Field(ge=0)
    max_snapshot_age_seconds: float = Field(gt=0)
    max_simulated_input_age_seconds: float = Field(gt=0)


class ClockEvaluation(BaseModel):
    domain: Literal["wall", "simulation"]
    clock_id: str | None
    revision: str | None
    speed: float
    evaluated_simulated_at: datetime | None
    valid_until_simulated_at: datetime | None
    rules_sha256: str


class AutopilotPolicy(StrictInput):
    policy_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    currency: Literal["USD"] = "USD"
    min_profit_dollars: float = Field(ge=0)
    min_margin_percent: float = Field(ge=0, lt=100)
    max_bid_dollars: float = Field(gt=0)
    max_deadhead_miles: float = Field(ge=0)
    max_input_age_seconds: InputAgePolicy
    max_decision_age_seconds: float = Field(gt=0)
    prediction_timeout_seconds: float = Field(gt=0, le=300)
    max_reference_deviation_percent: float = Field(ge=0)
    fallback_mode: Literal["review", "baseline"] = "review"
    unseen_category_action: Literal["review", "baseline"] = "review"
    baseline_id: str | None = Field(default=None, min_length=1)
    baseline_version: str | None = Field(default=None, min_length=1)
    min_lane_samples: int | None = Field(default=None, ge=1)


class ProjectedPosition(TimestampInput):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    computed_at: AwareDatetime | None = None


class OperationalChecks(StrictInput):
    unit_available: bool | None = None
    commitments_clear: bool | None = None
    capacity_compatible: bool | None = None
    equipment_compatible: bool | None = None
    location_verified: bool | None = None
    timing_feasible: bool | None = None
    order_open: bool | None = None


class BackendContext(TimestampInput):
    shipment_id: str = Field(min_length=1, max_length=128)
    unit_id: str = Field(min_length=1, max_length=128)
    snapshot_version: str = Field(min_length=1, max_length=128)
    projected_position: ProjectedPosition | None = None
    loaded_cost_dollars: float | None = Field(default=None, ge=0)
    deadhead_cost_dollars: float | None = Field(default=None, ge=0)
    deadhead_miles: float | None = Field(default=None, ge=0)
    costs_observed_at: AwareDatetime | None = None
    unit_observed_at: AwareDatetime | None = None
    order_observed_at: AwareDatetime | None = None
    order_expires_at: AwareDatetime | None = None
    clock_id: str | None = Field(default=None, min_length=1, exclude_if=lambda v: v is None)
    clock_revision: str | None = Field(default=None, min_length=1, exclude_if=lambda v: v is None)
    operational_expires_at: AwareDatetime | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    checks: OperationalChecks = Field(default_factory=OperationalChecks)


class AutopilotControl(StrictInput):
    autopilot_enabled: bool | None = None
    kill_switch_active: bool | None = None


class AutopilotRequest(StrictInput):
    mode: Literal["live"] = "live"
    shipment_id: str = Field(min_length=1, max_length=128)
    unit_id: str = Field(min_length=1, max_length=128)
    shipment: AutopilotShipment
    backend_context: BackendContext | None = None
    policy: AutopilotPolicy | None = None
    control: AutopilotControl = Field(default_factory=AutopilotControl)
    clock: SimulationClock | None = Field(default=None, exclude_if=lambda v: v is None)


class DecisionReason(BaseModel):
    code: str
    severity: Literal["SKIP", "REVIEW"]
    field: str
    details: dict[str, float | str]


class AutopilotResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    api_version: Literal["v1"] = "v1"
    mode: Literal["live"] = "live"
    decision_id: str
    decision: Literal["BID", "SKIP", "REVIEW"]
    reason_codes: list[str]
    reasons: list[DecisionReason]
    shipment_id: str
    unit_id: str
    policy_version: str | None
    snapshot_version: str | None
    context_fingerprint: str
    evaluated_at: datetime
    valid_until: datetime | None
    clock: ClockEvaluation | None = None
    candidate_bid_dollars: float | None
    candidate_bid_rpm: float | None
    loaded_cost_dollars: float | None
    deadhead_cost_dollars: float | None
    total_estimated_cost_dollars: float | None
    minimum_bid_dollars: float | None
    estimated_profit_dollars: float | None
    estimated_margin_percent: float | None
    reference_deviation_percent: float | None
    advisory: BidPredictionResponse | BaselineFallbackResponse | UnavailableAdvice | None
    currency: Literal["USD"] = "USD"
    submission_enabled: Literal[False] = False
    eligible_for_live_submission: Literal[False] = False
    requires_review_before_live_use: Literal[True] = True
    live_blockers: list[str]


def utc_now():
    return datetime.now(timezone.utc)


def create_autopilot_endpoint(
    predict_bid, timeout_fallback=lambda shipment: None, *, emit_decision_log=True
):
    async def evaluate_autopilot(request: AutopilotRequest) -> AutopilotResponse:
        advisory, prediction_error = None, None
        disabled = (
            request.shipment.order_type != "open_bid"
            or request.control.autopilot_enabled is False
            or request.control.kill_switch_active is True
        )
        if request.policy is not None and not disabled:
            parent = current_log.get()

            def predict():
                # Timed-out worker results must never mutate the finished parent request log.
                child = PricingLog(endpoint="/api/v1/autopilot/evaluate")
                if parent is not None:
                    child.request_id = parent.request_id
                token = current_log.set(child)
                try:
                    return predict_bid(
                        BidPredictionRequest.model_validate(request.shipment.model_dump())
                    )
                finally:
                    current_log.reset(token)

            try:
                advisory = await asyncio.wait_for(
                    asyncio.to_thread(predict), timeout=request.policy.prediction_timeout_seconds
                )
                note_prediction(advisory)
            except TimeoutError:
                prediction_error = "PREDICTION_TIMEOUT"
                note_error({"code": prediction_error})
                advisory = UnavailableAdvice(
                    http_status=503, error={"code": prediction_error, "action": "REVIEW"}
                )
                if request.policy.fallback_mode == "baseline":
                    fallback = timeout_fallback(request.shipment)
                    if fallback is not None:
                        advisory, prediction_error = fallback, None
                        if parent is not None:
                            parent.error_code = None
                        note_prediction(advisory)
            except HTTPException as error:
                note_error(error.detail)
                code = error.detail.get("code")
                prediction_error = (
                    "UNSUPPORTED_MODEL_INPUT"
                    if code == "UNSEEN_CATEGORY"
                    else "PREDICTION_UNAVAILABLE"
                )
                advisory = UnavailableAdvice(http_status=error.status_code, error=error.detail)
        candidate = (
            advisory.model_dump()
            if advisory is not None and not isinstance(advisory, UnavailableAdvice)
            else None
        )
        result = evaluate(request.model_dump(), candidate, prediction_error, utc_now())
        live_blockers = ["LIVE_AUTHORIZATION_REQUIRED"]
        response = AutopilotResponse(
            **result,
            decision_id=uuid4().hex,
            shipment_id=request.shipment_id,
            unit_id=request.unit_id,
            policy_version=request.policy.policy_version if request.policy else None,
            snapshot_version=request.backend_context.snapshot_version
            if request.backend_context
            else None,
            advisory=advisory,
            live_blockers=live_blockers,
        )
        record = current_log.get()
        if emit_decision_log:
            write_event(
                logging.INFO,
                {
                    "event": "autopilot_decision",
                    "request_id": record.request_id if record else None,
                    "decision_id": response.decision_id,
                    "decision": response.decision,
                    "reason_codes": response.reason_codes,
                    "policy_version": identifier(response.policy_version),
                    "mode": "live",
                    "submission_enabled": False,
                },
            )
        return response

    return evaluate_autopilot
