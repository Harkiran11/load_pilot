"""Selection tests with mixed order types, invalid records, and optional blanks."""

import csv
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from loadpilot_model.data.preparation import load_open_bid_rows
from loadpilot_model.data.validation import OPTIONAL, SCHEMA
from loadpilot_model.features.schema import FEATURE_COLUMNS, build_model_inputs


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.csv"
        self.row = dict.fromkeys(SCHEMA, "")
        self.row.update(
            shipment_id="OPEN-1",
            lane_id="ATL-DFW",
            origin_city="Atlanta",
            origin_state="GA",
            destination_city="Dallas",
            destination_state="TX",
            origin_latitude="33",
            origin_longitude="-84",
            destination_latitude="33",
            destination_longitude="-97",
            distance_miles="100",
            weight_lbs="10000",
            order_type="open_bid",
            offered_rate="300",
            bid_rate="320",
            won="false",
            cost_per_mile="2",
            estimated_cost="200",
            shipped_at="2026-01-01 12:00:00",
            market_rpm="3",
            season="winter",
        )

    def select(self, rows, columns=None):
        with self.path.open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=columns or list(SCHEMA), extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
        return load_open_bid_rows(self.path, date(2026, 10, 15))

    def test_mixed_orders_keep_won_and_lost_open_bids(self):
        rows, report = self.select(
            [
                self.row,
                {**self.row, "shipment_id": "OPEN-2", "won": "true"},
                {**self.row, "shipment_id": "FIXED", "order_type": "fixed_rate", "bid_rate": ""},
            ]
        )
        self.assertEqual([r["shipment_id"] for r in rows], ["OPEN-1", "OPEN-2"])
        self.assertEqual(report["selected_award_counts"], {"false": 1, "true": 1})
        self.assertEqual(report["excluded_row_count"], 1)
        self.assertEqual(rows[0]["fuel_surcharge_rpm"], "")

    def test_all_invalid_rows_excluded_beyond_issue_sample_limit(self):
        bad = [{**self.row, "shipment_id": f"BAD-{n}", "bid_rate": "inf"} for n in range(15)]
        rows, report = self.select([self.row, *bad])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["exclusion_reason_counts"]["NONFINITE:bid_rate"], 15)

    def test_fully_empty_optional_columns_preserve_all_rows_and_never_become_features(self):
        records = [
            {**self.row, "shipment_id": f"OPEN-{i}", **dict.fromkeys(OPTIONAL, "")}
            for i in range(3)
        ]
        rows, report = self.select(records)
        features, targets, _ = build_model_inputs(rows)
        self.assertEqual(len(features), 3)
        self.assertEqual(len(targets), 3)
        self.assertEqual(report["excluded_row_count"], 0)
        self.assertEqual(set(report["missing_data"]["source_fully_missing_columns"]), OPTIONAL)
        self.assertEqual(
            report["missing_data"]["selected_optional_missing_counts"], dict.fromkeys(OPTIONAL, 3)
        )
        self.assertTrue(OPTIONAL.isdisjoint(FEATURE_COLUMNS))
        self.assertTrue(all(OPTIONAL.isdisjoint(row) for row in features))
        self.assertTrue(all(row[name] == "" for row in rows for name in OPTIONAL))

    def test_optional_null_markers_and_partial_missingness_do_not_drop_rows(self):
        records = [
            {**self.row, "shipment_id": f"OPEN-{i}", **dict.fromkeys(OPTIONAL, marker)}
            for i, marker in enumerate(("", "  ", "NaN", "NULL", "N/A", "none"))
        ]
        records.append(
            {
                **self.row,
                "shipment_id": "PRESENT",
                "margin_estimate": "-5",
                "all_in_rpm": "3",
                "fuel_surcharge_rpm": "0.5",
                "booked_lead_hours": "12",
            }
        )
        rows, report = self.select(records)
        self.assertEqual(len(build_model_inputs(rows)[0]), 7)
        self.assertEqual(report["excluded_row_count"], 0)
        self.assertEqual(
            report["missing_data"]["selected_optional_missing_counts"], dict.fromkeys(OPTIONAL, 6)
        )
        self.assertEqual(report["missing_data"]["source_fully_missing_columns"], [])

    def test_absent_optional_columns_do_not_change_model_inputs(self):
        baseline, _ = self.select([self.row])
        rows, report = self.select(
            [self.row], columns=[name for name in SCHEMA if name not in OPTIONAL]
        )
        self.assertEqual(build_model_inputs(rows), build_model_inputs(baseline))
        self.assertEqual(report["missing_data"]["optional_columns_absent"], sorted(OPTIONAL))

    def test_present_invalid_optional_values_remain_validation_errors(self):
        rows, report = self.select(
            [
                self.row,
                {**self.row, "shipment_id": "BAD", "fuel_surcharge_rpm": "-1"},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["excluded_row_count"], 1)
        self.assertIn("OUT_OF_RANGE:fuel_surcharge_rpm", report["exclusions"][0]["reasons"])

    def test_target_uses_submitted_bid_and_loaded_distance_without_rounding(self):
        rows, report = self.select([{**self.row, "bid_rate": "100", "distance_miles": "3"}])
        target = float(rows[0]["target_bid_rpm"])
        self.assertEqual(target, 100 / 3)
        self.assertNotEqual(target, float(rows[0]["offered_rate"]) / 3)
        self.assertEqual(report["target"]["count"], 1)
        self.assertEqual(report["target"]["min"], target)
        self.assertEqual(report["output_columns"][-1], "target_bid_rpm")

    def test_nonpositive_or_missing_bids_and_distances_never_get_targets(self):
        invalid = []
        for field in ("bid_rate", "distance_miles"):
            for value in ("0", "-1", "", "NaN", "inf", "bad"):
                invalid.append({**self.row, "shipment_id": f"{field}-{value}", field: value})
        rows, report = self.select([self.row, *invalid])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["excluded_row_count"], len(invalid))
        self.assertEqual(float(rows[0]["target_bid_rpm"]), 3.2)

    def test_extreme_ratio_is_excluded_if_not_finite_or_positive(self):
        rows, report = self.select(
            [
                {
                    **self.row,
                    "shipment_id": "OVERFLOW",
                    "bid_rate": "1e308",
                    "distance_miles": "1e-308",
                },
                {
                    **self.row,
                    "shipment_id": "UNDERFLOW",
                    "bid_rate": "1e-308",
                    "distance_miles": "1e308",
                },
            ]
        )
        self.assertEqual(rows, [])
        self.assertEqual(report["exclusion_reason_counts"]["INVALID_TARGET_BID_RPM"], 2)
        self.assertIsNone(report["target"]["min"])

    def test_existing_target_is_recomputed_from_source_amounts(self):
        rows, report = self.select(
            [{**self.row, "target_bid_rpm": "999"}], columns=[*SCHEMA, "target_bid_rpm"]
        )
        self.assertTrue(math.isfinite(float(rows[0]["target_bid_rpm"])))
        self.assertEqual(float(rows[0]["target_bid_rpm"]), 3.2)
        self.assertEqual(report["output_columns"].count("target_bid_rpm"), 1)

    def test_duplicate_ids_invalid_dates_and_nonpositive_targets(self):
        rows, report = self.select(
            [
                self.row,
                self.row,
                {**self.row, "shipment_id": "ZERO", "distance_miles": "0"},
                {**self.row, "shipment_id": "MISSING", "bid_rate": ""},
                {**self.row, "shipment_id": "DATE", "shipped_at": "bad"},
            ]
        )
        self.assertEqual(rows, [])
        self.assertEqual(report["status"], "no_eligible_rows")
        self.assertEqual(report["excluded_row_count"], 5)

    def test_unknown_type_excluded_and_known_categories_normalized(self):
        rows, report = self.select(
            [
                {**self.row, "order_type": " OPEN_BID ", "won": " FALSE "},
                {**self.row, "shipment_id": "UNKNOWN", "order_type": "other"},
            ]
        )
        self.assertEqual(rows[0]["order_type"], "open_bid")
        self.assertEqual(report["excluded_row_count"], 1)

    def test_missing_required_schema_stops_selection(self):
        with self.assertRaisesRegex(ValueError, "INVALID_SCHEMA"):
            self.select([self.row], columns=[c for c in SCHEMA if c != "bid_rate"])

    def test_malformed_rows_audited_and_source_preserved(self):
        self.select([self.row])
        with self.path.open("a") as stream:
            stream.write("invalid,short,row\n")
        before = self.path.read_bytes()
        rows, report = load_open_bid_rows(self.path, date(2026, 10, 15))
        self.assertEqual(len(rows), 1)
        self.assertEqual(report["excluded_row_count"], 1)
        self.assertIn("MALFORMED_ROW", report["exclusions"][0]["reasons"])
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
