"""Batch pricing and conditional profit allocation; never submit or reserve."""

import asyncio
import hashlib
import logging
import math
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from loadpilot_model.api.autopilot import (
    AutopilotControl,
    AutopilotPolicy,
    AutopilotRequest,
    AutopilotResponse,
    AutopilotShipment,
    BackendContext,
    ClockEvaluation,
    SimulationClock,
    StrictInput,
    utc_now,
)
from loadpilot_model.api.pricing import (
    BaselineFallbackResponse,
    BaselineReference,
    BidPredictionResponse,
    SupportWarning,
)
from loadpilot_model.api.telemetry import current_log, write_event
from loadpilot_model.decision.batch import select_assignments
from loadpilot_model.decision.clock import DecisionClock
from loadpilot_model.decision.engine import ENGINE_SHA256, evaluate, fingerprint

ALLOCATOR_SHA256 = hashlib.sha256(
    (Path(__file__).parents[1] / "decision/batch.py").read_bytes()
).hexdigest()
BATCH_ANALYSIS_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class BatchOpportunity(StrictInput):
    shipment_id: str = Field(min_length=1, max_length=128)
    unit_id: str = Field(min_length=1, max_length=128)
    shipment: AutopilotShipment
    backend_context: BackendContext | None = None
    offered_rate_dollars: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def offered_price_semantics(self):
        if self.shipment.order_type == "fixed_rate":
            if self.offered_rate_dollars is None:
                raise ValueError("Fixed-rate orders require offered_rate_dollars (total USD)")
            with localcontext() as context:
                context.prec = 400
                amount = Decimal(str(self.offered_rate_dollars))
                if amount != amount.quantize(Decimal("0.01")):
                    raise ValueError("Offered dollars must use whole USD cents")
        elif self.offered_rate_dollars is not None:
            raise ValueError(
                "Open-bid orders use model proposals; offered_rate_dollars is fixed-rate only"
            )
        return self


class BatchRequest(StrictInput):
    mode: Literal["live"] = "live"
    batch_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    fleet_snapshot_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    strategy: Literal["best_order", "fleet_allocation"] = "best_order"
    opportunities: list[BatchOpportunity] = Field(
        min_length=0,
        max_length=200,
        alias="orders",
        validation_alias=AliasChoices("orders", "opportunities"),
    )
    policy: AutopilotPolicy
    control: AutopilotControl = Field(default_factory=AutopilotControl)
    analysis_timeout_seconds: float = Field(default=10, gt=0, le=60)
    clock: SimulationClock | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def consistent_snapshot(self):
        pairs, shipments, units, order_states = set(), {}, {}, {}

        def merge_known(states, identifier, values, label):
            known = states.setdefault(identifier, {})
            for field, value in values.items():
                if value is None:
                    continue
                if field in known and known[field] != value:
                    raise ValueError(f"Conflicting {label} state within batch")
                known[field] = value

        for item in self.opportunities:
            pair = (item.shipment_id, item.unit_id)
            if pair in pairs:
                raise ValueError("Duplicate shipment/unit opportunity")
            pairs.add(pair)
            context = item.backend_context
            order = (item.shipment.model_dump(), item.offered_rate_dollars)
            if context:
                if context.shipment_id != item.shipment_id or context.unit_id != item.unit_id:
                    raise ValueError("Opportunity context must match shipment/unit IDs")
                if context.snapshot_version != self.fleet_snapshot_version:
                    raise ValueError("All contexts must use the supplied fleet snapshot version")
                merge_known(
                    order_states,
                    item.shipment_id,
                    {
                        "observed_at": context.order_observed_at,
                        "expires_at": context.order_expires_at,
                        "open": context.checks.order_open,
                    },
                    "shipment",
                )
                position = context.projected_position
                merge_known(
                    units,
                    item.unit_id,
                    {
                        "latitude": position.latitude if position else None,
                        "longitude": position.longitude if position else None,
                        "position_at": position.computed_at if position else None,
                        "observed_at": context.unit_observed_at,
                        "available": context.checks.unit_available,
                        "commitments_clear": context.checks.commitments_clear,
                    },
                    "unit",
                )
            if item.shipment_id in shipments and shipments[item.shipment_id] != order:
                raise ValueError("Conflicting shipment state within batch")
            shipments[item.shipment_id] = order
        if len(shipments) > 50 or len({item.unit_id for item in self.opportunities}) > 50:
            raise ValueError("A batch supports at most 50 shipments and 50 units")
        return self


