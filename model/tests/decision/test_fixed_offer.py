"""Fixed offers retain posted revenue and require an explicit evaluation scope."""

import unittest

from loadpilot_model.decision.engine import evaluate
from tests.decision.fixtures import NOW, candidate, request


class FixedOfferDecisionTests(unittest.TestCase):
    def context(self):
        inputs = request()
        inputs["shipment"]["order_type"] = "fixed_rate"
        inputs["offered_rate_dollars"] = 4800
        price = candidate(4800)
        price.update(pricing_source="offered_rate", reference_pricing_source="model")
        return inputs, price

    def test_fixed_offers_are_opt_in_and_do_not_change_single_order_contract(self):
        inputs, price = self.context()
        result = evaluate(inputs, price, None, NOW)
        self.assertEqual(result["decision"], "SKIP")
        self.assertIn("UNSUPPORTED_ORDER_TYPE", result["reason_codes"])

    def test_fixed_offer_profit_uses_posted_price_without_open_bid_caps(self):
        inputs, price = self.context()
        result = evaluate(inputs, price, None, NOW, allow_fixed_rate=True)
        self.assertEqual(result["decision"], "BID")
        self.assertEqual(result["candidate_bid_dollars"], 4800)
        self.assertEqual(result["estimated_profit_dollars"], 3400)
        self.assertGreater(result["reference_deviation_percent"], 25)

    def test_offer_and_evaluated_amount_must_match(self):
        inputs, price = self.context()
        inputs["offered_rate_dollars"] = 4700
        result = evaluate(inputs, price, None, NOW, allow_fixed_rate=True)
        self.assertEqual(result["decision"], "REVIEW")
        self.assertIn("INVALID_CANDIDATE", result["reason_codes"])

    def test_offered_revenue_cannot_bypass_unapproved_fallback_reference(self):
        inputs, price = self.context()
        price["reference_pricing_source"] = "baseline"
        result = evaluate(inputs, price, None, NOW, allow_fixed_rate=True)
        self.assertEqual(result["decision"], "REVIEW")
        self.assertIn("FALLBACK_NOT_ALLOWED", result["reason_codes"])
