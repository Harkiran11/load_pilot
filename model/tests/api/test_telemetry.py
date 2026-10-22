"""Pricing JSON event schema, redaction, correlation, concurrency and sink failure."""

import copy
import json
import logging
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from loadpilot_model.api import telemetry
from tests.api import test_fallback, test_pricing


class PricingTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pricing.PricingApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.payload = self.fixture.payload
        self.app = self.fixture.app

    def events(self, captured):
        for record in captured.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
        return [json.loads(record.getMessage()) for record in captured.records]

    def test_success_logs_versions_latency_and_generated_correlation_without_payload(self):
        payload = {**self.payload, "lane_id": "PRIVATE-LANE-TEST-SECRET"}
        with (
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(self.app) as client,
        ):
            response = client.post(
                "/api/v1/predict-bid?private=QUERY_SECRET_987",
                json=payload,
                headers={
                    "Authorization": "Bearer AUTH_SECRET_987",
                    "X-Request-Id": "CLIENT_SECRET_987",
                },
            )
            self.assertEqual(response.status_code, 200)
        events = self.events(logs)
        completed = [event for event in events if event["event"] == "pricing_request"]
        self.assertEqual(len(completed), 1)
        event = completed[0]
        self.assertRegex(event["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(event["request_id"], response.headers["X-Prediction-Id"])
        self.assertEqual(event["model_version"], "synthetic-rpm-v1")
        self.assertEqual(event["attempted_model_version"], "synthetic-rpm-v1")
        self.assertEqual(event["baseline_policy_version"], "baseline-fallback-v1")
        self.assertEqual(event["support_policy_version"], "pricing-support-v1")
        self.assertEqual(event["outcome"], "model_advisory")
        self.assertGreaterEqual(event["latency_ms"], 0)
        self.assertEqual(event["http_status"], 200)
        self.assertIsNotNone(datetime.fromisoformat(event["timestamp"]).tzinfo)
        for secret in (
            "PRIVATE-LANE-TEST-SECRET",
            "QUERY_SECRET_987",
            "AUTH_SECRET_987",
            "CLIENT_SECRET_987",
        ):
            self.assertNotIn(secret, json.dumps(events))
        for field in (
            "shipment_id",
            "lane_id",
            "distance_miles",
            "weight_lbs",
            "predicted_bid_rpm",
            "predicted_bid_dollars",
            "headers",
            "body",
            "input",
            "query",
            "client_ip",
        ):
            self.assertNotIn(field, event)
        self.assertIsNone(telemetry.current_log.get())

    def test_validation_errors_log_codes_without_unknown_field_names_or_values(self):
        with (
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(self.app) as client,
        ):
            response = client.post(
                "/api/v1/predict-bid",
                json={
                    **self.payload,
                    "PRIVATE_EXTRA_KEY_987": "PRIVATE_EXTRA_VALUE_987",
                    "units": {"distance": "km"},
                },
            )
        self.assertEqual(response.status_code, 422)
        events = self.events(logs)
        event = next(e for e in events if e["event"] == "pricing_request")
        self.assertEqual(event["error_code"], "INVALID_REQUEST")
        self.assertEqual(
            set(event["validation_error_codes"]), {"UNEXPECTED_FIELD", "UNSUPPORTED_UNIT"}
        )
        self.assertEqual(event["request_id"], response.headers["X-Prediction-Id"])
        self.assertNotIn("PRIVATE_EXTRA", json.dumps(events))

    def test_exception_message_and_trace_are_omitted_but_type_and_attempt_are_logged(self):
        self.fixture.predictor.loaded = SimpleNamespace(metadata={"model_version": "attempted-v1"})
        self.fixture.predictor.predict.side_effect = RuntimeError(
            "RAW_EXCEPTION_SECRET_987\nprivate shipment"
        )
        with (
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
        events = self.events(logs)
        diagnostic = next(e for e in events if e["event"] == "pricing_diagnostic")
        completed = next(e for e in events if e["event"] == "pricing_request")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(diagnostic["exception_type"], "RuntimeError")
        self.assertEqual(diagnostic["request_id"], completed["request_id"])
        self.assertEqual(completed["attempted_model_version"], "attempted-v1")
        self.assertEqual(completed["failure_trigger"], "MODEL_ERROR")
        self.assertEqual(completed["error_code"], "MODEL_ERROR")
        self.assertNotIn("RAW_EXCEPTION_SECRET_987", json.dumps(events))

    def test_startup_failure_diagnostics_do_not_expose_bundle_paths_or_exception_text(self):
        self.fixture.factory.side_effect = OSError("/private/SENSITIVE_BUNDLE_PATH_987")
        with (
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(self.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
        events = self.events(logs)
        startup = next(e for e in events if e["event"] == "pricing_startup")
        self.assertFalse(startup["model_loaded"])
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SENSITIVE_BUNDLE_PATH_987", json.dumps(events))

    def test_concurrent_requests_keep_ids_and_versions_separate(self):
        barrier = threading.Barrier(2)
        response_template = copy.deepcopy(self.fixture.predictor.predict.return_value)

        def prediction(features):
            result = copy.deepcopy(response_template)
            result["model_version"] = (
                "synthetic-a" if features["distance_miles"] == 100 else "synthetic-b"
            )
            barrier.wait(timeout=5)
            return result

        self.fixture.predictor.predict.side_effect = prediction
        with (
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(self.app) as client,
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        client.post,
                        "/api/v1/predict-bid",
                        json={**self.payload, "distance_miles": miles},
                    )
                    for miles in (100.0, 200.0)
                ]
                responses = [future.result(timeout=10) for future in futures]
        events = [e for e in self.events(logs) if e["event"] == "pricing_request"]
        self.assertEqual(len(events), 2)
        by_id = {e["request_id"]: e for e in events}
        self.assertEqual(len(by_id), 2)
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                by_id[response.headers["X-Prediction-Id"]]["model_version"],
                response.json()["model_version"],
            )

    def test_logging_sink_failure_does_not_change_prediction(self):
        with (
            TestClient(self.app) as client,
            patch.object(
                logging.getLogger(telemetry.LOGGER_NAME), "log", side_effect=OSError("sink failure")
            ),
        ):
            response = client.post("/api/v1/predict-bid", json=self.payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["predicted_bid_dollars"], 300)

    def test_configuration_does_not_duplicate_handlers_or_change_root_logger(self):
        root = logging.getLogger()
        before = (root.level, list(root.handlers))
        telemetry.configure_logging()
        telemetry.configure_logging()
        handlers = logging.getLogger(telemetry.LOGGER_NAME).handlers
        self.assertEqual(sum(h.get_name() == "pricing-json" for h in handlers), 1)
        self.assertEqual((root.level, list(root.handlers)), before)


class FallbackTelemetryTests(unittest.TestCase):
    def test_baseline_event_is_correlated_with_source_policy_and_trigger(self):
        fixture = test_fallback.PricingFallbackApiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.http.factory.side_effect = ImportError("MODEL_RUNTIME_SECRET_987")
        with (
            patch.dict("os.environ", {"LOADPILOT_PRICING_FALLBACK_MODE": "baseline"}),
            self.assertLogs(telemetry.LOGGER_NAME, level="INFO") as logs,
            TestClient(fixture.app) as client,
        ):
            response = client.post("/api/v1/predict-bid", json=fixture.payload)
        self.assertEqual(response.status_code, 200)
        events = [json.loads(record.getMessage()) for record in logs.records]
        event = next(e for e in events if e["event"] == "pricing_request")
        self.assertEqual(event["request_id"], response.headers["X-Prediction-Id"])
        self.assertEqual(event["pricing_source"], "baseline")
        self.assertEqual(event["outcome"], "fallback_advisory")
        self.assertIsNone(event["model_version"])
        self.assertEqual(event["baseline_policy_version"], "baseline-fallback-v1")
        self.assertEqual(event["failure_trigger"], "MODEL_UNAVAILABLE")
        self.assertEqual(event["warning_codes"], ["BASELINE_FALLBACK"])
        self.assertNotIn("MODEL_RUNTIME_SECRET_987", json.dumps(events))
