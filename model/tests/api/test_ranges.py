"""Profitability floors intersect supported historical bands without manufacturing bids."""

import unittest
from types import SimpleNamespace

from loadpilot_model.api.ranges import recommend_range


def history(lower=1.8, upper=2.2):
    return SimpleNamespace(
        describe=lambda lane, miles: {
            "policy_version": "historical-range-v1",
            "artifact_sha256": "a" * 64,
            "source": "lane",
            "sample_count": 30,
            "minimum_samples": 20,
            "distance_band": "[500.0, 1500.0)",
            "training_date_min": "2026-01-01 00:00:00",
            "training_date_max": "2026-01-14 00:00:00",
            "lower_quantile": 0.25,
            "upper_quantile": 0.75,
            "lower_rpm": lower,
            "upper_rpm": upper,
            "lower_bid_dollars": lower * miles,
            "upper_bid_dollars": upper * miles,
        }
    )


class ProfitableRangeTests(unittest.TestCase):
    def recommend(self, **changes):
        values = dict(
            history=history(),
            lane="ATL-DFW",
            miles=1000,
            cost=1400,
            minimum_profit=250,
            minimum_margin=15,
            maximum_bid=None,
        )
        return recommend_range(**{**values, **changes})

    def test_history_is_the_range_when_it_already_meets_policy(self):
        result = self.recommend()
        self.assertEqual((result.lower_bid_dollars, result.upper_bid_dollars), (1800, 2200))
        self.assertEqual(result.minimum_profitable_bid_dollars, 1650)
        self.assertFalse(result.calibrated_prediction_interval)

    def test_minimum_margin_raises_floor_with_upward_cent_rounding(self):
        result = self.recommend(minimum_margin=30)
        self.assertEqual(result.lower_bid_dollars, 2000)
        result = self.recommend(cost=1400.01, minimum_margin=30)
        self.assertEqual(result.minimum_profitable_bid_dollars, 2000.02)

    def test_incompatible_costs_return_no_range_instead_of_loss_making_prices(self):
        result = self.recommend(cost=2300)
        self.assertEqual(result.status, "no_profitable_range")
        self.assertEqual(result.reason, "PROFITABILITY_CONFLICT")
        self.assertIsNone(result.lower_bid_dollars)
        self.assertIsNone(result.upper_bid_dollars)
        self.assertIsNotNone(result.historical_reference)

    def test_configured_maximum_caps_upper_bound_and_can_invalidate_range(self):
        result = self.recommend(maximum_bid=2100.009)
        self.assertEqual(result.upper_bid_dollars, 2100)
        result = self.recommend(maximum_bid=1700)
        self.assertEqual(result.reason, "MAXIMUM_BID_CONFLICT")
        self.assertIsNone(result.lower_bid_dollars)

    def test_unknown_cost_or_policy_never_claims_a_profitable_range(self):
        for values, reason in (
            ({"cost": None}, "COST_UNAVAILABLE"),
            ({"minimum_profit": None}, "PROFITABILITY_POLICY_REQUIRED"),
            ({"minimum_margin": None}, "PROFITABILITY_POLICY_REQUIRED"),
        ):
            result = self.recommend(**values)
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.reason, reason)
            self.assertIsNone(result.lower_bid_dollars)
            self.assertIsNotNone(result.historical_reference)

    def test_history_failure_keeps_floor_but_does_not_invent_a_range(self):
        result = self.recommend(history=None)
        self.assertEqual(result.reason, "HISTORY_UNAVAILABLE")
        self.assertEqual(result.minimum_profitable_bid_dollars, 1650)
        broken = SimpleNamespace(describe=lambda *args: 1 / 0)
        result = self.recommend(history=broken)
        self.assertEqual(result.reason, "HISTORY_ERROR")
        self.assertIsNone(result.lower_bid_dollars)

    def test_zero_thresholds_require_at_least_one_cent_above_break_even(self):
        result = self.recommend(cost=1800, minimum_profit=0, minimum_margin=0)
        self.assertEqual(result.lower_bid_dollars, 1800.01)

    def test_cent_rounding_stays_inside_the_historical_reference(self):
        result = self.recommend(history=history(1.800001, 2.200009))
        self.assertEqual(result.lower_bid_dollars, 1800.01)
        self.assertEqual(result.upper_bid_dollars, 2200)
