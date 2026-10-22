"""Offline review of backend-exported live batch decisions; never performs inference."""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from loadpilot_model.api.autopilot import TimestampInput
from loadpilot_model.api.live import LiveBatchRequest, LiveBatchResponse
from loadpilot_model.decision.engine import fingerprint


class DecisionRecord(TimestampInput):
    received_at: AwareDatetime
    request: LiveBatchRequest
    response: LiveBatchResponse


class SubmissionRecord(TimestampInput):
    submission_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    submitted_at: AwareDatetime
    amount_dollars: float = Field(gt=0)
    status: Literal["accepted", "rejected", "unknown"]
    revalidated_at: AwareDatetime | None = None
    authorization_passed: bool | None = None
    operational_checks_passed: bool | None = None
    atomic_reservation_acquired: bool | None = None
    rate_limit_passed: bool | None = None

    @field_validator("amount_dollars")
    @classmethod
    def whole_cents(cls, value):
        money = Decimal(str(value))
        with localcontext() as context:
            context.prec = 400
            if money != money.quantize(Decimal("0.01")):
                raise ValueError("Submission amount must use whole USD cents")
        return value


class OutcomeRecord(TimestampInput):
    submission_id: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)
    observed_at: AwareDatetime
    award_status: Literal["awarded", "lost", "unknown"]
    shipment_status: Literal["completed", "cancelled", "in_progress", "unknown"]
    completed_at: AwareDatetime | None = None
    actual_payout_dollars: float | None = Field(default=None, ge=0)
    actual_loaded_cost_dollars: float | None = Field(default=None, ge=0)
    actual_deadhead_cost_dollars: float | None = Field(default=None, ge=0)
    actual_other_cost_dollars: float | None = Field(default=None, ge=0)
    # Backend attests all costs are included exactly once in these three components.
    actual_cost_scope_complete: bool = False


