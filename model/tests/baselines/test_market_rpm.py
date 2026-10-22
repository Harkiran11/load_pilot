"""Reference validity, bid-time availability, freshness, and provenance gates."""

import unittest

from loadpilot_model.baselines.market_rpm import MarketRPMBaseline


class MarketRPMTests(unittest.TestCase):
    def setUp(self):
        # Synthetic approved-source fixture; no real feed is registered by these tests.
        self.baseline = MarketRPMBaseline(("test-independent-feed",), 3600)
        self.quote = {
            "source_id": "test-independent-feed",
            "quote_id": "Q-1",
            "shipment_id": "S-1",
            "lane_id": "ATL-DFW",
            "market_rpm": "2.75",
            "currency": "USD",
            "rate_basis": "loaded_mile",
            "observed_at": "2026-01-01T10:00:00Z",
            "available_at": "2026-01-01T10:05:00Z",
        }

    def predict(self, quote=None, decision="2026-01-01T11:00:00Z"):
        return self.baseline.predict(
            self.quote if quote is None else quote,
            decision_at=decision,
            shipment_id="S-1",
            lane_id="ATL-DFW",
        )

    def test_valid_quote_used_unchanged_and_age_boundary_is_inclusive(self):
        result = self.predict()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["predicted_bid_rpm"], 2.75)
        self.assertEqual(
            self.predict(decision="2026-01-01T12:00:00+01:00")["predicted_bid_rpm"], 2.75
        )
        result = self.predict(decision="2026-01-01T11:00:01Z")
        self.assertEqual(result["reason"], "STALE_MARKET_QUOTE")
        self.assertIsNone(result["predicted_bid_rpm"])

    def test_observation_is_not_enough_if_quote_became_available_after_bid(self):
        result = self.predict({**self.quote, "available_at": "2026-01-01T11:00:01Z"})
        self.assertEqual(result["reason"], "NOT_AVAILABLE_AT_BID")

    def test_invalid_missing_or_nonpositive_rpm_never_returns_reference(self):
        for value in (None, "", "bad", 0, -1, True, "NaN", "inf"):
            with self.subTest(value=value):
                result = self.predict({**self.quote, "market_rpm": value})
                self.assertEqual(result["reason"], "INVALID_MARKET_RPM")
                self.assertIsNone(result["predicted_bid_rpm"])

    def test_naive_missing_and_inconsistent_timestamps_rejected(self):
        for update, reason in (
            ({"observed_at": None}, "INVALID_MARKET_TIMESTAMP"),
            ({"observed_at": "2026-01-01 10:00:00"}, "INVALID_MARKET_TIMESTAMP"),
            ({"observed_at": "2026-01-01T10:10:00Z"}, "INCONSISTENT_MARKET_TIMESTAMPS"),
        ):
            with self.subTest(update=update):
                self.assertEqual(self.predict({**self.quote, **update})["reason"], reason)

    def test_unapproved_and_offer_derived_sources_rejected(self):
        for source, reason in (
            ("unapproved", "UNAPPROVED_MARKET_SOURCE"),
            ("historical_csv.market_rpm", "OFFER_DERIVED_REFERENCE"),
            ("historical_csv.all_in_rpm", "OFFER_DERIVED_REFERENCE"),
        ):
            self.assertEqual(self.predict({**self.quote, "source_id": source})["reason"], reason)
        with self.assertRaises(ValueError):
            MarketRPMBaseline(("historical_csv.market_rpm",), 3600)
        result = MarketRPMBaseline().predict(
            self.quote, decision_at="2026-01-01T11:00:00Z", shipment_id="S-1", lane_id="ATL-DFW"
        )
        self.assertEqual(result["reason"], "NO_APPROVED_MARKET_SOURCE")

    def test_context_units_and_quote_id_must_match(self):
        for field, value, reason in (
            ("shipment_id", "S-2", "SHIPMENT_MISMATCH"),
            ("lane_id", "DFW-ATL", "LANE_MISMATCH"),
            ("currency", "CAD", "CURRENCY_MISMATCH"),
            ("rate_basis", "total", "RATE_BASIS_MISMATCH"),
            ("quote_id", "", "MISSING_QUOTE_ID"),
        ):
            with self.subTest(field=field):
                self.assertEqual(self.predict({**self.quote, field: value})["reason"], reason)

    def test_bids_and_outcomes_cannot_change_market_reference(self):
        changed = {**self.quote, "bid_rate": "99999", "won": True, "margin_estimate": "90000"}
        self.assertEqual(self.predict(changed), self.predict())

    def test_active_source_requires_valid_age_policy(self):
        for value in (None, 0, -1, True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MarketRPMBaseline(("test-independent-feed",), value)

    def test_missing_quote_returns_unavailable_without_a_synthetic_price(self):
        result = self.baseline.predict(
            None, decision_at="2026-01-01T11:00:00Z", shipment_id="S-1", lane_id="ATL-DFW"
        )
        self.assertEqual(result["reason"], "MISSING_MARKET_QUOTE")
        self.assertIsNone(result["predicted_bid_rpm"])


if __name__ == "__main__":
    unittest.main()
