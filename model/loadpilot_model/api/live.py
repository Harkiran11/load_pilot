"""Authenticated live recommendations; submission and reservations belong to backend."""

import asyncio
import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from loadpilot_model.api.autopilot import (
    AutopilotRequest,
    AutopilotResponse,
    utc_now,
)
from loadpilot_model.api.batch import (
    BatchEvaluation,
    BatchRequest,
    BatchResponse,
)
from loadpilot_model.api.pricing import (
    BaselineFallbackResponse,
    BidPredictionResponse,
    PricingErrorResponse,
)
from loadpilot_model.api.telemetry import current_log, write_event
from loadpilot_model.decision.engine import fingerprint

LIVE_API_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class LiveSingleRequest(AutopilotRequest):
    mode: Literal["live"] = "live"


class LiveBatchRequest(BatchRequest):
    mode: Literal["live"] = "live"


class LiveSingleResponse(AutopilotResponse):
    mode: Literal["live"] = "live"
    eligible_for_live_submission: bool
    requires_review_before_live_use: bool
    authorization_source: Literal["server_configuration"] = "server_configuration"
    model_bundle_id: str | None
    live_request_fingerprint: str
    execution_owner: Literal["backend"] = "backend"
    backend_revalidation_required: Literal[True] = True
    journal_recorded: Literal[False] = False


class LiveBatchOrderResponse(LiveSingleResponse):
    decision: Literal["BID", "SKIP"]


class LiveBatchEvaluation(BatchEvaluation):
    evaluation: LiveBatchOrderResponse


class LiveBatchResponse(BatchResponse):
    mode: Literal["live"] = "live"
    evaluations: list[LiveBatchEvaluation]
    live_ready: bool
    requires_review_before_live_use: bool
    authorization_source: Literal["server_configuration"] = "server_configuration"
    model_bundle_id: str | None
    live_request_fingerprint: str
    eligible_for_live_submission: bool
    execution_owner: Literal["backend"] = "backend"
    backend_revalidation_required: Literal[True] = True


def blocked(code, status=503):
    return HTTPException(
        status,
        detail={"code": code, "action": "REVIEW"},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


@dataclass(frozen=True)
class LiveAuthorization:
    signature: str
    model_version: str | None
    bundle_id: str | None


def check_live_gate(credentials, policy, manifest, order_types=(), clock=None):
    """Server controls authorize preflight eligibility without asserting model accuracy."""
    if os.getenv("LOADPILOT_AUTOPILOT_LIVE_ENABLED", "false") != "true":
        raise blocked("LIVE_DISABLED")
    if os.getenv("LOADPILOT_AUTOPILOT_KILL_SWITCH", "false") != "false":
        raise blocked("LIVE_KILL_SWITCH_ACTIVE")
    token = os.getenv("LOADPILOT_AUTOPILOT_LIVE_TOKEN", "")
    if len(token) < 32:
        raise blocked("LIVE_AUTH_NOT_CONFIGURED")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), token.encode()
    ):
        raise blocked("LIVE_UNAUTHORIZED", 401)
    allowed = {
        value.strip()
        for value in os.getenv("LOADPILOT_AUTOPILOT_ORDER_TYPES", "open_bid,fixed_rate").split(",")
    }
    if not allowed or not allowed.issubset({"open_bid", "fixed_rate"}):
        raise blocked("LIVE_CONFIGURATION_INVALID")
    if not set(order_types).issubset(allowed):
        raise blocked("LIVE_ORDER_TYPE_DISABLED")
    clock_mode = os.getenv("LOADPILOT_AUTOPILOT_CLOCK_MODE", "either")
    if clock_mode not in {"wall", "simulation", "either"}:
        raise blocked("LIVE_CONFIGURATION_INVALID")
    if (clock_mode == "simulation" and clock is None) or (
        clock_mode == "wall" and clock is not None
    ):
        raise blocked("LIVE_CLOCK_MODE_MISMATCH")
    manifest = manifest if isinstance(manifest, dict) else {}
    version, bundle_id = manifest.get("model_version"), manifest.get("bundle_id")
    # This secret-derived signature stays in memory; never return or log it.
    signature = fingerprint(
        {
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "order_types": sorted(allowed),
            "clock_mode": clock_mode,
            "model_version": version,
            "bundle_id": bundle_id,
            "policy": policy.model_dump() if policy is not None else None,
        }
    )
    return LiveAuthorization(signature, version, bundle_id)