class LiveAuditExport(TimestampInput):
    schema_version: Literal[1] = 1
    currency: Literal["USD"] = "USD"
    synthetic: bool
    exported_at: AwareDatetime
    decisions: list[DecisionRecord]
    submissions: list[SubmissionRecord]
    # One latest observed outcome per shipment in this export, not an event stream.
    outcomes: list[OutcomeRecord]

    @model_validator(mode="after")
    def unique_records(self):
        for values, label in (
            ([r.response.analysis_id for r in self.decisions], "analysis_id"),
            ([s.submission_id for s in self.submissions], "submission_id"),
            ([o.shipment_id for o in self.outcomes], "outcome shipment_id"),
            (
                [r.evaluation.decision_id for d in self.decisions for r in d.response.evaluations],
                "decision_id",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {label}; merge/deduplicate exports before auditing")
        return self


def audit_live(export: LiveAuditExport) -> dict:
    """Check captured contracts and linkage, without reconstructing unknown fleet history."""
    issues = []

    def issue(code, identifier):
        issues.append({"code": code, "record_id": identifier})

    decisions = {}
    counts = Counter()
    reasons = Counter()
    for record in export.decisions:
        request, response = record.request, record.response
        rid = response.analysis_id
        valid = True

        def decision_issue(code):
            nonlocal valid
            valid = False
            issue(code, rid)

        if not response.evaluated_at <= record.received_at <= export.exported_at:
            decision_issue("DECISION_TIME_INVALID")
        if response.live_request_fingerprint != fingerprint(request.model_dump()):
            decision_issue("REQUEST_FINGERPRINT_MISMATCH")
        if (response.batch_id, response.fleet_snapshot_version, response.strategy) != (
            request.batch_id,
            request.fleet_snapshot_version,
            request.strategy,
        ):
            decision_issue("BATCH_IDENTITY_MISMATCH")
        if request.clock is not None:
            expected_clock = (request.clock.clock_id, request.clock.revision, request.clock.speed)
            captured_clocks = [response.clock, *[r.evaluation.clock for r in response.evaluations]]
            if any(
                clock is None
                or clock.domain != "simulation"
                or (clock.clock_id, clock.revision, clock.speed) != expected_clock
                for clock in captured_clocks
            ):
                decision_issue("CLOCK_IDENTITY_MISMATCH")
        requested = {(o.shipment_id, o.unit_id) for o in request.opportunities}
        returned = [(r.evaluation.shipment_id, r.evaluation.unit_id) for r in response.evaluations]
        if len(returned) != len(set(returned)) or set(returned) != requested:
            decision_issue("PAIR_COVERAGE_MISMATCH")
        selected = [r for r in response.evaluations if r.selected]
        if len(selected) != response.selected_count or (response.decision == "BID") != bool(
            selected
        ):
            decision_issue("SELECTION_COUNT_MISMATCH")
        for attr in ("shipment_id", "unit_id"):
            values = [getattr(r.evaluation, attr) for r in selected]
            if len(values) != len(set(values)):
                decision_issue("ALLOCATION_CONFLICT")
        if request.strategy == "best_order" and len(selected) > 1:
            decision_issue("BEST_ORDER_MULTIPLE_SELECTIONS")
        if selected and not response.eligible_for_live_submission:
            decision_issue("BATCH_NOT_ELIGIBLE")
        offered = {
            (o.shipment_id, o.unit_id): o.offered_rate_dollars
            for o in request.opportunities
            if o.shipment.order_type == "fixed_rate"
        }
        for row in response.evaluations:
            evaluation = row.evaluation
            counts[evaluation.decision] += 1
            reasons.update(evaluation.reason_codes)
            if evaluation.live_request_fingerprint != response.live_request_fingerprint:
                decision_issue("PAIR_FINGERPRINT_MISMATCH")
            if row.selected and (
                evaluation.decision != "BID" or not evaluation.eligible_for_live_submission
            ):
                decision_issue("SELECTED_PAIR_NOT_ELIGIBLE")
            pair = (evaluation.shipment_id, evaluation.unit_id)
            if (
                row.selected
                and pair in offered
                and (evaluation.candidate_bid_dollars != offered[pair])
            ):
                decision_issue("FIXED_OFFER_CHANGED")
        # A corrupt batch invalidates all its linked submission evidence.
        for row in response.evaluations:
            decisions[row.evaluation.decision_id] = (record, row, valid)

    submissions = {s.submission_id: s for s in export.submissions}
    accepted_orders = set()
    duplicate_accepted_orders = {
        order
        for order, count in Counter(
            s.shipment_id for s in export.submissions if s.status == "accepted"
        ).items()
        if count > 1
    }
    key_context = {}
    linked_submissions = set()
    safeguards_verified_count = 0
    for submission in export.submissions:
        sid = submission.submission_id
        identity = (submission.shipment_id, submission.unit_id, submission.amount_dollars)
        if submission.idempotency_key in key_context and (
            key_context[submission.idempotency_key] != identity
        ):
            issue("IDEMPOTENCY_KEY_REUSED_FOR_DIFFERENT_BID", sid)
        key_context[submission.idempotency_key] = identity
        if submission.status == "accepted":
            if submission.shipment_id in accepted_orders:
                issue("DUPLICATE_ACCEPTED_SHIPMENT", sid)
            accepted_orders.add(submission.shipment_id)
        if submission.submitted_at > export.exported_at:
            issue("SUBMISSION_AFTER_EXPORT", sid)
        captured = decisions.get(submission.decision_id)
        if captured is None:
            issue("SUBMISSION_DECISION_MISSING", sid)
            continue
        record, row, valid = captured
        evaluation = row.evaluation
        if not valid or not row.selected or not evaluation.eligible_for_live_submission:
            issue("SUBMISSION_WITHOUT_VALID_SELECTED_APPROVAL", sid)
            continue
        if identity != (
            evaluation.shipment_id,
            evaluation.unit_id,
            evaluation.candidate_bid_dollars,
        ):
            issue("SUBMISSION_QUOTE_OR_ASSIGNMENT_MISMATCH", sid)
            continue
        if evaluation.valid_until is None or not (
            record.received_at <= submission.submitted_at < evaluation.valid_until
        ):
            issue("SUBMISSION_OUTSIDE_DECISION_VALIDITY", sid)
            continue
        linked_submissions.add(sid)
        checks = (
            submission.authorization_passed,
            submission.operational_checks_passed,
            submission.atomic_reservation_acquired,
            submission.rate_limit_passed,
        )
        if not all(value is True for value in checks) or submission.revalidated_at is None:
            issue("BACKEND_SAFEGUARD_EVIDENCE_INCOMPLETE_OR_FAILED", sid)
        elif not record.received_at <= submission.revalidated_at <= submission.submitted_at:
            issue("BACKEND_REVALIDATION_TIME_INVALID", sid)
        else:
            safeguards_verified_count += 1

    realized = []
    awards = Counter()
    for outcome in export.outcomes:
        sid = outcome.submission_id
        submission = submissions.get(sid)
        if submission is None or submission.shipment_id != outcome.shipment_id:
            issue("OUTCOME_SUBMISSION_MISSING_OR_MISMATCHED", outcome.shipment_id)
            continue
        if submission.status != "accepted" or sid not in linked_submissions:
            issue("OUTCOME_SUBMISSION_NOT_VALIDATED", outcome.shipment_id)
            continue
        if outcome.shipment_id in duplicate_accepted_orders:
            issue("OUTCOME_HAS_AMBIGUOUS_DUPLICATE_SUBMISSIONS", outcome.shipment_id)
            continue
        if not submission.submitted_at <= outcome.observed_at <= export.exported_at:
            issue("OUTCOME_TIME_INVALID", outcome.shipment_id)
            continue
        awards[outcome.award_status] += 1
        if outcome.shipment_status != "completed":
            continue
        costs = [
            outcome.actual_loaded_cost_dollars,
            outcome.actual_deadhead_cost_dollars,
            outcome.actual_other_cost_dollars,
        ]
        if (
            outcome.award_status != "awarded"
            or outcome.completed_at is None
            or not submission.submitted_at <= outcome.completed_at <= outcome.observed_at
            or not outcome.actual_cost_scope_complete
            or outcome.actual_payout_dollars is None
            or any(cost is None for cost in costs)
        ):
            issue("COMPLETED_ACTUAL_ECONOMICS_INCOMPLETE", outcome.shipment_id)
            continue
        with localcontext() as context:
            context.prec = 400
            payout = Decimal(str(outcome.actual_payout_dollars))
            total_cost = sum((Decimal(str(cost)) for cost in costs), Decimal(0))
            realized.append(
                {
                    "shipment_id": outcome.shipment_id,
                    "actual_payout_dollars": str(payout),
                    "actual_total_cost_dollars": str(total_cost),
                    "realized_profit_dollars": str(payout - total_cost),
                }
            )

    with localcontext() as context:
        context.prec = 400
        total = sum((Decimal(r["realized_profit_dollars"]) for r in realized), Decimal(0))
    return {
        "schema_version": 1,
        "synthetic": export.synthetic,
        "currency": "USD",
        "decision_batch_count": len(export.decisions),
        "pair_decisions": dict(counts),
        "reason_counts": dict(reasons),
        "submission_attempt_count": len(export.submissions),
        "accepted_unique_shipment_count": len(accepted_orders),
        "accepted_shipments_without_outcomes_count": len(
            accepted_orders - {o.shipment_id for o in export.outcomes}
        ),
        "backend_safeguard_attested_count": safeguards_verified_count,
        "linked_award_counts": dict(awards),
        "complete_actual_economics_count": len(realized),
        "observed_realized_profit_dollars": None
        if export.synthetic or not realized
        else str(total),
        "actual_economics": [] if export.synthetic else realized,
        "synthetic_realized_profit_dollars": str(total) if export.synthetic and realized else None,
        "issues": issues,
        "issue_count": len(issues),
        "historical_fleet_verified": False,
        "backend_safeguards_independently_verified": False,
        "inference_repeated": False,
        "training_data_updated": False,
        "limitations": [
            "Contract/linkage review only; captured responses are not deterministic engine replay.",
            "Hashes detect mismatches, not authenticity of backend data or timestamps.",
            "Safeguards and actual cost scope are backend attestations, not execution proof.",
            "Export coverage is unknown; absent failures/outcomes cannot be assumed successful.",
            "No counterfactual awards, historical fleet reconstruction or optimal-profit proof.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contents = args.input.read_bytes()
    export = LiveAuditExport.model_validate_json(contents)
    report = audit_live(export)
    report["source_sha256"] = hashlib.sha256(contents).hexdigest()
    report["audit_implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        f"Reviewed {len(export.decisions)} batches; {report['issue_count']} issues. "
        f"Synthetic: {export.synthetic}. Report: {args.output / 'report.json'}"
    )
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
