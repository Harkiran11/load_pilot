"""Verify price/outcome leakage cannot change the feature projection."""

import unittest

from loadpilot_model.features.schema import (
    FEATURE_COLUMNS,
    FEATURE_PROVENANCE,
    TARGET_COLUMN,
    build_features,
    build_model_inputs,
)


class ModelInputTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "distance_miles": "100",
            "weight_lbs": "10000",
            "origin_latitude": "33",
            "origin_longitude": "-84",
            "destination_latitude": "33",
            "destination_longitude": "-97",
            "lane_id": "ATL-DFW",
            "origin_state": "GA",
            "destination_state": "TX",
            TARGET_COLUMN: "3.2",
            "shipment_id": "HS-1",
            "shipped_at": "2026-01-01 12:00:00",
        }

    def test_outcomes_prices_future_fields_and_unknown_columns_cannot_enter_features(self):
        expected = build_features(self.record)
        changed = {
            **self.record,
            "bid_rate": "99999",
            "won": "true",
            "margin_estimate": "9000",
            "shipment_id": "HS-CHANGED",
            "shipped_at": "2027-01-01",
            "season": "summer",
            "actual_profit": "1000",
            "future_cost": "0",
            "realized_rpm": "900",
            "offered_rate": "999",
            "market_rpm": "100",
            "all_in_rpm": "100",
            "target_bid_rpm": "999",
            "estimated_cost": "1",
        }
        self.assertEqual(build_features(changed), expected)
        self.assertEqual(tuple(expected), FEATURE_COLUMNS)

    def test_inference_projection_requires_no_target_bid_or_outcome(self):
        record = {name: self.record[name] for name in FEATURE_COLUMNS}
        self.assertEqual(build_features(record), build_features(self.record))

    def test_live_posted_attributes_map_to_identical_historical_features(self):
        live = {name: self.record[name] for name in FEATURE_COLUMNS if name != "lane_id"}
        live.update(origin_terminal_code=" atl ", destination_terminal_code="dfw")
        live["weight_lbs"] = 10000
        live["distance_miles"] = 100
        self.assertEqual(build_features(live), build_features(self.record))
        self.assertEqual(set(FEATURE_PROVENANCE), set(FEATURE_COLUMNS))

    def test_lane_is_not_inferred_from_unknown_terminals_or_future_assignment(self):
        live = {name: self.record[name] for name in FEATURE_COLUMNS if name != "lane_id"}
        live.update(
            origin_terminal_code=None,
            assigned_terminal_code="ATL",
            destination_terminal_code="DFW",
            winning_bid_rate="300",
        )
        with self.assertRaisesRegex(ValueError, "lane_id"):
            build_features(live)

    def test_unsupported_market_season_timing_cost_fields_do_not_change_inputs(self):
        expected = build_features(self.record)
        live = {
            **self.record,
            "pickup_at": "2030-01-01",
            "delivery_at": "2030-01-02",
            "created_at": "2030-01-01",
            "booked_lead_hours": "12",
            "season": "summer",
            "market_rpm": "100",
            "fuel_surcharge_rpm": "2",
            "cost_per_mile": "9",
            "estimated_cost": "900",
            "estimated_deadhead_cost": "50",
        }
        self.assertEqual(build_features(live), expected)

    def test_target_and_metadata_remain_separate_and_aligned(self):
        second = {**self.record, "distance_miles": "200", TARGET_COLUMN: "4", "shipment_id": "HS-2"}
        features, targets, metadata = build_model_inputs([self.record, second])
        self.assertEqual([row["distance_miles"] for row in features], ["100", "200"])
        self.assertEqual(targets, [{TARGET_COLUMN: "3.2"}, {TARGET_COLUMN: "4"}])
        self.assertEqual([row["shipment_id"] for row in metadata], ["HS-1", "HS-2"])
        self.assertTrue(all(set(row) == set(FEATURE_COLUMNS) for row in features))

    def test_invalid_or_missing_required_features_raise(self):
        for name, value in (
            ("lane_id", ""),
            ("distance_miles", "0"),
            ("weight_lbs", "NaN"),
            ("origin_latitude", "91"),
            ("destination_longitude", "bad"),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_features({**self.record, name: value})

    def test_invalid_target_is_rejected_instead_of_used_as_a_feature(self):
        for value in ("0", "-1", "inf", "NaN", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_model_inputs([{**self.record, TARGET_COLUMN: value}])


if __name__ == "__main__":
    unittest.main()