class BatchStatistics(BaseModel):
    unique_order_count: int
    fixed_rate_order_count: int
    available_offer_count: int
    excluded_offer_count: int
    average_offered_rpm: float | None
    loaded_mile_weighted_offered_rpm: float | None
    comparable_model_count: int
    comparable_average_offered_rpm: float | None
    average_model_reference_rpm: float | None
    scope: Literal["unique_fresh_open_fixed_rate_orders"] = "unique_fresh_open_fixed_rate_orders"


class OrderPriceComparison(BaseModel):
    revenue_source: Literal["offered_rate", "model", "baseline", "unavailable"]
    offered_rate_dollars: float | None
    offered_rpm: float | None
    included_in_batch_average: bool
    batch_average_offered_rpm: float | None
    difference_from_batch_average_rpm: float | None
    model_reference_bid_dollars: float | None
    model_reference_rpm: float | None
    difference_from_model_rpm: float | None
    difference_from_model_percent: float | None
    model_reference_projected_profit_dollars: float | None
    model_reference_scope: Literal["open_bid_price_model"] = "open_bid_price_model"
    reference_warnings: list[str]


class BatchOrderResponse(AutopilotResponse):
    decision: Literal["BID", "SKIP"]


class BatchEvaluation(BaseModel):
    selected: bool
    selection_reason: Literal["SELECTED", "INELIGIBLE", "NOT_SELECTED_BY_ALLOCATION"]
    evaluation: BatchOrderResponse
    requires_review: bool
    comparison: OrderPriceComparison


class RankedOrder(BaseModel):
    rank: int
    shipment_id: str
    unit_id: str
    suggested_bid_dollars: float
    suggested_bid_rpm: float
    total_estimated_cost_dollars: float
    projected_profit_dollars: float
    projected_margin_percent: float
    pricing_source: Literal["model", "baseline", "offered_rate"]
    order_type: Literal["open_bid", "fixed_rate"]
    comparison: OrderPriceComparison
    historical_reference: BaselineReference
    warnings: list[SupportWarning]


class BatchResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    api_version: Literal["v1"] = "v1"
    mode: Literal["live"] = "live"
    batch_id: str
    analysis_id: str
    fleet_snapshot_version: str
    context_fingerprint: str
    evaluated_at: datetime
    valid_until: datetime | None
    clock: ClockEvaluation | None = None
    decision: Literal["BID", "SKIP"]
    analysis_status: Literal["complete", "partial", "unavailable"]
    batch_statistics: BatchStatistics
    strategy: Literal["best_order", "fleet_allocation"]
    recommended_order: RankedOrder | None
    order_ranking: list[RankedOrder]
    reason_codes: list[str]
    evaluations: list[BatchEvaluation]
    selected_count: int
    total_proposed_revenue_dollars: float | None
    total_estimated_cost_dollars: float | None
    total_projected_profit_dollars: float | None
    projected_margin_percent: float | None
    allocation_optimal_for_eligible_pairs: bool
    allocation_scope: Literal["single_best_order", "one_order_per_unit_per_batch"]
    profit_basis: Literal["conditional_on_all_selected_orders_awarded_at_proposed_prices"] = (
        "conditional_on_all_selected_orders_awarded_at_proposed_prices"
    )
    currency: Literal["USD"] = "USD"
    engine_sha256: str = ENGINE_SHA256
    allocator_sha256: str = ALLOCATOR_SHA256
    submission_enabled: Literal[False] = False
    reservations_created: Literal[False] = False
    live_ready: Literal[False] = False
    requires_review_before_live_use: Literal[True] = True
    journal_recorded: Literal[False] = False


def _finite(value):
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _available_offer(item, now, policy, clock=None):
    context = item.backend_context
    state = DecisionClock(clock, now)
    domain_now = state.simulated_now if clock is not None else now
    if state.issues or domain_now is None:
        return False
    if clock is not None and (
        context is None
        or context.clock_revision != clock["revision"]
        or context.clock_id != clock["clock_id"]
    ):
        return False
    if clock is not None and context.order_observed_at is not None:
        state.input_deadline(context.order_observed_at)
        if state.issues:
            return False
    return (
        item.offered_rate_dollars is not None
        and context is not None
        and context.checks.order_open is True
        and context.order_expires_at is not None
        and context.order_expires_at > domain_now
        and context.order_observed_at is not None
        and 0
        <= (now - context.order_observed_at).total_seconds()
        <= policy.max_input_age_seconds.order_status
    )


