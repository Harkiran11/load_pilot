"""Manual bid context stays separate from model features and backend economics."""

import math
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from loadpilot_model.api.pricing import (
    BaselineFallbackResponse,
    BidPredictionRequest,
    BidPredictionResponse,
    PricingErrorDetail,
    SupportWarning,
)
from loadpilot_model.api.ranges import RecommendedRange, recommend_range
from loadpilot_model.api.telemetry import note_error


class BackendEconomics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    source: Literal["backend"] = "backend"
    total_estimated_cost_dollars: float | None = Field(ge=0)
    gross_profit_dollars: float | None
    margin_percent: float | None = Field(le=100)


class ManualWarningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    minimum_profit_dollars: float | None = Field(default=None, ge=0)
    minimum_margin_percent: float | None = Field(default=None, ge=0, lt=100)
    maximum_reference_deviation_percent: float | None = Field(default=None, ge=0)
    maximum_bid_dollars: float | None = Field(default=None, gt=0)


class ManualModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    shipment: BidPredictionRequest
    manual_bid_dollars: float = Field(gt=0, description="Authoritative dispatcher-entered USD bid")
    backend_economics: BackendEconomics
    warning_policy: ManualWarningPolicy = Field(default_factory=ManualWarningPolicy)


class UnavailableAdvice(BaseModel):
    status: Literal["unavailable"] = "unavailable"
    http_status: int
    error: PricingErrorDetail


class ManualComparison(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    manual_bid_rpm: float | None
    manual_minus_advisory_dollars: float | None
    absolute_reference_deviation_percent: float | None


class ManualModeResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    mode: Literal["manual"] = "manual"
    manual_bid_dollars: float
    backend_economics: BackendEconomics
    economics_verified: Literal[False] = False
    advisory: BidPredictionResponse | BaselineFallbackResponse | UnavailableAdvice
    comparison: ManualComparison
    warnings: list[SupportWarning]
    recommended_range: RecommendedRange
    advisory_only: Literal[True] = True
    submission_enabled: Literal[False] = False
    requires_guardrail_checks: Literal[True] = True


def finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def create_manual_endpoint(predict_bid, get_history=lambda: None):
    def evaluate_manual(request: ManualModeRequest) -> ManualModeResponse:
        # Only the nested shipment reaches pricing. Economics and entered bid are never features.
        try:
            advisory = predict_bid(request.shipment)
        except HTTPException as error:
            note_error(error.detail)
            advisory = UnavailableAdvice(http_status=error.status_code, error=error.detail)
        bid, economics, policy = (
            request.manual_bid_dollars,
            request.backend_economics,
            request.warning_policy,
        )
        comparison = ManualComparison(
            manual_bid_rpm=finite(bid / request.shipment.distance_miles),
            manual_minus_advisory_dollars=None,
            absolute_reference_deviation_percent=None,
        )
        warnings = []

        def warn(code, message):
            warnings.append(SupportWarning(code=code, message=message, fields=[], details={}))

        if any(
            value is None
            for value in (
                economics.total_estimated_cost_dollars,
                economics.gross_profit_dollars,
                economics.margin_percent,
            )
        ):
            warn(
                "BACKEND_ECONOMICS_INCOMPLETE", "Backend economics are incomplete; review required."
            )
        if (
            policy.minimum_profit_dollars is not None
            and economics.gross_profit_dollars is not None
            and economics.gross_profit_dollars < policy.minimum_profit_dollars
        ):
            warn(
                "BELOW_MINIMUM_PROFIT", "Backend-estimated profit is below the supplied threshold."
            )
        if (
            policy.minimum_margin_percent is not None
            and economics.margin_percent is not None
            and economics.margin_percent < policy.minimum_margin_percent
        ):
            warn(
                "BELOW_MINIMUM_MARGIN", "Backend-estimated margin is below the supplied threshold."
            )
        if not isinstance(advisory, UnavailableAdvice):
            comparison.manual_minus_advisory_dollars = finite(bid - advisory.predicted_bid_dollars)
            deviation = finite(abs(bid / advisory.predicted_bid_dollars - 1) * 100)
            comparison.absolute_reference_deviation_percent = deviation
            baseline_deviation = finite(
                abs(bid / advisory.baseline_reference.predicted_bid_dollars - 1) * 100
            )
            limit = policy.maximum_reference_deviation_percent
            if limit is not None and any(
                value is not None and value > limit for value in (deviation, baseline_deviation)
            ):
                warn(
                    "REFERENCE_DEVIATION", "Manual bid differs beyond the supplied reference limit."
                )
        return ManualModeResponse(
            manual_bid_dollars=bid,
            backend_economics=economics,
            advisory=advisory,
            comparison=comparison,
            warnings=warnings,
            recommended_range=recommend_range(
                get_history(),
                request.shipment.lane_id,
                request.shipment.distance_miles,
                economics.total_estimated_cost_dollars,
                policy.minimum_profit_dollars,
                policy.minimum_margin_percent,
                policy.maximum_bid_dollars,
            ),
        )

    return evaluate_manual
