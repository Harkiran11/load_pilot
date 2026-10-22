"""Inclusive policy limits, unknown/stale inputs and deterministic SKIP precedence."""

import copy
import unittest
from datetime import timedelta

from loadpilot_model.decision.engine import CHECKS, evaluate, fingerprint
from tests.decision.fixtures import NOW, candidate, request


class ShadowEngineTests(unittest.TestCase):
    def test_valid_candidate_has_loaded_plus_deadhead_economics_and_expiry(self):
        payload = request()
        before = copy.deepcopy(payload)
        result = evaluate(payload, candidate(), None, NOW)
        self.assertEqual(result["decision"], "BID")
        self.assertEqual(result["reason_codes"], ["ALL_CHECKS_PASSED"])
        self.assertEqual(result["candidate_bid_dollars"], 2000)
        self.assertEqual(result["total_estimated_cost_dollars"], 1400)
        self.assertEqual(result["minimum_bid_dollars"], 1750)
        self.assertEqual(result["estimated_profit_dollars"], 600)
        self.assertEqual(result["estimated_margin_percent"], 30)
        self.assertEqual(result["valid_until"], NOW + timedelta(seconds=60))
        self.assertEqual(payload, before)

    def test_floor_and_max_bid_are_inclusive_without_adjusting_candidate(self):
        for price, decision, reason in (
            (1750, "BID", "ALL_CHECKS_PASSED"),
            (1749.99, "SKIP", "BELOW_PROFIT_FLOOR"),
            (2500, "BID", "ALL_CHECKS_PASSED"),
            (2500.01, "SKIP", "ABOVE_MAX_BID"),
        ):
            with self.subTest(price=price):
                result = evaluate(request(), candidate(price), None, NOW)
                self.assertEqual(result["decision"], decision)
                self.assertIn(reason, result["reason_codes"])
                self.assertEqual(result["candidate_bid_dollars"], price)

    def test_infeasible_policy_floor_is_skip_even_with_missing_prediction(self):
        payload = request()
        payload["policy"]["max_bid_dollars"] = 1600
        result = evaluate(payload, None, "PREDICTION_UNAVAILABLE", NOW)
        self.assertEqual(result["decision"], "SKIP")
        self.assertIn("NO_FEASIBLE_PRICE", result["reason_codes"])
        self.assertIn("PREDICTION_UNAVAILABLE", result["reason_codes"])

    def test_each_known_operational_failure_skips_and_each_unknown_requires_review(self):
        for name, code in CHECKS.items():
            for value, decision in ((False, "SKIP"), (None, "REVIEW")):
                payload = request()
                payload["backend_context"]["checks"][name] = value
                result = evaluate(payload, candidate(), None, NOW)
                self.assertEqual(result["decision"], decision)
                self.assertIn(
                    code if value is False else "OPERATIONAL_CHECK_UNKNOWN", result["reason_codes"]
                )

    def test_deadhead_equality_passes_and_excess_skips(self):
        for miles, decision in ((100, "BID"), (100.001, "SKIP")):
            payload = request()
            payload["backend_context"]["deadhead_miles"] = miles
            self.assertEqual(evaluate(payload, candidate(), None, NOW)["decision"], decision)

    def test_missing_costs_fleet_position_or_policy_require_review(self):
        for field in ("loaded_cost_dollars", "deadhead_cost_dollars", "projected_position"):
            payload = request()
            payload["backend_context"][field] = None
            self.assertEqual(evaluate(payload, candidate(), None, NOW)["decision"], "REVIEW")
        for field in ("backend_context", "policy"):
            payload = request()
            payload[field] = None
            self.assertEqual(evaluate(payload, candidate(), None, NOW)["decision"], "REVIEW")

    def test_input_age_equality_passes_future_and_stale_snapshots_review(self):
        for age, decision in ((300, "BID"), (300.001, "REVIEW"), (-0.001, "REVIEW")):
            payload = request()
            payload["backend_context"]["costs_observed_at"] = NOW - timedelta(seconds=age)
            result = evaluate(payload, candidate(), None, NOW)
            self.assertEqual(result["decision"], decision)
        payload = request()
        payload["backend_context"]["unit_observed_at"] = None
        self.assertIn(
            "TIMESTAMP_MISSING", evaluate(payload, candidate(), None, NOW)["reason_codes"]
        )

    def test_order_expiration_is_checked_and_unknown_is_review(self):
        for stamp, decision in (
            (NOW, "SKIP"),
            (NOW - timedelta(seconds=1), "SKIP"),
            (None, "REVIEW"),
            (NOW + timedelta(seconds=1), "BID"),
        ):
            payload = request()
            payload["backend_context"]["order_expires_at"] = stamp
            result = evaluate(payload, candidate(), None, NOW)
            self.assertEqual(result["decision"], decision)
            if decision == "BID":
                self.assertEqual(result["valid_until"], stamp)

    def test_context_mismatch_and_changed_snapshot_invalidate_fingerprint(self):
        payload = request()
        before = fingerprint(payload)
        payload["backend_context"]["unit_id"] = "other-unit"
        result = evaluate(payload, candidate(), None, NOW)
        self.assertEqual(result["decision"], "REVIEW")
        self.assertIn("CONTEXT_MISMATCH", result["reason_codes"])
        self.assertNotEqual(result["context_fingerprint"], before)
        payload = request()
        payload["policy"]["min_profit_dollars"] = 201
        self.assertNotEqual(fingerprint(payload), before)

    def test_reference_deviation_equality_passes_and_excess_reviews(self):
        for limit, decision in ((25, "BID"), (24.999, "REVIEW")):
            payload = request()
            payload["policy"]["max_reference_deviation_percent"] = limit
            self.assertEqual(evaluate(payload, candidate(2500), None, NOW)["decision"], decision)

    def test_skip_precedence_records_every_known_failure(self):
        payload = request()
        payload["backend_context"]["checks"].update(unit_available=False, timing_feasible=None)
        result = evaluate(payload, candidate(1700), None, NOW)
        self.assertEqual(result["decision"], "SKIP")
        self.assertTrue(
            {"UNIT_UNAVAILABLE", "OPERATIONAL_CHECK_UNKNOWN", "BELOW_PROFIT_FLOOR"}
            <= set(result["reason_codes"])
        )

    def test_currency_rounding_recalculates_candidate_profit_and_rpm(self):
        result = evaluate(request(), candidate(1749.995), None, NOW)
        self.assertEqual(result["candidate_bid_dollars"], 1750)
        self.assertEqual(result["candidate_bid_rpm"], 1.75)
        self.assertEqual(result["estimated_profit_dollars"], 350)
        self.assertEqual(result["decision"], "BID")

    def test_invalid_candidate_or_reference_units_require_review(self):
        proposal = candidate()
        proposal["predicted_bid_rpm"] = 9
        self.assertIn("INVALID_CANDIDATE", evaluate(request(), proposal, None, NOW)["reason_codes"])
        proposal = candidate()
        proposal["baseline_reference"]["predicted_bid_rpm"] = 9
        self.assertIn("INVALID_REFERENCE", evaluate(request(), proposal, None, NOW)["reason_codes"])

    def test_control_states_kill_or_review_without_permission_to_submit(self):
        for control, decision in (
            ({"kill_switch_active": True}, "SKIP"),
            ({"autopilot_enabled": False}, "SKIP"),
            ({"kill_switch_active": None}, "REVIEW"),
        ):
            payload = request()
            payload["control"].update(control)
            self.assertEqual(evaluate(payload, candidate(), None, NOW)["decision"], decision)
