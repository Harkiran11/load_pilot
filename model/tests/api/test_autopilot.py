"""Backend-triggered decisions, prediction failures, fallback and submission boundaries."""

import copy
import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from loadpilot_model.api.autopilot import AutopilotRequest
from loadpilot_model.api.live import LiveSingleResponse
from loadpilot_model.inference.errors import PredictionInputError
from loadpilot_model.paths import PROJECT_ROOT
from tests.api import test_fallback, test_pricing
from tests.decision.fixtures import NOW, request

# The published API examples live outside this repository. Skip the contract
# examples rather than failing the suite when they are absent.
if not (PROJECT_ROOT / "requirements/docs/api/examples").is_dir():
    raise unittest.SkipTest("API example fixtures are not part of this checkout")


def json_payload(value):
    return AutopilotRequest.model_validate(value).model_dump(mode="json")


class AutopilotApiTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        clock = patch("loadpilot_model.api.autopilot.utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.http = test_pricing.PricingApiTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.token = "synthetic-internal-autopilot-token-for-tests"
        self.headers = {"Authorization": f"Bearer {self.token}"}
        import os

        os.environ.update(
            LOADPILOT_AUTOPILOT_LIVE_ENABLED="true",
            LOADPILOT_AUTOPILOT_KILL_SWITCH="false",
            LOADPILOT_AUTOPILOT_LIVE_TOKEN=self.token,
        )
        live_clock = patch("loadpilot_model.api.live.utc_now", return_value=NOW)
        live_clock.start()
        self.addCleanup(live_clock.stop)
        self.request = request()
        self.http.predictor.predict.return_value.update(
            predicted_bid_dollars=2000, predicted_bid_rpm=2
        )
        self.http.predictor.predict.return_value["baseline_reference"].update(
            predicted_bid_dollars=2000, predicted_bid_rpm=2
        )

    def test_live_bid_has_identity_economics_and_backend_execution_eligibility(self):
        before = copy.deepcopy(self.request)
        with TestClient(self.http.app, headers=self.headers) as client:
            result = client.post("/api/v1/autopilot/evaluate", json=json_payload(self.request))
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["decision"], "BID")
        self.assertEqual(body["candidate_bid_dollars"], 2000)
        self.assertEqual(body["estimated_profit_dollars"], 600)
        self.assertEqual(body["estimated_margin_percent"], 30)
        self.assertEqual(body["minimum_bid_dollars"], 1750)
        self.assertEqual(body["unit_id"], "unit-example")
        self.assertEqual(body["snapshot_version"], "snapshot-example-v1")
        self.assertEqual(body["mode"], "live")
        self.assertFalse(body["submission_enabled"])
        self.assertTrue(body["eligible_for_live_submission"])
        self.assertEqual(body["live_blockers"], [])
        self.assertEqual(self.request, before)
        features = {
            key: value for key, value in self.request["shipment"].items() if key != "order_type"
        }
        self.http.predictor.predict.assert_called_once_with(features)
        self.assertIn("X-Prediction-Id", result.headers)

    def test_missing_policy_and_context_return_review_not_a_fabricated_pass(self):
        for field, code in (
            ("policy", "INVALID_POLICY"),
            ("backend_context", "FLEET_DATA_MISSING"),
        ):
            payload = json_payload(self.request)
            payload[field] = None
            with TestClient(self.http.app, headers=self.headers) as client:
                response = client.post("/api/v1/autopilot/evaluate", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["decision"], "REVIEW")
            self.assertIn(code, response.json()["reason_codes"])

    def test_fixed_rate_disabled_and_kill_switch_skip_without_prediction(self):
        for change in ("fixed_rate", "disabled", "kill"):
            payload = json_payload(self.request)
            if change == "fixed_rate":
                payload["shipment"]["order_type"] = "fixed_rate"
            elif change == "disabled":
                payload["control"]["autopilot_enabled"] = False
            else:
                payload["control"]["kill_switch_active"] = True
            with TestClient(self.http.app, headers=self.headers) as client:
                response = client.post("/api/v1/autopilot/evaluate", json=payload)
            self.assertEqual(response.json()["decision"], "SKIP")
        self.http.predictor.predict.assert_not_called()

    def test_model_error_and_unseen_input_return_review_with_no_candidate(self):
        for error, code in (
            (RuntimeError("PRIVATE_FAILURE"), "PREDICTION_UNAVAILABLE"),
            (PredictionInputError("UNSEEN_CATEGORY", []), "UNSUPPORTED_MODEL_INPUT"),
        ):
            self.http.predictor.predict.side_effect = error
            with TestClient(self.http.app, headers=self.headers) as client:
                response = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["decision"], "REVIEW")
            self.assertIn(code, body["reason_codes"])
            self.assertIsNone(body["candidate_bid_dollars"])
            self.assertNotIn("PRIVATE_FAILURE", response.text)

    def test_prediction_timeout_discards_late_worker_result_and_records_failure(self):
        output = copy.deepcopy(self.http.predictor.predict.return_value)

        def slow(*args):
            time.sleep(0.03)
            return output

        self.http.predictor.predict.side_effect = slow
        self.request["policy"]["prediction_timeout_seconds"] = 0.001
        with self.assertLogs("loadpilot_model.api.pricing", level="INFO") as logs:
            with TestClient(self.http.app, headers=self.headers) as client:
                response = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                )
        self.assertEqual(response.json()["decision"], "REVIEW")
        self.assertIn("PREDICTION_TIMEOUT", response.json()["reason_codes"])
        self.assertIsNone(response.json()["candidate_bid_dollars"])
        events = [json.loads(record.getMessage()) for record in logs.records]
        event = next(item for item in events if item["event"] == "pricing_request")
        self.assertEqual(event["outcome"], "advisory_unavailable")
        self.assertEqual(event["error_code"], "PREDICTION_TIMEOUT")
        self.assertIsNone(event["pricing_source"])

    def test_invalid_cost_policy_timestamps_live_mode_and_action_fields_reject_before_inference(
        self,
    ):
        changes = [
            ("negative_cost", lambda p: p["backend_context"].update(deadhead_cost_dollars=-1)),
            ("numeric_string", lambda p: p["policy"].update(min_profit_dollars="200")),
            ("margin_100", lambda p: p["policy"].update(min_margin_percent=100)),
            (
                "naive_time",
                lambda p: p["backend_context"].update(costs_observed_at="2026-10-16T12:00:00"),
            ),
            ("shadow", lambda p: p.update(mode="shadow")),
            ("submit", lambda p: p.update(submit=True)),
            ("candidate", lambda p: p.update(candidate_bid_dollars=2000)),
            ("manual_bid", lambda p: p["shipment"].update(bid_rate=2000)),
            ("blank_id", lambda p: p.update(unit_id="   ")),
        ]
        with TestClient(self.http.app, headers=self.headers) as client:
            for name, change in changes:
                with self.subTest(name=name):
                    payload = json_payload(self.request)
                    change(payload)
                    response = client.post("/api/v1/autopilot/evaluate", json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"]["action"], "REVIEW")
        self.http.predictor.predict.assert_not_called()

    def test_audit_logs_decision_codes_and_versions_without_operational_payload(self):
        with self.assertLogs("loadpilot_model.api.pricing", level="INFO") as logs:
            with TestClient(self.http.app, headers=self.headers) as client:
                response = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                )
        events = [json.loads(record.getMessage()) for record in logs.records]
        decision = next(item for item in events if item["event"] == "autopilot_decision")
        completed = next(item for item in events if item["event"] == "pricing_request")
        self.assertEqual(decision["decision_id"], response.json()["decision_id"])
        self.assertEqual(decision["request_id"], response.headers["X-Prediction-Id"])
        self.assertEqual(completed["endpoint"], "/api/v1/autopilot/evaluate")
        text = json.dumps(events)
        for secret in (
            "shipment-example",
            "unit-example",
            "deadhead_cost_dollars",
            "candidate_bid_dollars",
            "estimated_profit_dollars",
        ):
            self.assertNotIn(secret, text)

    def test_openapi_documents_authenticated_live_decision_contract(self):
        with TestClient(self.http.app, headers=self.headers) as client:
            spec = client.get("/openapi.json").json()
        self.assertIn("/api/v1/autopilot/evaluate", spec["paths"])
        schemas = spec["components"]["schemas"]
        self.assertEqual(schemas["LiveSingleRequest"]["properties"]["mode"]["const"], "live")
        self.assertEqual(
            schemas["LiveSingleResponse"]["properties"]["submission_enabled"]["const"], False
        )

    def test_documented_request_and_three_decision_examples_match_runtime_contract(self):
        root = PROJECT_ROOT / "requirements/docs/api/examples"
        payload = AutopilotRequest.model_validate_json(
            (root / "autopilot-request.json").read_text()
        )
        for name in ("bid", "skip", "review"):
            response = LiveSingleResponse.model_validate_json(
                (root / f"autopilot-{name}-response.json").read_text()
            )
            self.assertEqual(response.decision, name.upper())
            self.assertEqual(response.shipment_id, payload.shipment_id)
            self.assertEqual(response.eligible_for_live_submission, name == "bid")


class AutopilotFallbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_fallback.PricingFallbackApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        clock = patch("loadpilot_model.api.autopilot.utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.token = "synthetic-internal-autopilot-token-for-tests"
        self.headers = {"Authorization": f"Bearer {self.token}"}
        import os

        os.environ.update(
            LOADPILOT_AUTOPILOT_LIVE_ENABLED="true",
            LOADPILOT_AUTOPILOT_KILL_SWITCH="false",
            LOADPILOT_AUTOPILOT_LIVE_TOKEN=self.token,
        )
        live_clock = patch("loadpilot_model.api.live.utc_now", return_value=NOW)
        live_clock.start()
        self.addCleanup(live_clock.stop)
        self.request = request()
        self.request["policy"].update(
            max_bid_dollars=10000,
            fallback_mode="baseline",
            baseline_id="lane_median_bid_rpm",
            baseline_version="baseline-fallback-v1",
            min_lane_samples=5,
        )

    def test_approved_fallback_runs_all_guards_and_is_not_an_automatic_pass(self):
        with patch.dict(
            "os.environ",
            {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline", "LOADPILOT_PRICING_MODEL_BLOCKED": "true"},
        ):
            with TestClient(self.fixture.app, headers=self.headers) as client:
                response = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                )
                self.assertEqual(response.json()["decision"], "BID", response.text)
                self.assertEqual(response.json()["advisory"]["pricing_source"], "baseline")
                self.request["backend_context"]["checks"]["unit_available"] = False
                response = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                )
                self.assertEqual(response.json()["decision"], "SKIP")
                self.assertIn("UNIT_UNAVAILABLE", response.json()["reason_codes"])

    def test_server_fallback_cannot_override_request_review_policy(self):
        self.request["policy"]["fallback_mode"] = "review"
        with patch.dict(
            "os.environ",
            {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline", "LOADPILOT_PRICING_MODEL_BLOCKED": "true"},
        ):
            with TestClient(self.fixture.app, headers=self.headers) as client:
                body = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                ).json()
        self.assertEqual(body["decision"], "REVIEW")
        self.assertIn("FALLBACK_NOT_ALLOWED", body["reason_codes"])

    def test_unseen_category_fallback_requires_both_server_and_policy_opt_in(self):
        self.fixture.http.predictor.predict.side_effect = PredictionInputError(
            "UNSEEN_CATEGORY", []
        )
        with patch.dict(
            "os.environ",
            {
                "LOADPILOT_PRICING_FALLBACK_MODE": "baseline",
                "LOADPILOT_PRICING_UNSEEN_CATEGORY_ACTION": "baseline",
            },
        ):
            with TestClient(self.fixture.app, headers=self.headers) as client:
                body = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                ).json()
                self.assertEqual(body["decision"], "REVIEW")
                self.request["policy"]["unseen_category_action"] = "baseline"
                body = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                ).json()
                self.assertEqual(body["decision"], "BID")

    def test_timeout_can_use_independent_explicitly_approved_baseline(self):
        def slow(*args):
            time.sleep(0.03)
            raise RuntimeError("late failure")

        self.fixture.http.predictor.predict.side_effect = slow
        self.request["policy"]["prediction_timeout_seconds"] = 0.001
        with patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}):
            with TestClient(self.fixture.app, headers=self.headers) as client:
                body = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                ).json()
        self.assertEqual(body["decision"], "BID", body)
        self.assertEqual(body["advisory"]["trigger"], "MODEL_TIMEOUT")
        self.assertFalse(body["submission_enabled"])

    def test_baseline_policy_identity_mismatch_requires_review(self):
        self.request["policy"]["baseline_version"] = "unapproved-version"
        with patch.dict(
            "os.environ",
            {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline", "LOADPILOT_PRICING_MODEL_BLOCKED": "true"},
        ):
            with TestClient(self.fixture.app, headers=self.headers) as client:
                body = client.post(
                    "/api/v1/autopilot/evaluate", json=json_payload(self.request)
                ).json()
        self.assertEqual(body["decision"], "REVIEW")
        self.assertIn("FALLBACK_POLICY_MISMATCH", body["reason_codes"])
