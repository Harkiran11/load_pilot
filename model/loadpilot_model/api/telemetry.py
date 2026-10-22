"""Allowlisted JSON pricing events with per-request correlation and no payloads."""

import contextvars
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

LOGGER_NAME = "loadpilot_model.api.pricing"
ERROR_CODES = {
    "INVALID_REQUEST",
    "MISSING_REQUIRED_FIELD",
    "INVALID_TYPE",
    "INVALID_VALUE",
    "NONFINITE_NUMBER",
    "OUT_OF_RANGE",
    "UNSUPPORTED_UNIT",
    "UNSUPPORTED_ORDER_TYPE",
    "UNEXPECTED_FIELD",
    "INVALID_JSON",
    "UNSEEN_CATEGORY",
    "INVALID_MODEL_INPUT",
    "MODEL_UNAVAILABLE",
    "MODEL_ERROR",
    "INVALID_FALLBACK_POLICY",
    "MODEL_UNRELIABLE",
    "INVALID_MODEL_OUTPUT",
    "UNSUPPORTED_MODEL_INPUT",
    "REQUEST_CANCELLED",
    "PREDICTION_TIMEOUT",
    "INVALID_TIMESTAMP",
    "LIVE_AUTHORIZATION_CHANGED",
    "LIVE_MODEL_MISMATCH",
    "LIVE_CONFIGURATION_INVALID",
    "LIVE_ORDER_TYPE_DISABLED",
    "LIVE_DISABLED",
    "LIVE_KILL_SWITCH_ACTIVE",
    "LIVE_AUTH_NOT_CONFIGURED",
    "LIVE_UNAUTHORIZED",
    "LIVE_DECISION_EXPIRED",
    "LIVE_CLOCK_MODE_MISMATCH",
}
WARNING_CODES = {
    "MODEL_RELEASE_UNVERIFIED",
    "LANE_SUPPORT_UNVERIFIED",
    "SPARSE_LANE_REFERENCE",
    "NUMERIC_SUPPORT_HEURISTIC",
    "EVALUATED_SLICE_REGRESSION",
    "BASELINE_FALLBACK",
}


def configure_logging():
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(handler.get_name() == "pricing-json" for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name("pricing-json")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def identifier(value):
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value) else None
    )


@dataclass
class PricingLog:
    endpoint: str = "/api/v1/predict-bid"
    request_id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=time.perf_counter)
    status: int = 500
    model_version: str | None = None
    attempted_model_version: str | None = None
    baseline_id: str | None = None
    baseline_policy_version: str | None = None
    support_policy_version: str | None = None
    pricing_source: str | None = None
    trigger: str | None = None
    error_code: str | None = None
    validation_codes: list[str] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)


current_log = contextvars.ContextVar("pricing_log", default=None)


def write_event(level: int, event: dict):
    """Logging sink failure must not change pricing or trigger a new bid path."""
    try:
        logging.getLogger(LOGGER_NAME).log(
            level,
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "log_schema_version": 1,
                    **event,
                },
                sort_keys=True,
                allow_nan=False,
            ),
        )
    except Exception:
        # Do not log the failed payload/exception or recurse into the failed sink.
        pass


def diagnostic(code: str, error: Exception | None = None):
    record = current_log.get()
    write_event(
        logging.ERROR,
        {
            "event": "pricing_diagnostic",
            "request_id": record.request_id if record else None,
            "code": code,
            "exception_type": identifier(type(error).__name__) if error else None,
        },
    )


def note_prediction(result):
    record = current_log.get()
    if record is None:
        return
    record.model_version = identifier(result.model_version)
    if record.model_version is not None:
        record.attempted_model_version = record.model_version
    record.pricing_source = result.pricing_source
    record.baseline_id = identifier(result.baseline_reference.baseline_id)
    record.baseline_policy_version = identifier(result.baseline_reference.policy_version)
    record.support_policy_version = identifier(getattr(result, "support_policy_version", None))
    record.trigger = getattr(result, "trigger", None)
    record.warning_codes = sorted({w.code for w in result.warnings if w.code in WARNING_CODES})


def note_error(detail):
    record = current_log.get()
    if record is None or not isinstance(detail, dict):
        return
    code = detail.get("code")
    record.error_code = code if code in ERROR_CODES else "UNKNOWN_ERROR_CODE"
    record.validation_codes = sorted(
        {
            error.get("code")
            for error in detail.get("errors", [])
            if error.get("code") in ERROR_CODES
        }
    )


def finish(record: PricingLog):
    if record.status >= 500:
        outcome, level = "review", logging.ERROR
    elif record.status >= 400:
        outcome, level = "rejected", logging.WARNING
    elif record.error_code is not None:
        outcome, level = "advisory_unavailable", logging.WARNING
    elif record.pricing_source == "baseline":
        outcome, level = "fallback_advisory", logging.WARNING
    else:
        outcome, level = "model_advisory", logging.INFO
    write_event(
        level,
        {
            "event": "pricing_request",
            "request_id": record.request_id,
            "api_version": "v1",
            "endpoint": record.endpoint,
            "http_status": record.status,
            "outcome": outcome,
            "latency_ms": round(max(0, time.perf_counter() - record.started) * 1000, 3),
            "pricing_source": record.pricing_source,
            "model_version": record.model_version,
            "attempted_model_version": record.attempted_model_version,
            "baseline_id": record.baseline_id,
            "baseline_policy_version": record.baseline_policy_version,
            "support_policy_version": record.support_policy_version,
            "failure_trigger": record.trigger,
            "error_code": record.error_code,
            "validation_error_codes": record.validation_codes,
            "warning_codes": record.warning_codes,
            "requires_review": True,
            "submission_enabled": False,
        },
    )
