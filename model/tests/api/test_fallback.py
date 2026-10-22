"""HTTP failure policy: REVIEW by default, explicit independent baseline opt-in."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from loadpilot_model.api.pricing import create_pricing_router
from loadpilot_model.inference.errors import PredictionInputError
from tests.api import test_pricing
from tests.inference import test_serving_fallback


class PricingFallbackApiTests(unittest.TestCase):
    def setUp(self):
        # Reuse synthetic artifact and HTTP fixtures without running their test methods.
        self.artifacts = test_serving_fallback.ServingFallbackTests()
        self.artifacts.setUp()
        self.addCleanup(self.artifacts.doCleanups)
        self.http = test_pricing.PricingApiTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.app = FastAPI()
        self.app.include_router(create_pricing_router(self.artifacts.bundle))
        self.payload = self.http.payload
        environment = patch.dict(
            "os.environ",
            {
                "LOADPILOT_PRICING_FALLBACK_MODE": "review",
                "LOADPILOT_PRICING_UNSEEN_CATEGORY_ACTION": "review",
                "LOADPILOT_PRICING_MODEL_BLOCKED": "false",
                "LOADPILOT_MODEL_BUNDLE": str(self.artifacts.bundle),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def assert_candidate(self, response, trigger):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["pricing_source"], "baseline")
        self.assertIsNone(body["model_version"])
        self.assertEqual(body["trigger"], trigger)
        self.assertTrue(body["requires_review"])
        self.assertTrue(body["requires_guardrail_checks"])
        self.assertFalse(body["submission_enabled"])
        self.assertGreater(body["predicted_bid_dollars"], 0)

    def test_unavailable_model_reviews_by_default_and_baseline_requires_opt_in(self):
        self.http.factory.side_effect = ImportError("torch unavailable")
        with (
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("predicted_bid_rpm", response.text)
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            self.assert_candidate(
                client.post("/api/v1/predict-bid", json=self.payload), "MODEL_UNAVAILABLE"
            )

    def test_runtime_failure_and_invalid_output_use_explicit_baseline(self):
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            TestClient(self.app) as client,
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
        ):
            self.http.predictor.predict.side_effect = RuntimeError("failed")
            self.assert_candidate(
                client.post("/api/v1/predict-bid", json=self.payload), "MODEL_ERROR"
            )
            self.http.predictor.predict.side_effect = None
            self.http.predictor.predict.return_value = {"predicted_bid_rpm": -1}
            self.assert_candidate(
                client.post("/api/v1/predict-bid", json=self.payload), "INVALID_MODEL_OUTPUT"
            )

    def test_blocked_model_uses_review_or_baseline_and_never_runs_model(self):
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_MODEL_BLOCKED": "true"}),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "MODEL_UNRELIABLE")
        with (
            patch.dict(
                "os.environ",
                {"LOADPILOT_PRICING_MODEL_BLOCKED": "true", "LOADPILOT_PRICING_FALLBACK_MODE": "baseline"},
            ),
            TestClient(self.app) as client,
        ):
            self.assert_candidate(
                client.post("/api/v1/predict-bid", json=self.payload), "MODEL_UNRELIABLE"
            )
        self.http.factory.assert_not_called()
        self.http.predictor.predict.assert_not_called()

    def test_unseen_category_requires_second_opt_in(self):
        self.http.predictor.predict.side_effect = PredictionInputError("UNSEEN_CATEGORY", [])
        payload = {**self.payload, "lane_id": "UNSEEN-LANE"}
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "UNSEEN_CATEGORY")
        with (
            patch.dict(
                "os.environ",
                {
                    "LOADPILOT_PRICING_FALLBACK_MODE": "baseline",
                    "LOADPILOT_PRICING_UNSEEN_CATEGORY_ACTION": "baseline",
                },
            ),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=payload)
            self.assert_candidate(response, "UNSUPPORTED_MODEL_INPUT")
            self.assertEqual(
                response.json()["baseline_reference"]["reference_source"], "global_median"
            )

    def test_bad_units_missing_values_or_fixed_rate_never_reach_fallback(self):
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            TestClient(self.app) as client,
            patch("loadpilot_model.inference.fallback.BaselineFallback.recommend") as recommend,
        ):
            for change in (
                {"units": {"distance": "km"}},
                {"distance_miles": None},
                {"weight_lbs": 0},
                {"order_type": "fixed_rate"},
            ):
                self.assertEqual(
                    client.post("/api/v1/predict-bid", json={**self.payload, **change}).status_code,
                    422,
                )
            recommend.assert_not_called()

    def test_corrupt_fallback_invalid_config_or_fallback_error_cannot_emit_price(self):
        self.http.factory.side_effect = ValueError("invalid model")
        path = self.artifacts.bundle / "baselines/lane-median-v1.json"
        path.write_bytes(path.read_bytes() + b"changed")
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("predicted_bid_rpm", response.text)
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "typo"}),
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_FALLBACK_POLICY")

    def test_fallback_runtime_exception_reviews_without_price(self):
        self.http.predictor.predict.side_effect = RuntimeError("model failed")
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            TestClient(self.app) as client,
            patch(
                "loadpilot_model.inference.fallback.BaselineFallback.recommend",
                side_effect=RuntimeError("baseline failed"),
            ),
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("predicted_bid_rpm", response.text)
