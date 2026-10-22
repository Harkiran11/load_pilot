"""Intersect descriptive historical prices with caller-supplied profitability policy."""

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from loadpilot_model.api.telemetry import diagnostic


class HistoricalBand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    policy_version: Literal["historical-range-v1"]
    artifact_sha256: str
    source: Literal["lane_distance_band", "lane", "distance_band", "global"]
    sample_count: int = Field(ge=1)
    minimum_samples: int = Field(ge=1)
    distance_band: str
    training_date_min: str
    training_date_max: str
    fit_partition: Literal["train"] = "train"
    lower_quantile: Literal[0.25]
    upper_quantile: Literal[0.75]
    lower_rpm: float = Field(gt=0)
    upper_rpm: float = Field(gt=0)
    lower_bid_dollars: float = Field(gt=0)
    upper_bid_dollars: float = Field(gt=0)


class RecommendedRange(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    status: Literal["available", "no_profitable_range", "unavailable"]
    reason: str
    historical_reference: HistoricalBand | None = None
    minimum_profitable_bid_dollars: float | None = Field(default=None, gt=0)
    lower_bid_dollars: float | None = Field(default=None, gt=0)
    upper_bid_dollars: float | None = Field(default=None, gt=0)
    currency: Literal["USD"] = "USD"
    based_on_backend_estimated_cost: Literal[True] = True
    calibrated_prediction_interval: Literal[False] = False
    requires_review: Literal[True] = True


def recommend_range(history, lane, miles, cost, minimum_profit, minimum_margin, maximum_bid):
    result = RecommendedRange(status="unavailable", reason="HISTORY_UNAVAILABLE")
    if history is not None:
        try:
            result.historical_reference = HistoricalBand.model_validate(
                history.describe(lane, miles)
            )
        except Exception as error:
            diagnostic("HISTORICAL_RANGE_FAILED", error)
            result.reason = "HISTORY_ERROR"
    if cost is None:
        result.reason = "COST_UNAVAILABLE"
        return result
    if minimum_profit is None or minimum_margin is None:
        result.reason = "PROFITABILITY_POLICY_REQUIRED"
        return result
    # Decimal arithmetic and outward constraints ensure cent-rounded bids meet the floor.
    with localcontext() as context:
        context.prec = 400
        cent = Decimal("0.01")
        cost_value = Decimal(str(cost))
        floor = max(
            cost_value + cent,
            cost_value + Decimal(str(minimum_profit)),
            cost_value / (1 - Decimal(str(minimum_margin)) / 100),
        )
        floor = floor.quantize(cent, rounding=ROUND_CEILING)
        if not math.isfinite(float(floor)):
            result.reason = "AMOUNT_OUT_OF_RANGE"
            return result
        result.minimum_profitable_bid_dollars = float(floor)
        reference = result.historical_reference
        if reference is None:
            return result
        lower = max(
            floor, Decimal(str(reference.lower_bid_dollars)).quantize(cent, rounding=ROUND_CEILING)
        )
        upper = Decimal(str(reference.upper_bid_dollars)).quantize(cent, rounding=ROUND_FLOOR)
        if maximum_bid is not None:
            upper = min(upper, Decimal(str(maximum_bid)).quantize(cent, rounding=ROUND_FLOOR))
        if lower > upper:
            result.status = "no_profitable_range"
            result.reason = (
                "MAXIMUM_BID_CONFLICT"
                if maximum_bid is not None and Decimal(str(maximum_bid)) < lower
                else "PROFITABILITY_CONFLICT"
            )
            return result
        result.status, result.reason = "available", "HISTORY_SUPPORTED"
        result.lower_bid_dollars, result.upper_bid_dollars = float(lower), float(upper)
        return result
