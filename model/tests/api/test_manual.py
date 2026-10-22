"""Backend integration contract: manual economics cannot become model features."""

import copy
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from loadpilot_model.api.pricing import create_pricing_router
from loadpilot_model.inference.errors import PredictionInputError
from loadpilot_model.paths import PROJECT_ROOT
from tests.api import test_fallback, test_pricing
from tests.api.test_ranges import history

# The published API examples live outside this repository. Skip the contract
# examples rather than failing the suite when they are absent.
if not (PROJECT_ROOT / "requirements/docs/api/examples").is_dir():
    raise unittest.SkipTest("API example fixtures are not part of this checkout")


class ManualModeApiTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.http = test_pricing.PricingApiTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.request = {
            "shipment": self.http.payload,
            "manual_bid_dollars": 2000.12,
            "backend_economics": {
                "total_estimated_cost_dollars": 1400,
                "gross_profit_dollars": 600.12,
                "margin_percent": 30.0042,
            },
        }

    def test_manual_bid_and_economics_survive_changing_predictions_without_feature_leakage(self):
        original = copy.deepcopy(self.request)
        with TestClient(self.http.app) as client:
            for dollars in (300, 3000):
                self.http.predictor.predict.return_value.update(
                    predicted_bid_dollars=dollars, predicted_bid_rpm=dollars / 100
                )
                response = client.post("/api/v1/manual-mode/evaluate", json=self.request)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["manual_bid_dollars"], 2000.12)
                self.assertEqual(body["backend_economics"]["gross_profit_dollars"], 600.12)
                self.assertEqual(body["backend_economics"]["margin_percent"], 30.0042)
                self.assertFalse(body["economics_verified"])
                self.assertFalse(body["submission_enabled"])
                self.assertAlmostEqual(
                    body["comparison"]["manual_minus_advisory_dollars"], 2000.12 - dollars
                )
                self.http.predictor.predict.assert_called_with(self.http.payload)
                self.assertIn("X-Prediction-Id", response.headers)
        self.assertEqual(self.request, original)

    def test_model_load_failure_is_nested_unavailable_and_keeps_manual_economics(self):
        self.http.factory.side_effect = RuntimeError("failed")
        with TestClient(self.http.app) as client:
            response = client.post("/api/v1/manual-mode/evaluate", json=self.request)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["advisory"]["status"], "unavailable")
        self.assertEqual(body["advisory"]["http_status"], 503)
        self.assertEqual(body["advisory"]["error"]["code"], "MODEL_UNAVAILABLE")
        self.assertEqual(body["backend_economics"]["gross_profit_dollars"], 600.12)
        self.assertIsNone(body["comparison"]["manual_minus_advisory_dollars"])

    def test_runtime_failure_and_unseen_categories_leave_manual_context_available(self):
        with TestClient(self.http.app) as client:
            for error, code in (
                (RuntimeError("private failure"), "MODEL_ERROR"),
                (PredictionInputError("UNSEEN_CATEGORY", []), "UNSEEN_CATEGORY"),
            ):
                self.http.predictor.predict.side_effect = error
                response = client.post("/api/v1/manual-mode/evaluate", json=self.request)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["advisory"]["error"]["code"], code)
                self.assertEqual(response.json()["manual_bid_dollars"], 2000.12)
                self.assertNotIn("private failure", response.text)

    def test_missing_costs_are_echoed_as_unknown_and_losses_are_allowed(self):
        with TestClient(self.http.app) as client:
            for economics in (
                dict(
                    total_estimated_cost_dollars=None,
                    gross_profit_dollars=None,
                    margin_percent=None,
                ),
                dict(
                    total_estimated_cost_dollars=3000,
                    gross_profit_dollars=-999.88,
                    margin_percent=-49.991,
                ),
            ):
                response = client.post(
                    "/api/v1/manual-mode/evaluate",
                    json={**self.request, "backend_economics": economics},
                )
                self.assertEqual(response.status_code, 200)
                for key, value in economics.items():
                    self.assertEqual(response.json()["backend_economics"][key], value)
                self.assertEqual(
                    bool(response.json()["warnings"]), economics["margin_percent"] is None
                )

    def test_supplied_policy_controls_display_warnings_without_authorizing_actions(self):
        request = {
            **self.request,
            "warning_policy": {
                "minimum_profit_dollars": 700,
                "minimum_margin_percent": 40,
                "maximum_reference_deviation_percent": 10,
            },
        }
        with TestClient(self.http.app) as client:
            body = client.post("/api/v1/manual-mode/evaluate", json=request).json()
        self.assertEqual(
            {w["code"] for w in body["warnings"]},
            {"BELOW_MINIMUM_PROFIT", "BELOW_MINIMUM_MARGIN", "REFERENCE_DEVIATION"},
        )
        self.assertFalse(body["submission_enabled"])
        self.assertEqual(body["manual_bid_dollars"], 2000.12)

    def test_invalid_contract_and_action_fields_are_rejected_before_inference(self):
        with TestClient(self.http.app) as client:
            for change in (
                {"manual_bid_dollars": "2000.12"},
                {"manual_bid_dollars": True},
                {"manual_bid_dollars": 0},
                {"submit": True},
                {"unit_id": "fake"},
                {
                    "backend_economics": {
                        **self.request["backend_economics"],
                        "margin_percent": "30",
                    }
                },
                {"shipment": {**self.http.payload, "bid_rate": 2000.12}},
                {"shipment": {**self.http.payload, "units": {"currency": "CAD"}}},
            ):
                response = client.post(
                    "/api/v1/manual-mode/evaluate", json={**self.request, **change}
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "INVALID_REQUEST")
        self.http.predictor.predict.assert_not_called()

    def test_openapi_documents_nested_economics_and_both_endpoints(self):
        with TestClient(self.http.app) as client:
            spec = client.get("/openapi.json").json()
        self.assertIn("/api/v1/predict-bid", spec["paths"])
        self.assertIn("/api/v1/manual-mode/evaluate", spec["paths"])
        schemas = spec["components"]["schemas"]
        self.assertIn("backend_economics", schemas["ManualModeRequest"]["required"])
        self.assertIn("advisory", schemas["ManualModeResponse"]["required"])

    def test_documented_json_examples_match_current_runtime_schemas(self):
        from loadpilot_model.api.manual import ManualModeRequest, ManualModeResponse

        examples = PROJECT_ROOT / "requirements/docs/api/examples"
        request = ManualModeRequest.model_validate_json(
            (examples / "manual-mode-request.json").read_text()
        )
        for name in ("manual-mode-response.json", "manual-mode-unavailable-response.json"):
            response = ManualModeResponse.model_validate_json((examples / name).read_text())
            self.assertEqual(response.manual_bid_dollars, request.manual_bid_dollars)
            self.assertEqual(response.backend_economics, request.backend_economics)

    def test_invalid_economics_source_has_clear_field_code(self):
        request = copy.deepcopy(self.request)
        request["backend_economics"]["source"] = "model"
        with TestClient(self.http.app) as client:
            response = client.post("/api/v1/manual-mode/evaluate", json=request)
        self.assertEqual(response.status_code, 422)
        error = response.json()["detail"]["errors"][0]
        self.assertEqual(error["field"], "backend_economics.source")
        self.assertEqual(error["code"], "INVALID_VALUE")

    def test_manual_values_are_absent_from_structured_prediction_logs(self):
        with self.assertLogs("loadpilot_model.api.pricing", level="INFO") as logs:
            with TestClient(self.http.app) as client:
                client.post("/api/v1/manual-mode/evaluate", json=self.request)
        text = "\n".join(logs.output)
        for private in ("2000.12", "600.12", "30.0042", "gross_profit_dollars"):
            self.assertNotIn(private, text)
        events = [json.loads(record.getMessage()) for record in logs.records]
        event = next(e for e in events if e["event"] == "pricing_request")
        self.assertEqual(event["endpoint"], "/api/v1/manual-mode/evaluate")

    def test_unavailable_advisory_is_logged_as_failure_even_with_http_200(self):
        self.http.predictor.predict.side_effect = RuntimeError("failed")
        with self.assertLogs("loadpilot_model.api.pricing", level="INFO") as logs:
            with TestClient(self.http.app) as client:
                client.post("/api/v1/manual-mode/evaluate", json=self.request)
        events = [json.loads(record.getMessage()) for record in logs.records]
        event = next(e for e in events if e["event"] == "pricing_request")
        self.assertEqual(event["outcome"], "advisory_unavailable")
        self.assertEqual(event["error_code"], "MODEL_ERROR")

    def test_explicit_baseline_fallback_retains_manual_economics_and_source_identity(self):
        fixture = test_fallback.PricingFallbackApiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.dict(
            "os.environ",
            {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline", "LOADPILOT_PRICING_MODEL_BLOCKED": "true"},
        ):
            with TestClient(fixture.app) as client:
                body = client.post("/api/v1/manual-mode/evaluate", json=self.request).json()
        self.assertEqual(body["advisory"]["pricing_source"], "baseline")
        self.assertIsNone(body["advisory"]["model_version"])
        self.assertEqual(body["manual_bid_dollars"], 2000.12)
        self.assertEqual(body["backend_economics"]["gross_profit_dollars"], 600.12)

    def test_historical_range_is_independent_of_model_price_and_survives_model_failure(self):
        app = FastAPI()
        app.include_router(
            create_pricing_router(
                PROJECT_ROOT / "synthetic-bundle",
                range_path=PROJECT_ROOT / "synthetic-history.json",
            )
        )
        request = copy.deepcopy(self.request)
        request["shipment"]["distance_miles"] = 1000
        request["warning_policy"] = {"minimum_profit_dollars": 250, "minimum_margin_percent": 15}
        with patch(
            "loadpilot_model.baselines.historical_range.HistoricalPricingRange", return_value=history()
        ):
            with TestClient(app) as client:
                previous_range = None
                for price in (1500, 2500, None):
                    if price is None:
                        self.http.predictor.predict.side_effect = RuntimeError("model failed")
                    else:
                        self.http.predictor.predict.return_value.update(
                            predicted_bid_dollars=price, predicted_bid_rpm=price / 1000
                        )
                    body = client.post("/api/v1/manual-mode/evaluate", json=request).json()
                    band = body["recommended_range"]
                    self.assertEqual(band["status"], "available")
                    self.assertEqual(
                        (band["lower_bid_dollars"], band["upper_bid_dollars"]), (1800, 2200)
                    )
                    if previous_range is not None:
                        self.assertEqual(previous_range, band)
                    previous_range = band
                    self.assertEqual(body["manual_bid_dollars"], 2000.12)
                    self.assertEqual(body["backend_economics"]["gross_profit_dollars"], 600.12)

    def test_corrupt_history_does_not_block_model_or_manual_context(self):
        app = FastAPI()
        app.include_router(
            create_pricing_router(
                PROJECT_ROOT / "synthetic-bundle", range_path=PROJECT_ROOT / "missing-history.json"
            )
        )
        with TestClient(app) as client:
            body = client.post("/api/v1/manual-mode/evaluate", json=self.request).json()
        self.assertEqual(body["advisory"]["pricing_source"], "model")
        self.assertEqual(body["recommended_range"]["status"], "unavailable")
        self.assertEqual(body["manual_bid_dollars"], 2000.12)


if __name__ == "__main__":
    unittest.main()
