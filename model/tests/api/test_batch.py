"""Batch API checks profit allocation, input integrity and expiration after inference."""

import copy
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from loadpilot_model.api.pricing import create_pricing_router
from tests.api import test_pricing
from tests.api.test_autopilot import json_payload
from tests.decision.fixtures import NOW, request


class BatchApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pricing.PricingApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        advisory = self.fixture.predictor.predict.return_value
        advisory.update(predicted_bid_dollars=2000, predicted_bid_rpm=2)
        advisory["baseline_reference"].update(predicted_bid_dollars=2000, predicted_bid_rpm=2)
        for target in (
            "loadpilot_model.api.autopilot.utc_now",
            "loadpilot_model.api.batch.utc_now",
            "loadpilot_model.api.live.utc_now",
        ):
            clock = patch(target, return_value=NOW)
            clock.start()
            self.addCleanup(clock.stop)
        env = patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.token = "synthetic-internal-backend-token-for-tests"
        self.headers = {"Authorization": f"Bearer {self.token}"}
        import os

        os.environ.update(
            LOADPILOT_AUTOPILOT_LIVE_ENABLED="true",
            LOADPILOT_AUTOPILOT_KILL_SWITCH="false",
            LOADPILOT_AUTOPILOT_LIVE_TOKEN=self.token,
        )
        self.fixture.predictor.loaded = SimpleNamespace(
            manifest={
                "model_version": "synthetic-rpm-v1",
                "bundle_id": "a" * 64,
            }
        )
        self.app = FastAPI()
        self.app.include_router(create_pricing_router(Path("synthetic-bundle")))
        source = json_payload(request())
        self.body = dict(
            batch_id="batch-1",
            fleet_snapshot_version="snapshot-example-v1",
            policy=source["policy"],
            control=source["control"],
            opportunities=[
                self.pair("A", "X", 900),
                self.pair("A", "Y", 800),
                self.pair("B", "X", 700),
            ],
        )

    def pair(self, shipment, unit, profit):
        source = json_payload(request())
        context = source["backend_context"]
        context.update(shipment_id=shipment, unit_id=unit, loaded_cost_dollars=2000 - profit - 200)
        return dict(
            shipment_id=shipment, unit_id=unit, shipment=source["shipment"], backend_context=context
        )

    def post(self, body=None):
        with TestClient(self.app) as client:
            return client.post(
                "/api/v1/autopilot/analyze-batch", json=body or self.body, headers=self.headers
            )

    def test_allocates_best_combination_and_prices_each_order_once(self):
        self.body["strategy"] = "fleet_allocation"
        result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        chosen = [
            (row["evaluation"]["shipment_id"], row["evaluation"]["unit_id"])
            for row in body["evaluations"]
            if row["selected"]
        ]
        self.assertEqual(chosen, [("A", "Y"), ("B", "X")])
        self.assertEqual(body["total_projected_profit_dollars"], 1500)
        self.assertEqual(body["total_estimated_cost_dollars"], 2500)
        self.assertEqual(body["total_proposed_revenue_dollars"], 4000)
        self.assertEqual(body["projected_margin_percent"], 37.5)
        self.assertEqual(self.fixture.predictor.predict.call_count, 2)
        self.assertFalse(body["submission_enabled"])
        self.assertFalse(body["reservations_created"])
        self.assertFalse(body["journal_recorded"])

    def test_default_strategy_recommends_one_best_order_from_the_batch(self):
        self.body["orders"] = self.body.pop("opportunities")
        result = self.post()
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["strategy"], "best_order")
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["recommended_order"]["shipment_id"], "A")
        self.assertEqual(body["recommended_order"]["unit_id"], "X")
        self.assertEqual(body["total_projected_profit_dollars"], 900)
        self.assertEqual([row["shipment_id"] for row in body["order_ranking"]], ["A", "B"])
        self.assertIn("historical_reference", body["recommended_order"])

    def test_historical_price_and_current_cost_determine_profit_not_highest_bid(self):
        # A higher history-based price can still produce less projected profit.
        self.body["policy"]["min_margin_percent"] = 10
        self.body["opportunities"] = [self.pair("A", "X", 600), self.pair("B", "X", 900)]
        self.body["opportunities"][0]["shipment"]["distance_miles"] = 1500
        self.body["opportunities"][0]["backend_context"]["loaded_cost_dollars"] = 1800
        template = copy.deepcopy(self.fixture.predictor.predict.return_value)

        def history_price(features):
            advice = copy.deepcopy(template)
            price = 2400 if features["distance_miles"] == 1500 else 2000
            advice.update(
                predicted_bid_dollars=price, predicted_bid_rpm=price / features["distance_miles"]
            )
            advice["baseline_reference"].update(
                predicted_bid_dollars=price, predicted_bid_rpm=price / features["distance_miles"]
            )
            return advice

        self.fixture.predictor.predict.side_effect = history_price
        result = self.post().json()
        self.assertEqual(result["recommended_order"]["shipment_id"], "B")
        self.assertEqual(result["recommended_order"]["projected_profit_dollars"], 900)
        self.assertEqual(result["order_ranking"][1]["suggested_bid_dollars"], 2400)
        self.assertEqual(result["order_ranking"][1]["projected_profit_dollars"], 400)

    def test_more_profitable_expired_order_is_not_recommended(self):
        self.body["opportunities"] = [self.pair("A", "X", 900), self.pair("B", "X", 700)]
        self.body["opportunities"][0]["backend_context"]["order_expires_at"] = NOW.isoformat()
        result = self.post().json()
        self.assertEqual(result["recommended_order"]["shipment_id"], "B")
        self.assertEqual(len(result["order_ranking"]), 1)

    def test_conflicting_or_duplicate_snapshots_are_rejected(self):
        mutations = [
            lambda b: b["opportunities"].append(copy.deepcopy(b["opportunities"][0])),
            lambda b: b["opportunities"][0]["backend_context"].update(snapshot_version="other"),
            lambda b: b["opportunities"][1]["shipment"].update(weight_lbs=4000),
            lambda b: b["opportunities"][2]["backend_context"]["projected_position"].update(
                latitude=20
            ),
            lambda b: b["opportunities"][0]["backend_context"].update(unit_id="other"),
            lambda b: b.update(mode="shadow"),
        ]
        for change in mutations:
            with self.subTest(change=change):
                body = copy.deepcopy(self.body)
                change(body)
                self.assertEqual(self.post(body).status_code, 422)

    def test_expiration_during_pricing_excludes_orders(self):
        # All snapshots are valid at prediction start, then orders expire.
        for item in self.body["opportunities"]:
            item["backend_context"]["order_expires_at"] = (NOW + timedelta(seconds=1)).isoformat()
        with patch("loadpilot_model.api.batch.utc_now", return_value=NOW + timedelta(seconds=2)):
            result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["selected_count"], 0)
        self.assertTrue(
            all(
                "ORDER_EXPIRED" in row["evaluation"]["reason_codes"]
                for row in result["evaluations"]
            )
        )

    def test_unknown_costs_require_review_without_assumed_profit(self):
        self.body["opportunities"] = [self.pair("A", "X", 900)]
        self.body["opportunities"][0]["backend_context"]["deadhead_cost_dollars"] = None
        result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["selected_count"], 0)
        self.assertIsNone(result["evaluations"][0]["evaluation"]["estimated_profit_dollars"])

    def test_disabled_autopilot_never_predicts_or_allocates(self):
        self.body["control"]["autopilot_enabled"] = False
        result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["selected_count"], 0)
        self.fixture.predictor.predict.assert_not_called()

    def test_total_analysis_timeout_returns_review_without_partial_allocations(self):
        advisory = copy.deepcopy(self.fixture.predictor.predict.return_value)

        def slow(*args, **kwargs):
            time.sleep(0.02)
            return advisory

        self.fixture.predictor.predict.side_effect = slow
        self.body["analysis_timeout_seconds"] = 0.001
        result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["reason_codes"], ["BATCH_ANALYSIS_TIMEOUT"])
        self.assertEqual(result["selected_count"], 0)
        self.assertIsNone(result["total_projected_profit_dollars"])
        self.assertFalse(result["allocation_optimal_for_eligible_pairs"])

    def test_selection_that_expires_during_allocation_is_withheld(self):
        with patch("loadpilot_model.api.batch.utc_now", side_effect=[NOW, NOW + timedelta(seconds=61)]):
            result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["reason_codes"], ["BATCH_SELECTION_EXPIRED"])
        self.assertEqual(result["selected_count"], 0)
        self.assertIsNone(result["valid_until"])

    def test_model_unavailable_never_creates_a_profit_allocation(self):
        from fastapi import HTTPException

        self.fixture.predictor.predict.side_effect = HTTPException(
            503, detail={"code": "MODEL_UNAVAILABLE", "action": "REVIEW"}
        )
        result = self.post().json()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["selected_count"], 0)
        self.assertTrue(
            all(row["evaluation"]["candidate_bid_dollars"] is None for row in result["evaluations"])
        )
