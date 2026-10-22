"""Versioned advisory pricing router for the existing FastAPI application."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from loadpilot_model.api.telemetry import (
    PricingLog,
    configure_logging,
    current_log,
    diagnostic,
    finish,
    identifier,
    note_error,
    note_prediction,
    write_event,
)
from loadpilot_model.features.schema import MISSING_FEATURE_TOKENS
from loadpilot_model.inference.errors import PredictionInputError
from loadpilot_model.inference.fallback import BaselineFallback, FallbackSettings


def input_error(error: dict) -> dict:
    location = error["loc"]
    field = ".".join(str(part) for part in location if part != "body") or "body"
    kind = error["type"]
    if kind in {"missing", "missing_category"} or error.get("input", object()) is None:
        code, message = "MISSING_REQUIRED_FIELD", "A nonmissing value is required."
    elif kind == "json_invalid":
        code, message = "INVALID_JSON", "Request body must be valid JSON."
    elif kind == "extra_forbidden":
        code, message = "UNEXPECTED_FIELD", "Field is not part of the pricing contract."
    elif kind == "literal_error":
        if "units" in location:
            code, message = (
                "UNSUPPORTED_UNIT",
                "Use miles, pounds, degrees and USD; no conversion is applied.",
            )
        elif "order_type" in location:
            code, message = "UNSUPPORTED_ORDER_TYPE", "Pricing requires an open_bid order."
        else:
            code, message = "INVALID_VALUE", "Use the documented field value."
    elif kind == "finite_number":
        code, message = "NONFINITE_NUMBER", "Number must be finite."
    elif kind in {"greater_than", "greater_than_equal", "less_than_equal", "less_than"}:
        code, message = "OUT_OF_RANGE", "Value is outside the documented field range."
    elif field.endswith("_at") and kind in {
        "datetime_type",
        "datetime_parsing",
        "datetime_from_date_parsing",
        "timezone_aware",
        "value_error",
    }:
        code, message = "INVALID_TIMESTAMP", "Use an ISO 8601 timestamp with a timezone offset."
    else:
        code, message = "INVALID_TYPE", "Use the documented JSON type; strings are not numbers."
    return {"field": field, "code": code, "message": message}


class PricingRoute(APIRoute):
    """Scope structured errors to pricing without changing backend error handlers."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            record = PricingLog(
                endpoint=(
                    self.path
                    if self.path
                    in {
                        "/api/v1/predict-bid",
                        "/api/v1/manual-mode/evaluate",
                        "/api/v1/autopilot/evaluate",
                        "/api/v1/autopilot/analyze-batch",
                        "/api/v1/autopilot/live/evaluate",
                        "/api/v1/autopilot/live/analyze-batch",
                    }
                    else "/api/v1/predict-bid"
                )
            )
            token = current_log.set(record)
            try:
                try:
                    response = await original(request)
                except RequestValidationError as error:
                    raise HTTPException(
                        422,
                        detail={
                            "code": "INVALID_REQUEST",
                            "action": "REVIEW",
                            "errors": [input_error(item) for item in error.errors()],
                        },
                    ) from error
                except ResponseValidationError as error:
                    diagnostic("INVALID_RESPONSE", error)
                    raise HTTPException(
                        503, detail={"code": "MODEL_ERROR", "action": "REVIEW"}
                    ) from error
                record.status = response.status_code
                response.headers["X-Prediction-Id"] = record.request_id
                return response
            except HTTPException as error:
                record.status = error.status_code
                note_error(error.detail)
                error.headers = {**(error.headers or {}), "X-Prediction-Id": record.request_id}
                raise
            except asyncio.CancelledError:
                record.status, record.error_code = 499, "REQUEST_CANCELLED"
                raise
            except Exception as error:
                diagnostic("UNEXPECTED_PRICING_ERROR", error)
                record.status, record.error_code = 503, "MODEL_ERROR"
                raise HTTPException(
                    503,
                    detail={"code": "MODEL_ERROR", "action": "REVIEW"},
                    headers={"X-Prediction-Id": record.request_id},
                ) from error
            finally:
                finish(record)
                current_log.reset(token)

        return handler


class BidInputUnits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    distance: Literal["miles"] = "miles"
    weight: Literal["lb"] = "lb"
    coordinates: Literal["degrees"] = "degrees"
    currency: Literal["USD"] = "USD"


class BidPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    order_type: Literal["open_bid"] = "open_bid"
    units: BidInputUnits = Field(default_factory=BidInputUnits)
    distance_miles: float = Field(gt=0, description="Positive loaded distance in miles")
    weight_lbs: float = Field(gt=0, description="Positive shipment weight in pounds")
    origin_latitude: float = Field(ge=-90, le=90)
    origin_longitude: float = Field(ge=-180, le=180)
    destination_latitude: float = Field(ge=-90, le=90)
    destination_longitude: float = Field(ge=-180, le=180)
    lane_id: str = Field(min_length=1)
    origin_state: str = Field(min_length=1)
    destination_state: str = Field(min_length=1)

    @field_validator("lane_id", "origin_state", "destination_state", mode="before")
    @classmethod
    def normalize_category(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if value.lower() in MISSING_FEATURE_TOKENS:
            raise PydanticCustomError("missing_category", "A nonmissing category is required")
        return value.upper()


class BaselineReference(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    baseline_id: str
    policy_version: str
    reference_source: Literal["lane_median", "global_median"]
    reason: str
    predicted_bid_rpm: float = Field(gt=0)
    predicted_bid_dollars: float = Field(gt=0)
    lane_training_row_count: int | None = Field(ge=0)
    min_lane_samples: int | None = Field(ge=1)


class SupportWarning(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    code: str
    message: str
    fields: list[str]
    details: dict[str, float | int | str | None]


class PredictionUncertainty(BaseModel):
    status: Literal["not_calibrated"]
    confidence_level: None
    rpm_interval: None
    dollar_interval: None


class BidPredictionResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    api_version: Literal["v1"]
    model_version: str
    predicted_bid_rpm: float = Field(gt=0)
    predicted_bid_dollars: float = Field(gt=0)
    currency: Literal["USD"]
    distance_basis: Literal["loaded_miles"]
    advisory_only: Literal[True]
    release_status: Literal["unverified"]
    baseline_reference: BaselineReference
    support_policy_version: Literal["pricing-support-v1"]
    warnings: list[SupportWarning]
    uncertainty: PredictionUncertainty
    pricing_source: Literal["model"] = "model"
    requires_review: Literal[True] = True
    submission_enabled: Literal[False] = False
    requires_guardrail_checks: Literal[True] = True


class BaselineFallbackResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    api_version: Literal["v1"]
    model_version: None
    pricing_source: Literal["baseline"]
    status: Literal["fallback_advisory"]
    trigger: str
    predicted_bid_rpm: float = Field(gt=0)
    predicted_bid_dollars: float = Field(gt=0)
    currency: Literal["USD"]
    distance_basis: Literal["loaded_miles"]
    advisory_only: Literal[True]
    submission_enabled: Literal[False]
    requires_guardrail_checks: Literal[True]
    requires_review: Literal[True]
    baseline_reference: BaselineReference
    policy_sha256: str
    baseline_sha256: str
    warnings: list[SupportWarning]
    uncertainty: PredictionUncertainty


class PricingFieldError(BaseModel):
    field: str
    code: str
    message: str


class PricingErrorDetail(BaseModel):
    code: str
    action: Literal["REVIEW"]
    errors: list[PricingFieldError] = Field(default_factory=list)


class PricingErrorResponse(BaseModel):
    detail: PricingErrorDetail


def create_pricing_router(
    bundle_path: Path | None = None,
    range_path: Path | None = None,
) -> APIRouter:
    """Load once per lifespan; model failures leave ordinary backend routes available."""
    predictor = None
    fallback = None
    settings = FallbackSettings()
    invalid_policy = False
    historical_range = None
    configure_logging()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal predictor, fallback, settings, invalid_policy, historical_range
        started = time.perf_counter()
        configured = os.getenv("LOADPILOT_MODEL_BUNDLE") or bundle_path
        history_path = os.getenv("LOADPILOT_HISTORICAL_RANGE_ARTIFACT") or range_path
        if configured and history_path:
            try:
                from loadpilot_model.baselines.historical_range import HistoricalPricingRange

                historical_range = await asyncio.to_thread(
                    HistoricalPricingRange, Path(history_path), Path(configured)
                )
            except Exception as error:
                diagnostic("HISTORICAL_RANGE_INITIALIZATION_FAILED", error)
        try:
            settings = FallbackSettings.from_environment()
        except ValueError as error:
            invalid_policy = True
            diagnostic("INVALID_FALLBACK_POLICY", error)
        if configured and not invalid_policy and settings.mode == "baseline":
            try:
                fallback = await asyncio.to_thread(BaselineFallback, Path(configured))
            except Exception as error:
                diagnostic("FALLBACK_INITIALIZATION_FAILED", error)
        try:
            if configured and not invalid_policy and not settings.model_blocked:
                # Keep optional PyTorch imports out of the backend's ordinary import path.
                from loadpilot_model.inference.predictor import FreightPredictor

                predictor = await asyncio.to_thread(FreightPredictor, Path(configured))
        except Exception as error:
            diagnostic("MODEL_INITIALIZATION_FAILED", error)
            predictor = None
        write_event(
            logging.INFO,
            {
                "event": "pricing_startup",
                "model_loaded": predictor is not None,
                "fallback_loaded": fallback is not None,
                "historical_range_loaded": historical_range is not None,
                "invalid_policy": invalid_policy,
                "fallback_mode": settings.mode,
                "unseen_category_action": settings.unseen_category_action,
                "model_blocked": settings.model_blocked,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        try:
            yield
        finally:
            predictor = None
            fallback = None
            invalid_policy = False
            historical_range = None
            write_event(logging.INFO, {"event": "pricing_shutdown"})

    router = APIRouter(
        prefix="/api/v1", tags=["Advisory pricing"], lifespan=lifespan, route_class=PricingRoute
    )

    @router.post(
        "/predict-bid",
        response_model=BidPredictionResponse | BaselineFallbackResponse,
        responses={422: {"model": PricingErrorResponse}, 503: {"model": PricingErrorResponse}},
    )
    def predict_bid(shipment: BidPredictionRequest):
        attributes = shipment.model_dump(exclude={"units"})

        def failure(trigger, status=503, code=None, errors=None):
            record = current_log.get()
            if record is not None:
                record.trigger = trigger
            if fallback is not None and not invalid_policy:
                try:
                    candidate = fallback.recommend(attributes, trigger, settings)
                    if candidate is not None:
                        result = BaselineFallbackResponse.model_validate(candidate)
                        note_prediction(result)
                        return result
                except Exception as error:
                    diagnostic("FALLBACK_FAILED", error)
            detail = {"code": code or trigger, "action": "REVIEW"}
            if errors is not None:
                detail["errors"] = errors
            raise HTTPException(status, detail=detail)

        if invalid_policy:
            return failure("INVALID_FALLBACK_POLICY")
        if settings.model_blocked:
            return failure("MODEL_UNRELIABLE")
        if predictor is None:
            return failure("MODEL_UNAVAILABLE")
        record = current_log.get()
        metadata = getattr(getattr(predictor, "loaded", None), "metadata", None)
        if record is not None and isinstance(metadata, dict):
            record.attempted_model_version = identifier(metadata.get("model_version"))
        try:
            result = BidPredictionResponse.model_validate(
                predictor.predict(shipment.model_dump(exclude={"order_type", "units"}))
            )
            note_prediction(result)
            return result
        except PredictionInputError as error:
            if error.code == "UNSEEN_CATEGORY":
                return failure("UNSUPPORTED_MODEL_INPUT", 422, error.code, error.errors)
            raise HTTPException(
                422, detail={"code": error.code, "action": "REVIEW", "errors": error.errors}
            ) from error
        except ValidationError as error:
            diagnostic("INVALID_MODEL_OUTPUT", error)
            return failure("INVALID_MODEL_OUTPUT", code="MODEL_ERROR")
        except Exception as error:
            diagnostic("MODEL_INFERENCE_FAILED", error)
            return failure("MODEL_ERROR")

    from loadpilot_model.api.manual import ManualModeResponse, create_manual_endpoint

    router.add_api_route(
        "/manual-mode/evaluate",
        create_manual_endpoint(predict_bid, lambda: historical_range),
        methods=["POST"],
        response_model=ManualModeResponse,
        summary="Compare an authoritative manual bid with advisory pricing",
        responses={422: {"model": PricingErrorResponse}},
    )
    from loadpilot_model.api.autopilot import create_autopilot_endpoint

    def timeout_fallback(shipment):
        if fallback is None or invalid_policy or settings.mode != "baseline":
            return None
        try:
            candidate = fallback.recommend(
                shipment.model_dump(exclude={"units"}), "MODEL_TIMEOUT", settings
            )
            return BaselineFallbackResponse.model_validate(candidate) if candidate else None
        except Exception as error:
            diagnostic("FALLBACK_FAILED", error)
            return None

    evaluate_autopilot = create_autopilot_endpoint(predict_bid, timeout_fallback)
    from loadpilot_model.api.batch import create_batch_endpoint

    batch_pricing = create_autopilot_endpoint(
        predict_bid, timeout_fallback, emit_decision_log=False
    )
    analyze_batch = create_batch_endpoint(batch_pricing)
    from loadpilot_model.api.live import install_live_routes

    install_live_routes(
        router,
        evaluate_autopilot,
        analyze_batch,
        lambda: predictor.loaded.manifest if predictor is not None else None,
    )
    return router
