"""Known offered revenue stays separate from historical model advice and batch averages."""

import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException

from tests.api import test_batch
from tests.decision.fixtures import NOW


class OfferedBatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_batch.BatchApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.body = copy.deepcopy(self.fixture.body)
        self.body["policy"]["min_margin_percent"] = 10
        self.body["opportunities"] = [
            self.fixed("A", "X", 1000, 3000, 2400),
            self.fixed("A", "Y", 1000, 3000, 2300),
            self.fixed("B", "X", 2000, 3600, 2400),
        ]
        template = copy.deepcopy(self.fixture.fixture.predictor.predict.return_value)

        def history_reference(features):
            value = copy.deepcopy(template)
            price = 2 * features["distance_miles"]
            value.update(predicted_bid_dollars=price, predicted_bid_rpm=2)
            value["baseline_reference"].update(predicted_bid_dollars=price, predicted_bid_rpm=2)
            return value

        self.fixture.fixture.predictor.predict.side_effect = history_reference

    def fixed(self, shipment, unit, miles, offer, loaded):
        item = self.fixture.pair(shipment, unit, 0)
        item["shipment"].update(order_type="fixed_rate", distance_miles=miles)
        item["offered_rate_dollars"] = offer
        item["backend_context"]["loaded_cost_dollars"] = loaded
        return item

    def post(self):
        result = self.fixture.post(self.body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_real_offered_revenue_drives_profit_and_unique_order_rpm_average(self):
        result = self.post()
        stats = result["batch_statistics"]
        self.assertEqual(stats["unique_order_count"], 2)
        self.assertEqual(stats["available_offer_count"], 2)
        self.assertAlmostEqual(stats["average_offered_rpm"], 2.4)
        self.assertAlmostEqual(stats["loaded_mile_weighted_offered_rpm"], 2.2)
        self.assertEqual(stats["average_model_reference_rpm"], 2)
        chosen = result["recommended_order"]
        self.assertEqual(chosen["shipment_id"], "B")
        self.assertEqual(chosen["pricing_source"], "offered_rate")
        self.assertEqual(chosen["suggested_bid_dollars"], 3600)
        self.assertEqual(chosen["projected_profit_dollars"], 1000)
        self.assertEqual(result["total_proposed_revenue_dollars"], 3600)
        self.assertEqual(chosen["comparison"]["model_reference_bid_dollars"], 4000)
        self.assertEqual(chosen["comparison"]["model_reference_projected_profit_dollars"], 1400)
        self.assertAlmostEqual(chosen["comparison"]["difference_from_batch_average_rpm"], -0.6)
        self.assertAlmostEqual(chosen["comparison"]["difference_from_model_percent"], -10)
        self.assertIn(
            "FIXED_RATE_REFERENCE_NOT_VALIDATED", chosen["comparison"]["reference_warnings"]
        )
        self.assertEqual(self.fixture.fixture.predictor.predict.call_count, 2)
        for call in self.fixture.fixture.predictor.predict.call_args_list:
            self.assertNotIn("offered_rate_dollars", call.args[0])
            self.assertNotIn("loaded_cost_dollars", call.args[0])

    def test_fleet_strategy_optimizes_actual_offer_profit_under_truck_limits(self):
        self.body["strategy"] = "fleet_allocation"
        result = self.post()
        self.assertEqual(result["selected_count"], 2)
        self.assertEqual(result["total_projected_profit_dollars"], 1500)
        selected = [
            (r["evaluation"]["shipment_id"], r["evaluation"]["unit_id"])
            for r in result["evaluations"]
            if r["selected"]
        ]
        self.assertEqual(selected, [("A", "Y"), ("B", "X")])

    def test_expired_closed_or_stale_offers_do_not_bias_current_batch_average(self):
        original = copy.deepcopy(self.body)
        for state in ("expired", "closed", "stale"):
            self.body = copy.deepcopy(original)
            for item in self.body["opportunities"][:2]:
                context = item["backend_context"]
                if state == "expired":
                    context["order_expires_at"] = NOW.isoformat()
                elif state == "closed":
                    context["checks"]["order_open"] = False
                else:
                    context["order_observed_at"] = (NOW - timedelta(seconds=301)).isoformat()
            result = self.post()
            self.assertEqual(result["batch_statistics"]["available_offer_count"], 1)
            self.assertEqual(result["batch_statistics"]["average_offered_rpm"], 1.8)
            self.assertEqual(result["recommended_order"]["shipment_id"], "B")
            self.assertTrue(
                all(
                    not row["comparison"]["included_in_batch_average"]
                    for row in result["evaluations"][:2]
                )
            )

    def test_missing_cost_blocks_selection_but_keeps_known_offer_in_price_average(self):
        for item in self.body["opportunities"][:2]:
            item["backend_context"]["deadhead_cost_dollars"] = None
        result = self.post()
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["batch_statistics"]["available_offer_count"], 2)
        self.assertEqual(result["recommended_order"]["shipment_id"], "B")
        self.assertTrue(result["evaluations"][0]["requires_review"])
        self.assertEqual(result["evaluations"][0]["evaluation"]["decision"], "SKIP")
        self.assertIsNone(result["evaluations"][0]["evaluation"]["estimated_profit_dollars"])

    def test_missing_context_on_one_pair_does_not_hide_valid_pair_for_same_order(self):
        self.body["opportunities"] = self.body["opportunities"][:2]
        self.body["opportunities"][0]["backend_context"] = None
        result = self.post()
        self.assertEqual(result["decision"], "BID")
        self.assertEqual(result["recommended_order"]["unit_id"], "Y")
        self.assertEqual(result["batch_statistics"]["available_offer_count"], 1)
        self.assertEqual(result["evaluations"][0]["evaluation"]["decision"], "SKIP")
        self.assertTrue(result["evaluations"][0]["requires_review"])

    def test_model_failure_keeps_known_profit_but_skips_without_history_comparison(self):
        self.fixture.fixture.predictor.predict.side_effect = HTTPException(
            503, detail={"code": "MODEL_UNAVAILABLE", "action": "REVIEW"}
        )
        result = self.post()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["analysis_status"], "partial")
        row = result["evaluations"][0]
        self.assertEqual(row["evaluation"]["estimated_profit_dollars"], 400)
        self.assertTrue(row["requires_review"])
        self.assertIsNone(row["comparison"]["model_reference_rpm"])

    def test_invalid_ambiguous_and_conflicting_offered_amounts_are_rejected(self):
        original = copy.deepcopy(self.body)
        for value in (None, 0, -1, True, 3000.001):
            self.body = copy.deepcopy(original)
            self.body["opportunities"][0]["offered_rate_dollars"] = value
            self.assertEqual(self.fixture.post(self.body).status_code, 422)
        self.body = copy.deepcopy(original)
        self.body["opportunities"][1]["offered_rate_dollars"] = 3100
        self.assertEqual(self.fixture.post(self.body).status_code, 422)
        self.body = copy.deepcopy(original)
        self.body["opportunities"][0]["shipment"]["order_type"] = "open_bid"
        self.assertEqual(self.fixture.post(self.body).status_code, 422)

    def test_empty_batch_skips_without_inference(self):
        self.body["opportunities"] = []
        result = self.post()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["reason_codes"], ["EMPTY_BATCH"])
        self.assertEqual(result["batch_statistics"]["unique_order_count"], 0)
        self.assertIsNone(result["batch_statistics"]["average_offered_rpm"])
        self.fixture.fixture.predictor.predict.assert_not_called()

    def test_open_bid_orders_still_use_model_prices_not_fixed_offer_average(self):
        self.body["opportunities"].append(self.fixture.pair("C", "Z", 700))
        result = self.post()
        self.assertEqual(result["batch_statistics"]["unique_order_count"], 3)
        self.assertEqual(result["batch_statistics"]["available_offer_count"], 2)
        self.assertAlmostEqual(result["batch_statistics"]["average_offered_rpm"], 2.4)
        row = result["evaluations"][-1]
        self.assertEqual(row["comparison"]["revenue_source"], "model")
        self.assertIsNone(row["comparison"]["offered_rpm"])

    def test_unrepresentable_aggregate_cannot_emit_invalid_money_or_selection(self):
        self.body["strategy"] = "fleet_allocation"
        self.body["opportunities"] = [
            self.fixed("A", "X", 1000, 1e308, 1000),
            self.fixed("B", "Y", 1000, 1e308, 1000),
        ]
        result = self.post()
        self.assertEqual(result["decision"], "SKIP")
        self.assertEqual(result["analysis_status"], "unavailable")
        self.assertEqual(result["reason_codes"], ["BATCH_ARITHMETIC_UNAVAILABLE"])
        self.assertEqual(result["selected_count"], 0)
        self.assertIsNone(result["total_projected_profit_dollars"])

    def test_logs_record_final_offered_decisions_not_hypothetical_reference_decisions(self):
        with (
            patch("loadpilot_model.api.autopilot.write_event") as reference_log,
            patch("loadpilot_model.api.batch.write_event") as batch_log,
        ):
            result = self.post()
        reference_log.assert_not_called()
        events = [call.args[1] for call in batch_log.call_args_list]
        self.assertEqual(len(events), len(result["evaluations"]))
        for event, row in zip(events, result["evaluations"], strict=True):
            self.assertEqual(event["event"], "batch_order_decision")
            self.assertEqual(event["decision_id"], row["evaluation"]["decision_id"])
            self.assertEqual(event["decision"], row["evaluation"]["decision"])
            self.assertEqual(event["selected"], row["selected"])
            self.assertEqual(event["revenue_source"], "offered_rate")
