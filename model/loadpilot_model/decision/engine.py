"""Evaluate one proposed price; never adjust a candidate or execute a bid."""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

from loadpilot_model.decision.clock import DecisionClock

ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

CHECKS = {
    "unit_available": "UNIT_UNAVAILABLE",
    "commitments_clear": "UNIT_COMMITTED",
    "capacity_compatible": "CAPACITY_EXCEEDED",
    "equipment_compatible": "EQUIPMENT_INCOMPATIBLE",
    "location_verified": "LOCATION_INFEASIBLE",
    "timing_feasible": "TIMING_INFEASIBLE",
    "order_open": "ORDER_CLOSED",
}


def fingerprint(request: dict) -> str:
    def serialize(value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        raise TypeError("Unsupported context value")

    return hashlib.sha256(
        json.dumps(request, sort_keys=True, default=serialize, allow_nan=False).encode()
    ).hexdigest()


def evaluate(
    request: dict,
    candidate: dict | None,
    prediction_error: str | None,
    now: datetime,
    *,
    allow_fixed_rate: bool = False,
) -> dict:
    """Trusted adapter supplies validated snapshots; absence always requires review."""
    reasons = []

    def reason(code, severity="REVIEW", field="", details=None):
        reasons.append(dict(code=code, severity=severity, field=field, details=details or {}))

    values = dict(
        candidate_bid_dollars=None,
        candidate_bid_rpm=None,
        loaded_cost_dollars=None,
        deadhead_cost_dollars=None,
        total_estimated_cost_dollars=None,
        minimum_bid_dollars=None,
        estimated_profit_dollars=None,
        estimated_margin_percent=None,
        reference_deviation_percent=None,
    )
    expiry = []

    def deadline(stamp, seconds):
        try:
            return stamp + timedelta(seconds=seconds)
        except OverflowError:
            return datetime.max.replace(tzinfo=timezone.utc)

    policy, context = request.get("policy"), request.get("backend_context")
    clock = DecisionClock(request.get("clock"), now)
    clock_deadline = clock.snapshot_deadline()
    if clock_deadline is not None:
        expiry.append(clock_deadline)
    fixed_offer = (
        allow_fixed_rate
        and request["shipment"]["order_type"] == "fixed_rate"
        and candidate is not None
        and candidate["pricing_source"] == "offered_rate"
    )
    if request["shipment"]["order_type"] != "open_bid" and not fixed_offer:
        reason("UNSUPPORTED_ORDER_TYPE", "SKIP", "shipment.order_type")
    control = request.get("control") or {}
    for key, code in (
        ("autopilot_enabled", "AUTOPILOT_DISABLED"),
        ("kill_switch_active", "KILL_SWITCH_ACTIVE"),
    ):
        value = control.get(key)
        if value is None:
            reason("CONTROL_STATE_UNKNOWN", field=f"control.{key}")
        elif (key == "autopilot_enabled" and not value) or (key == "kill_switch_active" and value):
            reason(code, "SKIP", f"control.{key}")
    if policy is None:
        reason("INVALID_POLICY", field="policy")
    elif policy["fallback_mode"] == "baseline" and (
        not policy.get("baseline_id")
        or not policy.get("baseline_version")
        or policy.get("min_lane_samples") is None
    ):
        reason("INVALID_POLICY", field="policy", details={"required": "baseline identity/support"})

    if context is None:
        reason("FLEET_DATA_MISSING", field="backend_context")
    else:
        for key in ("shipment_id", "unit_id"):
            if context[key] != request[key]:
                reason("CONTEXT_MISMATCH", field=f"backend_context.{key}")
        checks = context.get("checks") or {}
        if clock.snapshot is not None:
            if context.get("clock_id") != clock.snapshot["clock_id"]:
                reason("CLOCK_ID_MISMATCH", field="backend_context.clock_id")
            if context.get("clock_revision") != clock.snapshot["revision"]:
                reason("CLOCK_REVISION_MISMATCH", field="backend_context.clock_revision")
            operational_end = context.get("operational_expires_at")
            if operational_end is None:
                reason(
                    "OPERATIONAL_EXPIRATION_UNKNOWN", field="backend_context.operational_expires_at"
                )
            elif clock.simulated_now is not None:
                if operational_end <= clock.simulated_now:
                    reason(
                        "OPERATIONAL_WINDOW_EXPIRED", field="backend_context.operational_expires_at"
                    )
                limit = clock.expiry(operational_end)
                if limit is not None:
                    expiry.append(limit)
        elif context.get("operational_expires_at") is not None:
            if context["operational_expires_at"] <= now:
                reason("OPERATIONAL_WINDOW_EXPIRED", field="backend_context.operational_expires_at")
            expiry.append(context["operational_expires_at"])
        for name, code in CHECKS.items():
            if checks.get(name) is False:
                reason(code, "SKIP", f"backend_context.checks.{name}")
            elif checks.get(name) is None:
                reason("OPERATIONAL_CHECK_UNKNOWN", field=f"backend_context.checks.{name}")
        position = context.get("projected_position")
        if position is None:
            reason("PROJECTED_POSITION_MISSING", field="backend_context.projected_position")
        stamps = {
            "unit_position": position.get("computed_at") if position else None,
            "unit_availability": context.get("unit_observed_at"),
            "costs": context.get("costs_observed_at"),
            "order_status": context.get("order_observed_at"),
        }
        for source, stamp in stamps.items():
            if stamp is None:
                reason("TIMESTAMP_MISSING", field=source)
            else:
                age = (now - stamp).total_seconds()
                simulation_limit = clock.input_deadline(stamp)
                if simulation_limit is not None:
                    expiry.append(simulation_limit)
                if age < 0:
                    reason("FUTURE_INPUT_TIMESTAMP", field=source)
                elif policy is not None:
                    limit = policy["max_input_age_seconds"][source]
                    expiry.append(deadline(stamp, limit))
                    if age > limit:
                        reason(
                            "STALE_INPUT",
                            field=source,
                            details={"age_seconds": age, "limit_seconds": limit},
                        )
        expires_at = context.get("order_expires_at")
        if expires_at is None:
            reason("ORDER_EXPIRATION_UNKNOWN", field="backend_context.order_expires_at")
        elif (clock.snapshot is None or clock.simulated_now is not None) and expires_at <= (
            clock.simulated_now if clock.snapshot is not None else now
        ):
            reason("ORDER_EXPIRED", "SKIP", "backend_context.order_expires_at")
        else:
            limit = clock.expiry(expires_at)
            if limit is not None:
                expiry.append(limit)
        deadhead = context.get("deadhead_miles")
        if deadhead is None:
            reason("DEADHEAD_MISSING", field="backend_context.deadhead_miles")
        elif policy is not None and deadhead > policy["max_deadhead_miles"]:
            reason("DEADHEAD_LIMIT_EXCEEDED", "SKIP", "backend_context.deadhead_miles")
        values["loaded_cost_dollars"] = context.get("loaded_cost_dollars")
        values["deadhead_cost_dollars"] = context.get("deadhead_cost_dollars")
        if any(values[key] is None for key in ("loaded_cost_dollars", "deadhead_cost_dollars")):
            reason("INVALID_COST", field="backend_context")

    if prediction_error is not None:
        reason(prediction_error, field="advisory")
    if (
        candidate is not None
        and candidate.get("reference_pricing_source", candidate["pricing_source"]) == "baseline"
    ):
        reference = candidate["baseline_reference"]
        if policy is None or policy["fallback_mode"] != "baseline":
            reason("FALLBACK_NOT_ALLOWED", field="policy.fallback_mode")
        elif (
            policy.get("baseline_id") != reference["baseline_id"]
            or policy.get("baseline_version") != reference["policy_version"]
            or policy.get("min_lane_samples") != reference["min_lane_samples"]
        ):
            reason("FALLBACK_POLICY_MISMATCH", field="policy")
        if candidate.get("trigger") == "UNSUPPORTED_MODEL_INPUT" and (
            policy is None or policy["unseen_category_action"] != "baseline"
        ):
            reason("UNSUPPORTED_MODEL_INPUT", field="policy.unseen_category_action")

    with localcontext() as decimal_context:
        decimal_context.prec = 400
        cent = Decimal("0.01")

        def output(name, value):
            result = float(value)
            if math.isfinite(result):
                values[name] = result
            else:
                reason("ARITHMETIC_UNAVAILABLE", field=name)

        cost = None
        if all(values[key] is not None for key in ("loaded_cost_dollars", "deadhead_cost_dollars")):
            cost = Decimal(str(values["loaded_cost_dollars"])) + Decimal(
                str(values["deadhead_cost_dollars"])
            )
            output("total_estimated_cost_dollars", cost)
        if cost is not None and policy is not None:
            floor = max(
                cost + Decimal(str(policy["min_profit_dollars"])),
                cost / (1 - Decimal(str(policy["min_margin_percent"])) / 100),
            )
            floor = floor.quantize(cent, rounding=ROUND_CEILING)
            output("minimum_bid_dollars", floor)
            if not fixed_offer and floor > Decimal(str(policy["max_bid_dollars"])):
                reason("NO_FEASIBLE_PRICE", "SKIP", "policy.max_bid_dollars")
        if candidate is not None:
            price = Decimal(str(candidate["predicted_bid_dollars"])).quantize(
                cent, rounding=ROUND_HALF_UP
            )
            miles = Decimal(str(request["shipment"]["distance_miles"]))
            expected = Decimal(str(candidate["predicted_bid_rpm"])) * miles
            raw = Decimal(str(candidate["predicted_bid_dollars"]))
            if fixed_offer and (
                request.get("offered_rate_dollars") is None
                or raw != Decimal(str(request["offered_rate_dollars"]))
            ):
                reason("INVALID_CANDIDATE", field="offered_rate_dollars")
            if abs(expected - raw) > max(Decimal("0.000001"), abs(raw) * Decimal("1e-9")):
                reason("INVALID_CANDIDATE", field="advisory")
            if price <= 0:
                reason("INVALID_CANDIDATE", field="advisory.predicted_bid_dollars")
            else:
                output("candidate_bid_dollars", price)
                output("candidate_bid_rpm", price / miles)
                if cost is not None:
                    profit, margin = price - cost, 100 * (price - cost) / price
                    output("estimated_profit_dollars", profit)
                    output("estimated_margin_percent", margin)
                    if policy is not None and (
                        profit < Decimal(str(policy["min_profit_dollars"]))
                        or margin < Decimal(str(policy["min_margin_percent"]))
                    ):
                        reason("BELOW_PROFIT_FLOOR", "SKIP", "advisory.predicted_bid_dollars")
                if (
                    not fixed_offer
                    and policy is not None
                    and price > Decimal(str(policy["max_bid_dollars"]))
                ):
                    reason("ABOVE_MAX_BID", "SKIP", "advisory.predicted_bid_dollars")
                baseline = candidate.get("baseline_reference")
                if baseline is None:
                    reason("PRICE_REFERENCE_UNAVAILABLE", field="advisory")
                    reference = reference_rpm = None
                else:
                    reference = Decimal(str(baseline["predicted_bid_dollars"]))
                    reference_rpm = Decimal(str(baseline["predicted_bid_rpm"]))
                if reference is not None and abs(reference_rpm * miles - reference) > max(
                    Decimal("0.000001"), abs(reference) * Decimal("1e-9")
                ):
                    reason("INVALID_REFERENCE", field="advisory.baseline_reference")
                if reference is not None:
                    deviation = 100 * abs(price - reference) / reference
                    output("reference_deviation_percent", deviation)
                    if (
                        not fixed_offer
                        and policy is not None
                        and deviation > Decimal(str(policy["max_reference_deviation_percent"]))
                    ):
                        reason("REFERENCE_DEVIATION_EXCEEDED", field="advisory.baseline_reference")
    if candidate is None and prediction_error is None and not reasons:
        reason("PREDICTION_UNAVAILABLE", field="advisory")
    for code in dict.fromkeys(clock.issues):
        reason(code, field="clock")
    if clock.snapshot is not None and expiry and min(expiry) <= now and not reasons:
        reason("DECISION_WINDOW_EXPIRED", field="valid_until")
    decision = (
        "SKIP"
        if any(item["severity"] == "SKIP" for item in reasons)
        else "REVIEW"
        if reasons
        else "BID"
    )
    if decision == "BID":
        expiry.append(deadline(now, policy["max_decision_age_seconds"]))
    valid_until = min(expiry) if decision == "BID" else None
    return {
        "decision": decision,
        "reason_codes": list(dict.fromkeys(item["code"] for item in reasons))
        or ["ALL_CHECKS_PASSED"],
        "reasons": reasons,
        "evaluated_at": now,
        "valid_until": valid_until,
        "clock": clock.describe(valid_until),
        "context_fingerprint": fingerprint(request),
        **values,
    }