def install_live_routes(router, evaluate_autopilot, analyze_batch, get_manifest):
    bearer = HTTPBearer(auto_error=False)
    auth_type = Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]

    async def gate(credentials, policy, order_types, clock):
        return await asyncio.to_thread(
            check_live_gate, credentials, policy, get_manifest(), order_types, clock
        )

    async def recheck(credentials, policy, original, order_types, clock):
        current = await gate(credentials, policy, order_types, clock)
        if not hmac.compare_digest(current.signature, original.signature):
            raise blocked("LIVE_AUTHORIZATION_CHANGED")
        return current

    def live_response(response, authorization, request_fingerprint, selected=True, batch=False):
        supported = isinstance(response.advisory, BaselineFallbackResponse) or (
            isinstance(response.advisory, BidPredictionResponse)
            and (
                authorization.model_version is None
                or response.advisory.model_version == authorization.model_version
            )
        )
        eligible = selected and response.decision == "BID" and supported
        value = response.model_dump()
        if response.decision == "BID" and not supported:
            value.update(decision="SKIP" if batch else "REVIEW", valid_until=None)
            value["reason_codes"] = [*value["reason_codes"], "LIVE_MODEL_MISMATCH"]
            value["reasons"].append(
                {
                    "code": "LIVE_MODEL_MISMATCH",
                    "severity": "REVIEW",
                    "field": "advisory",
                    "details": {},
                }
            )
        value.update(
            mode="live",
            eligible_for_live_submission=eligible,
            requires_review_before_live_use=not eligible,
            live_blockers=[] if eligible else ["CANDIDATE_NOT_ELIGIBLE"],
            model_bundle_id=authorization.bundle_id,
            live_request_fingerprint=request_fingerprint,
        )
        record = current_log.get()
        write_event(
            logging.INFO,
            {
                "event": "live_autopilot_decision",
                "request_id": record.request_id if record else None,
                "mode": "live",
                "decision_id": response.decision_id,
                "decision": value["decision"],
                "selected": selected,
                "eligible_for_live_submission": eligible,
                "submission_enabled": False,
            },
        )
        return LiveSingleResponse.model_validate(value)

    errors = {status: {"model": PricingErrorResponse} for status in (401, 422, 503)}

    @router.post("/autopilot/evaluate", response_model=LiveSingleResponse, responses=errors)
    @router.post("/autopilot/live/evaluate", response_model=LiveSingleResponse, responses=errors)
    async def evaluate_live(body: LiveSingleRequest, credentials: auth_type):
        order_types = {body.shipment.order_type}
        original = await gate(credentials, body.policy, order_types, body.clock)
        inputs = AutopilotRequest.model_validate(body.model_dump())
        response = await evaluate_autopilot(inputs)
        authorization = await recheck(credentials, body.policy, original, order_types, body.clock)
        if response.decision == "BID" and (
            response.valid_until is None or response.valid_until <= utc_now()
        ):
            raise blocked("LIVE_DECISION_EXPIRED")
        return live_response(response, authorization, fingerprint(body.model_dump()))

    @router.post("/autopilot/analyze-batch", response_model=LiveBatchResponse, responses=errors)
    @router.post(
        "/autopilot/live/analyze-batch", response_model=LiveBatchResponse, responses=errors
    )
    async def analyze_live(body: LiveBatchRequest, credentials: auth_type):
        order_types = {item.shipment.order_type for item in body.opportunities}
        original = await gate(credentials, body.policy, order_types, body.clock)
        inputs = BatchRequest.model_validate(body.model_dump(by_alias=True))
        response = await analyze_batch(inputs)
        authorization = await recheck(credentials, body.policy, original, order_types, body.clock)
        if response.selected_count and (
            response.valid_until is None or response.valid_until <= utc_now()
        ):
            raise blocked("LIVE_DECISION_EXPIRED")
        request_fingerprint = fingerprint(body.model_dump())
        rows = [
            LiveBatchEvaluation(
                selected=row.selected,
                selection_reason=row.selection_reason,
                comparison=row.comparison,
                requires_review=row.requires_review,
                evaluation=LiveBatchOrderResponse.model_validate(
                    live_response(
                        row.evaluation, authorization, request_fingerprint, row.selected, batch=True
                    ).model_dump()
                ),
            )
            for row in response.evaluations
        ]
        eligible = response.decision == "BID" and response.selected_count > 0
        if any(row.selected and not row.evaluation.eligible_for_live_submission for row in rows):
            raise blocked("LIVE_MODEL_MISMATCH")
        return LiveBatchResponse.model_validate(
            {
                **response.model_dump(),
                "mode": "live",
                "evaluations": rows,
                "live_ready": True,
                "requires_review_before_live_use": not eligible,
                "eligible_for_live_submission": eligible,
                "live_request_fingerprint": request_fingerprint,
                "model_bundle_id": authorization.bundle_id,
            }
        )
