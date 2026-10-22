"""Versioned pricing HTTP contract and shared application lifespan checks."""

import copy
import json
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from loadpilot_model.api.pricing import create_pricing_router
from loadpilot_model.inference.errors import PredictionInputError


class PricingApiTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "distance_miles": 100.0,
            "weight_lbs": 10000.0,
            "origin_latitude": 33.0,
            "origin_longitude": -84.0,
            "destination_latitude": 33.0,
            "destination_longitude": -97.0,
            "lane_id": "ATL-DFW",
            "origin_state": "GA",
            "destination_state": "TX",
        }
        self.predictor = Mock()
        self.predictor.predict.return_value = {
            "api_version": "v1",
            "model_version": "synthetic-rpm-v1",
            "predicted_bid_rpm": 3.0,
            "predicted_bid_dollars": 300.0,
            "currency": "USD",
            "distance_basis": "loaded_miles",
            "advisory_only": True,
            "pricing_source": "model",
            "requires_review": True,
            "submission_enabled": False,
            "requires_guardrail_checks": True,
            "release_status": "unverified",
            "baseline_reference": {
                "baseline_id": "lane_median_bid_rpm",
                "policy_version": "baseline-fallback-v1",
                "reference_source": "lane_median",
                "reason": "SUFFICIENT_TRAINING_SAMPLES",
                "predicted_bid_rpm": 2.9,
                "predicted_bid_dollars": 290.0,
                "lane_training_row_count": 20,
                "min_lane_samples": 5,
            },
            "warnings": [
                {
                    "code": "MODEL_RELEASE_UNVERIFIED",
                    "message": "Review required.",
                    "fields": [],
                    "details": {},
                }
            ],
            "support_policy_version": "pricing-support-v1",
            "uncertainty": {
                "status": "not_calibrated",
                "confidence_level": None,
                "rpm_interval": None,
                "dollar_interval": None,
            },
        }
        self.app = FastAPI()
        self.app.include_router(create_pricing_router(Path("synthetic-bundle")))
        factory = patch(
            "loadpilot_model.inference.predictor.FreightPredictor", return_value=self.predictor
        )
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def test_versioned_prediction_loads_once_and_returns_advisory_price(self):
        before = copy.deepcopy(self.payload)
        with TestClient(self.app) as client:
            for _ in range(2):
                result = client.post("/api/v1/predict-bid", json=self.payload)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), self.predictor.predict.return_value)
            self.assertIn("/api/v1/predict-bid", client.get("/openapi.json").json()["paths"])
        self.factory.assert_called_once_with(Path("synthetic-bundle"))
        self.assertEqual(self.payload, before)
        self.predictor.predict.assert_called_with(self.payload)

    def test_baseline_warning_and_uncertainty_fields_are_returned_and_documented(self):
        with TestClient(self.app) as client:
            response = client.post("/api/v1/predict-bid", json=self.payload)
            body = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(body["baseline_reference"]["predicted_bid_dollars"], 290)
            self.assertEqual(body["warnings"][0]["code"], "MODEL_RELEASE_UNVERIFIED")
            self.assertIsNone(body["uncertainty"]["rpm_interval"])
            self.assertIsNone(body["uncertainty"]["confidence_level"])
            schemas = client.get("/openapi.json").json()["components"]["schemas"]
            for field in ("model_version", "baseline_reference", "warnings", "uncertainty"):
                self.assertIn(field, schemas["BidPredictionResponse"]["required"])

    def test_invalid_features_fixed_rate_and_entered_bid_are_rejected(self):
        with TestClient(self.app) as client:
            for change in (
                {"distance_miles": 0},
                {"weight_lbs": -1},
                {"origin_latitude": 91},
                {"distance_miles": "100"},
                {"distance_miles": True},
                {"order_type": "fixed_rate"},
                {"bid_rate": 300},
                {"won": True},
            ):
                with self.subTest(change=change):
                    self.assertEqual(
                        client.post(
                            "/api/v1/predict-bid", json={**self.payload, **change}
                        ).status_code,
                        422,
                    )
        self.predictor.predict.assert_not_called()

    def test_model_load_failure_keeps_other_routes_available_and_returns_review(self):
        self.factory.side_effect = ValueError("invalid artifact")

        @self.app.get("/ordinary-calculator")
        def ordinary_calculator():
            return {"available": True}

        with (
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["action"], "REVIEW")
            self.assertEqual(client.get("/ordinary-calculator").status_code, 200)

    def test_model_error_returns_no_price_and_review(self):
        self.predictor.predict.side_effect = RuntimeError("failed")
        with (
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            result = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(result.status_code, 503)

            self.assertEqual(result.json()["detail"]["code"], "MODEL_ERROR")
            self.predictor.predict.side_effect = None
            self.predictor.predict.return_value = {"predicted_bid_rpm": -1}
            result = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["detail"], {"code": "MODEL_ERROR", "action": "REVIEW"})

    def test_router_preserves_parent_startup_and_shutdown(self):
        events = []

        @asynccontextmanager
        async def existing_lifespan(app):
            events.append("start")
            yield
            events.append("stop")

        app = FastAPI(lifespan=existing_lifespan)
        app.include_router(create_pricing_router(Path("synthetic-bundle")))
        with TestClient(app) as client:
            self.assertEqual(events, ["start"])
            self.assertEqual(client.post("/api/v1/predict-bid", json=self.payload).status_code, 200)
        self.assertEqual(events, ["start", "stop"])

    def test_unconfigured_model_returns_unavailable(self):
        app = FastAPI()
        app.include_router(create_pricing_router())
        with patch.dict("os.environ", {}, clear=True), TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/predict-bid", json=self.payload).status_code, 503)
        self.factory.assert_not_called()

    def test_missing_null_and_missing_tokens_have_field_errors(self):
        with TestClient(self.app) as client:
            for field in self.payload:
                request = {k: v for k, v in self.payload.items() if k != field}
                result = client.post("/api/v1/predict-bid", json=request).json()["detail"]
                self.assertEqual(result["code"], "INVALID_REQUEST")
                self.assertIn(
                    {
                        "field": field,
                        "code": "MISSING_REQUIRED_FIELD",
                        "message": "A nonmissing value is required.",
                    },
                    result["errors"],
                )
            for value in (None, "", "   ", "NaN", "n/a", "null", "NONE"):
                response = client.post(
                    "/api/v1/predict-bid", json={**self.payload, "lane_id": value}
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"]["errors"][0]["code"], "MISSING_REQUIRED_FIELD"
                )
        self.predictor.predict.assert_not_called()

    def test_error_codes_for_type_range_order_and_extra_fields(self):
        cases = (
            ({"distance_miles": "100"}, "INVALID_TYPE"),
            ({"weight_lbs": True}, "INVALID_TYPE"),
            ({"distance_miles": 0}, "OUT_OF_RANGE"),
            ({"destination_longitude": -181}, "OUT_OF_RANGE"),
            ({"order_type": "fixed_rate"}, "UNSUPPORTED_ORDER_TYPE"),
            ({"bid_rate": 999}, "UNEXPECTED_FIELD"),
        )
        with TestClient(self.app) as client:
            for change, code in cases:
                result = client.post("/api/v1/predict-bid", json={**self.payload, **change})
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json()["detail"]["errors"][0]["code"], code)
                self.assertEqual(result.json()["detail"]["action"], "REVIEW")

    def test_units_are_explicit_and_never_converted(self):
        with TestClient(self.app) as client:
            for name, value in (
                ("distance", "km"),
                ("weight", "kg"),
                ("currency", "CAD"),
                ("coordinates", "radians"),
            ):
                result = client.post(
                    "/api/v1/predict-bid", json={**self.payload, "units": {name: value}}
                )
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json()["detail"]["errors"][0]["code"], "UNSUPPORTED_UNIT")
            self.predictor.predict.assert_not_called()
            result = client.post(
                "/api/v1/predict-bid",
                json={
                    **self.payload,
                    "units": {
                        "distance": "miles",
                        "weight": "lb",
                        "currency": "USD",
                        "coordinates": "degrees",
                    },
                },
            )
            self.assertEqual(result.status_code, 200)
            self.predictor.predict.assert_called_once_with(self.payload)

    def test_category_normalization_preserves_input(self):
        request = {
            **self.payload,
            "lane_id": " atl-dfw ",
            "origin_state": " ga ",
            "destination_state": " tx ",
        }
        before = copy.deepcopy(request)
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/api/v1/predict-bid", json=request).status_code, 200)
        self.predictor.predict.assert_called_once_with(self.payload)
        self.assertEqual(request, before)

    def test_unseen_categories_have_field_errors_and_no_prediction(self):
        errors = [{"field": "lane_id", "code": "UNSEEN_CATEGORY", "message": "Review required."}]
        self.predictor.predict.side_effect = PredictionInputError("UNSEEN_CATEGORY", errors)
        with TestClient(self.app) as client:
            result = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(
                result.json()["detail"],
                {"code": "UNSEEN_CATEGORY", "action": "REVIEW", "errors": errors},
            )
            self.assertNotIn("predicted_bid_rpm", result.text)

    def test_malformed_json_nonfinite_numbers_and_null_body_do_not_leak_inputs(self):
        with TestClient(self.app) as client:
            for body, code in (
                ("{broken", "INVALID_JSON"),
                (json.dumps({**self.payload, "distance_miles": float("nan")}), "NONFINITE_NUMBER"),
                (json.dumps({**self.payload, "weight_lbs": float("inf")}), "NONFINITE_NUMBER"),
                ("null", "MISSING_REQUIRED_FIELD"),
            ):
                response = client.post(
                    "/api/v1/predict-bid",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"]["errors"][0]["code"], code)
                self.assertNotIn('"input"', response.text)
                self.assertNotIn('"ctx"', response.text)

    def test_internal_value_error_or_invalid_output_is_model_failure_not_bad_request(self):
        self.predictor.predict.side_effect = ValueError("broken fitted transform")
        with (
            self.assertLogs("loadpilot_model.api.pricing", level="ERROR"),
            TestClient(self.app) as client,
        ):
            result = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(result.status_code, 503)

    def test_pricing_errors_do_not_change_other_backend_validation(self):
        @self.app.post("/other-backend-route")
        def other_backend_route(value: int):
            return {"value": value}

        with TestClient(self.app) as client:
            ordinary = client.post("/other-backend-route")
            pricing = client.post("/api/v1/predict-bid", json={})
            self.assertEqual(ordinary.status_code, 422)
            self.assertIsInstance(ordinary.json()["detail"], list)
            self.assertIsInstance(pricing.json()["detail"], dict)
            schema = client.get("/openapi.json").json()["paths"]["/api/v1/predict-bid"]["post"]
            self.assertEqual(
                schema["responses"]["422"]["content"]["application/json"]["schema"],
                {"$ref": "#/components/schemas/PricingErrorResponse"},
            )
