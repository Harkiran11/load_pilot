"""Live-only authorization works without journals, outcome evidence or approval files."""

import copy
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from loadpilot_model.api.live import LiveSingleRequest
from loadpilot_model.decision.engine import fingerprint
from tests.api import test_batch
from tests.decision.fixtures import NOW


class LiveApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_batch.BatchApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.token = self.fixture.token
        self.single = {
            **copy.deepcopy(self.fixture.body["opportunities"][0]),
            "policy": self.fixture.body["policy"],
            "control": self.fixture.body["control"],
            "mode": "live",
        }

    def post(self, body=None, batch=False, token=None, alias=False):
        prefix = "/api/v1/autopilot" if alias else "/api/v1/autopilot/live"
        path = "analyze-batch" if batch else "evaluate"
        headers = {"Authorization": f"Bearer {token or self.token}"}
        with TestClient(self.fixture.app) as client:
            return client.post(f"{prefix}/{path}", json=body or self.single, headers=headers)

    def test_live_bid_needs_no_approval_or_historical_evidence(self):
        # A stale deployment setting must not restore the removed prerequisite.
        with patch.dict(
            os.environ, {"LOADPILOT_AUTOPILOT_LIVE_APPROVAL_FILE": "/nonexistent/removed.json"}
        ):
            result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["decision"], "BID")
        self.assertTrue(body["eligible_for_live_submission"])
        self.assertFalse(body["submission_enabled"])
        self.assertEqual(body["authorization_source"], "server_configuration")
        self.assertEqual(body["model_bundle_id"], "a" * 64)
        self.assertTrue(body["backend_revalidation_required"])
        self.assertNotIn("release_approval_id", body)
        self.assertEqual(
            body["live_request_fingerprint"],
            fingerprint(LiveSingleRequest.model_validate(self.single).model_dump()),
        )
        self.assertEqual(body["advisory"]["release_status"], "unverified")

    def test_all_autopilot_routes_are_authenticated_live_routes(self):
        batch = copy.deepcopy(self.fixture.body)
        batch.update(mode="live", strategy="fleet_allocation")
        for alias in (False, True):
            self.assertTrue(self.post(alias=alias).json()["eligible_for_live_submission"])
            result = self.post(batch, batch=True, alias=alias)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["selected_count"], 2)
            self.assertTrue(
                all(
                    row["selected"] == row["evaluation"]["eligible_for_live_submission"]
                    for row in result.json()["evaluations"]
                )
            )
        with TestClient(self.fixture.app) as client:
            for path in ("evaluate", "live/evaluate", "analyze-batch", "live/analyze-batch"):
                body = batch if path.endswith("analyze-batch") else self.single
                self.assertEqual(
                    client.post(f"/api/v1/autopilot/{path}", json=body).status_code, 401
                )

    def test_removed_endpoints_absent_and_removed_mode_rejected(self):
        with TestClient(self.fixture.app) as client:
            schema = client.get("/openapi.json").json()
            self.assertFalse(any("shadow" in path for path in schema["paths"]))
            for path in ("evaluate", "outcomes", "report"):
                result = (
                    client.get(f"/api/v1/shadow/{path}")
                    if path == "report"
                    else client.post(f"/api/v1/shadow/{path}", json={})
                )
                self.assertEqual(result.status_code, 404)
        for batch in (False, True):
            body = copy.deepcopy(self.fixture.body if batch else self.single)
            body["mode"] = "shadow"
            self.assertEqual(self.post(body, batch=batch).status_code, 422)

    def test_disabled_kill_switch_missing_token_and_wrong_token_block_before_inference(self):
        for setting, value, code in (
            ("LOADPILOT_AUTOPILOT_LIVE_ENABLED", "false", "LIVE_DISABLED"),
            ("LOADPILOT_AUTOPILOT_KILL_SWITCH", "true", "LIVE_KILL_SWITCH_ACTIVE"),
            ("LOADPILOT_AUTOPILOT_LIVE_TOKEN", "", "LIVE_AUTH_NOT_CONFIGURED"),
            ("LOADPILOT_AUTOPILOT_ORDER_TYPES", "unknown", "LIVE_CONFIGURATION_INVALID"),
        ):
            with self.subTest(setting=setting), patch.dict(os.environ, {setting: value}):
                result = self.post()
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["detail"]["code"], code)
        self.assertEqual(self.post(token="wrong").status_code, 401)
        self.fixture.fixture.predictor.predict.assert_not_called()

    def test_kill_switch_change_during_inference_blocks_finished_candidate(self):
        advice = copy.deepcopy(self.fixture.fixture.predictor.predict.return_value)

        def kill(*args):
            os.environ["LOADPILOT_AUTOPILOT_KILL_SWITCH"] = "true"
            return advice

        self.fixture.fixture.predictor.predict.side_effect = kill
        result = self.post()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["detail"]["code"], "LIVE_KILL_SWITCH_ACTIVE")

    def test_configuration_change_during_inference_requires_reevaluation(self):
        advice = copy.deepcopy(self.fixture.fixture.predictor.predict.return_value)

        def change(*args):
            os.environ["LOADPILOT_AUTOPILOT_ORDER_TYPES"] = "open_bid"
            return advice

        self.fixture.fixture.predictor.predict.side_effect = change
        result = self.post()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["detail"]["code"], "LIVE_AUTHORIZATION_CHANGED")

    def test_fixed_rates_work_without_separate_release_review_and_can_be_disabled(self):
        body = copy.deepcopy(self.fixture.body)
        body["opportunities"] = [body["opportunities"][0]]
        body["opportunities"][0]["shipment"]["order_type"] = "fixed_rate"
        body["opportunities"][0]["offered_rate_dollars"] = 1800
        with patch.dict(os.environ, {"LOADPILOT_AUTOPILOT_ORDER_TYPES": "open_bid"}):
            result = self.post(body, batch=True)
            self.assertEqual(result.json()["detail"]["code"], "LIVE_ORDER_TYPE_DISABLED")
        result = self.post(body, batch=True)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["eligible_for_live_submission"])
        self.assertEqual(result.json()["total_proposed_revenue_dollars"], 1800)

    def test_skip_review_and_expired_decisions_never_authorize_submission(self):
        for cost, expected in ((3000, "SKIP"), (None, "REVIEW")):
            body = copy.deepcopy(self.single)
            body["backend_context"]["deadhead_cost_dollars"] = cost
            result = self.post(body)
            self.assertEqual(result.json()["decision"], expected)
            self.assertFalse(result.json()["eligible_for_live_submission"])
        with patch("loadpilot_model.api.live.utc_now", return_value=NOW + timedelta(seconds=61)):
            result = self.post()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["detail"]["code"], "LIVE_DECISION_EXPIRED")