def _statistics(first, predictions, now, policy, clock=None):
    offers, miles, amounts, matched_offers, model_rpms = [], [], [], [], []
    with localcontext() as context:
        context.prec = 400
        for shipment_id, item in first.items():
            if not _available_offer(item, now, policy, clock):
                continue
            distance = Decimal(str(item.shipment.distance_miles))
            amount = Decimal(str(item.offered_rate_dollars))
            rpm = amount / distance
            if _finite(rpm) is None:
                continue
            offers.append(rpm)
            miles.append(distance)
            amounts.append(amount)
            original = predictions.get(shipment_id)
            if original and isinstance(
                original.advisory, (BidPredictionResponse, BaselineFallbackResponse)
            ):
                matched_offers.append(rpm)
                model_rpms.append(Decimal(str(original.advisory.predicted_bid_rpm)))

        def average(values):
            return _finite(sum(values) / len(values)) if values else None

        fixed_count = sum(item.shipment.order_type == "fixed_rate" for item in first.values())
        return BatchStatistics(
            unique_order_count=len(first),
            fixed_rate_order_count=fixed_count,
            available_offer_count=len(offers),
            excluded_offer_count=fixed_count - len(offers),
            average_offered_rpm=average(offers),
            loaded_mile_weighted_offered_rpm=_finite(sum(amounts) / sum(miles)) if miles else None,
            comparable_model_count=len(model_rpms),
            comparable_average_offered_rpm=average(matched_offers),
            average_model_reference_rpm=average(model_rpms),
        )


def _comparison(item, original, response, statistics, now, policy, clock=None):
    advice = original.advisory
    supported = isinstance(advice, (BidPredictionResponse, BaselineFallbackResponse))
    with localcontext() as context:
        context.prec = 400
        offer = (
            Decimal(str(item.offered_rate_dollars))
            if item.offered_rate_dollars is not None
            else None
        )
        rpm = offer / Decimal(str(item.shipment.distance_miles)) if offer is not None else None
        model_rpm = Decimal(str(advice.predicted_bid_rpm)) if supported else None
        mean = statistics.average_offered_rpm
        cost = response.total_estimated_cost_dollars
        return OrderPriceComparison(
            reference_warnings=["FIXED_RATE_REFERENCE_NOT_VALIDATED"]
            if item.shipment.order_type == "fixed_rate"
            else [],
            revenue_source="offered_rate"
            if offer is not None
            else advice.pricing_source
            if supported
            else "unavailable",
            offered_rate_dollars=item.offered_rate_dollars,
            offered_rpm=_finite(rpm),
            included_in_batch_average=bool(
                _available_offer(item, now, policy, clock) and _finite(rpm) is not None
            ),
            batch_average_offered_rpm=mean,
            difference_from_batch_average_rpm=_finite(rpm - Decimal(str(mean)))
            if rpm is not None and mean is not None
            else None,
            model_reference_bid_dollars=advice.predicted_bid_dollars if supported else None,
            model_reference_rpm=_finite(model_rpm),
            difference_from_model_rpm=_finite(rpm - model_rpm)
            if rpm is not None and model_rpm is not None
            else None,
            difference_from_model_percent=_finite(100 * (rpm - model_rpm) / model_rpm)
            if rpm is not None and model_rpm is not None
            else None,
            model_reference_projected_profit_dollars=_finite(
                Decimal(str(advice.predicted_bid_dollars)) - Decimal(str(cost))
            )
            if supported and cost is not None
            else None,
        )


def create_batch_endpoint(evaluate_autopilot):
    async def analyze_batch(body: BatchRequest) -> BatchResponse:
        def inputs(item):
            return AutopilotRequest(
                shipment_id=item.shipment_id,
                unit_id=item.unit_id,
                shipment=item.shipment,
                backend_context=item.backend_context,
                policy=body.policy,
                control=body.control,
                clock=body.clock,
            )

        # Pricing depends on shipment features, not the selected truck. Predict
        # once per shipment, then apply pair-specific fleet/cost rules below.
        first = {}
        for item in body.opportunities:
            first.setdefault(item.shipment_id, item)
        semaphore = asyncio.Semaphore(4)

        async def predict(item):
            async with semaphore:
                pricing_input = inputs(item)
                if item.shipment.order_type == "fixed_rate":
                    # Compare against open-bid history without passing the offer
                    # or any truck economics into model features.
                    pricing_input = pricing_input.model_copy(
                        update={
                            "shipment": item.shipment.model_copy(update={"order_type": "open_bid"})
                        }
                    )
                return await evaluate_autopilot(pricing_input)

        try:
            predictions = await asyncio.wait_for(
                asyncio.gather(*(predict(item) for item in first.values())),
                timeout=body.analysis_timeout_seconds,
            )
        except TimeoutError:
            predictions = None
        now = utc_now()
        clock_snapshot = body.clock.model_dump() if body.clock else None
        clock_state = DecisionClock(clock_snapshot, now)
        by_shipment = dict(zip(first, predictions, strict=True)) if predictions is not None else {}
        # A missing context on one truck pair must not hide an otherwise known
        # current offer for the same order from batch price statistics.
        for item in body.opportunities:
            if _available_offer(item, now, body.policy, clock_snapshot) and not _available_offer(
                first[item.shipment_id], now, body.policy, clock_snapshot
            ):
                first[item.shipment_id] = item
        statistics = _statistics(first, by_shipment, now, body.policy, clock_snapshot)
        base = dict(
            batch_id=body.batch_id,
            analysis_id=uuid4().hex,
            fleet_snapshot_version=body.fleet_snapshot_version,
            context_fingerprint=fingerprint(body.model_dump()),
            evaluated_at=now,
            clock=clock_state.describe(),
            batch_statistics=statistics,
            strategy=body.strategy,
            allocation_scope="single_best_order"
            if body.strategy == "best_order"
            else "one_order_per_unit_per_batch",
        )

        def failed(code, evaluations=(), comparisons=(), review_flags=()):
            return BatchResponse(
                **base,
                valid_until=None,
                decision="SKIP",
                analysis_status="unavailable",
                recommended_order=None,
                order_ranking=[],
                reason_codes=[code],
                evaluations=[
                    BatchEvaluation(
                        selected=False,
                        selection_reason="INELIGIBLE"
                        if row.decision != "BID"
                        else "NOT_SELECTED_BY_ALLOCATION",
                        evaluation=row,
                        requires_review=review_flags[index],
                        comparison=comparisons[index],
                    )
                    for index, row in enumerate(evaluations)
                ],
                selected_count=0,
                total_proposed_revenue_dollars=None,
                total_estimated_cost_dollars=None,
                total_projected_profit_dollars=None,
                projected_margin_percent=None,
                allocation_optimal_for_eligible_pairs=False,
            )

        if predictions is None:
            return failed("BATCH_ANALYSIS_TIMEOUT")
        evaluations, comparisons, review_flags, opportunities, indices = [], [], [], [], []
        for item in body.opportunities:
            original = by_shipment[item.shipment_id]
            candidate = (
                original.advisory.model_dump()
                if isinstance(original.advisory, (BidPredictionResponse, BaselineFallbackResponse))
                else None
            )
            error = next(
                (
                    code
                    for code in original.reason_codes
                    if code
                    in {"PREDICTION_UNAVAILABLE", "PREDICTION_TIMEOUT", "UNSUPPORTED_MODEL_INPUT"}
                ),
                None,
            )
            evaluation_input = inputs(item).model_dump()
            if item.offered_rate_dollars is not None:
                with localcontext() as context:
                    context.prec = 400
                    rpm = Decimal(str(item.offered_rate_dollars)) / Decimal(
                        str(item.shipment.distance_miles)
                    )
                candidate = {
                    **(candidate or {}),
                    "reference_pricing_source": candidate["pricing_source"] if candidate else None,
                    "pricing_source": "offered_rate",
                    "predicted_bid_dollars": item.offered_rate_dollars,
                    "predicted_bid_rpm": _finite(rpm) or 0,
                }
                evaluation_input["offered_rate_dollars"] = item.offered_rate_dollars
            # A common post-inference time excludes orders/costs that expired
            # while the batch was being priced.
            result = evaluate(evaluation_input, candidate, error, now, allow_fixed_rate=True)
            review_flags.append(any(reason["severity"] == "REVIEW" for reason in result["reasons"]))
            if result["decision"] == "REVIEW":
                result["decision"] = "SKIP"
            response = BatchOrderResponse(
                **result,
                decision_id=uuid4().hex,
                shipment_id=item.shipment_id,
                unit_id=item.unit_id,
                policy_version=body.policy.policy_version,
                snapshot_version=item.backend_context.snapshot_version
                if item.backend_context
                else None,
                advisory=original.advisory,
                live_blockers=original.live_blockers,
            )
            evaluations.append(response)
            comparisons.append(
                _comparison(item, original, response, statistics, now, body.policy, clock_snapshot)
            )
            if response.decision == "BID":
                indices.append(len(evaluations) - 1)
                with localcontext() as context:
                    context.prec = 400
                    profit = Decimal(str(response.candidate_bid_dollars)) - Decimal(
                        str(response.total_estimated_cost_dollars)
                    )
                opportunities.append(
                    (
                        item.shipment_id,
                        item.unit_id,
                        profit,
                    )
                )
        # Rank each order by its most profitable eligible truck. The historical
        # pricing model supplies revenue; supplied current costs supply economics.
        ranked_indices, seen = [], set()
        for index in sorted(
            range(len(opportunities)),
            key=lambda i: (-opportunities[i][2], opportunities[i][0], opportunities[i][1]),
        ):
            shipment, _, profit = opportunities[index]
            if profit > 0 and shipment not in seen:
                ranked_indices.append(indices[index])
                seen.add(shipment)
        ranking = [
            RankedOrder(
                rank=rank,
                shipment_id=evaluations[index].shipment_id,
                unit_id=evaluations[index].unit_id,
                suggested_bid_dollars=evaluations[index].candidate_bid_dollars,
                suggested_bid_rpm=evaluations[index].candidate_bid_rpm,
                total_estimated_cost_dollars=evaluations[index].total_estimated_cost_dollars,
                projected_profit_dollars=evaluations[index].estimated_profit_dollars,
                projected_margin_percent=evaluations[index].estimated_margin_percent,
                pricing_source=comparisons[index].revenue_source,
                order_type=body.opportunities[index].shipment.order_type,
                comparison=comparisons[index],
                historical_reference=evaluations[index].advisory.baseline_reference,
                warnings=evaluations[index].advisory.warnings,
            )
            for rank, index in enumerate(ranked_indices, 1)
        ]
        if body.strategy == "best_order":
            selected = set(ranked_indices[:1])
        else:
            selected = {
                indices[index]
                for index in await asyncio.to_thread(select_assignments, opportunities)
            }
        chosen = [evaluations[index] for index in sorted(selected)]
        if any(row.valid_until is None or row.valid_until <= utc_now() for row in chosen):
            return failed("BATCH_SELECTION_EXPIRED", evaluations, comparisons, review_flags)
        with localcontext() as context:
            context.prec = 400
            revenue = sum((Decimal(str(row.candidate_bid_dollars)) for row in chosen), Decimal(0))
            costs = sum(
                (Decimal(str(row.total_estimated_cost_dollars)) for row in chosen), Decimal(0)
            )
            profit = revenue - costs
            margin = float(100 * profit / revenue) if revenue else None
        if not all(math.isfinite(float(value)) for value in (revenue, costs, profit)):
            return failed("BATCH_ARITHMETIC_UNAVAILABLE", evaluations, comparisons, review_flags)
        record = current_log.get()
        for index, row in enumerate(evaluations):
            write_event(
                logging.INFO,
                {
                    "event": "batch_order_decision",
                    "mode": "live",
                    "request_id": record.request_id if record else None,
                    "analysis_id": base["analysis_id"],
                    "decision_id": row.decision_id,
                    "decision": row.decision,
                    "reason_codes": row.reason_codes,
                    "selected": index in selected,
                    "requires_review": review_flags[index],
                    "revenue_source": comparisons[index].revenue_source,
                    "submission_enabled": False,
                },
            )
        return BatchResponse(
            **{
                **base,
                "clock": clock_state.describe(
                    min(row.valid_until for row in chosen) if chosen else None
                ),
            },
            valid_until=min(row.valid_until for row in chosen) if chosen else None,
            decision="BID" if chosen else "SKIP",
            analysis_status="partial" if any(review_flags) else "complete",
            reason_codes=[
                (
                    "MOST_PROFITABLE_ORDER_SELECTED"
                    if body.strategy == "best_order"
                    else "BATCH_ALLOCATION_SELECTED"
                )
                if chosen
                else "EMPTY_BATCH"
                if not first
                else "BATCH_NO_ELIGIBLE_POSITIVE_PROFIT_ALLOCATION"
            ],
            recommended_order=ranking[0] if ranking else None,
            order_ranking=ranking,
            evaluations=[
                BatchEvaluation(
                    selected=index in selected,
                    selection_reason="SELECTED"
                    if index in selected
                    else ("INELIGIBLE" if row.decision != "BID" else "NOT_SELECTED_BY_ALLOCATION"),
                    evaluation=row,
                    comparison=comparisons[index],
                    requires_review=review_flags[index],
                )
                for index, row in enumerate(evaluations)
            ],
            selected_count=len(chosen),
            total_proposed_revenue_dollars=float(revenue),
            total_estimated_cost_dollars=float(costs),
            total_projected_profit_dollars=float(profit),
            projected_margin_percent=margin,
            allocation_optimal_for_eligible_pairs=True,
        )

    return analyze_batch
