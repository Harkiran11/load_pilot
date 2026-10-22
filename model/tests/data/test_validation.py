"""Data integrity checks use isolated fixtures, never the private historical CSV."""

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from loadpilot_model.data.validation import SCHEMA, validate


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "shipments.csv"
        self.row = dict.fromkeys(SCHEMA, "")
        self.row.update(
            shipment_id="HS-TEST",
            lane_id="ATL-DFW",
            origin_city="Atlanta",
            origin_state="GA",
            origin_latitude="33",
            origin_longitude="-84",
            destination_city="Dallas",
            destination_state="TX",
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

    def run_validation(self, rows=None, columns=None, expected_rows=None):
        with self.path.open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=columns or list(SCHEMA), extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows if rows is not None else [self.row])
        return validate(self.path, date(2026, 10, 15), expected_rows)

    def codes(self, report):
        return {entry["code"] for entry in report["issues"]}

    def test_optional_missing_and_negative_margin_are_allowed(self):
        self.row["margin_estimate"] = "-50"
        report = self.run_validation()
        self.assertEqual(report["invalid_row_count"], 0)
        self.assertEqual(report["status"], "pass_with_warnings")

    def test_open_bid_requires_bid_but_fixed_rate_does_not(self):
        self.row["bid_rate"] = ""
        self.assertIn("MISSING_REQUIRED:bid_rate", self.codes(self.run_validation()))
        self.row["order_type"] = "fixed_rate"
        self.assertEqual(self.run_validation()["invalid_row_count"], 0)

    def test_invalid_numbers_coordinates_categories_and_dates(self):
        self.row.update(
            distance_miles="0",
            weight_lbs="bad",
            estimated_cost="inf",
            origin_latitude="91",
            shipped_at="bad",
            won="pending",
        )
        report = self.run_validation()
        self.assertEqual(report["invalid_row_count"], 1)
        self.assertTrue(
            {
                "OUT_OF_RANGE:distance_miles",
                "NONNUMERIC:weight_lbs",
                "NONFINITE:estimated_cost",
                "OUT_OF_RANGE:origin_latitude",
                "INVALID_DATE",
                "INVALID_CATEGORY:won",
            }
            <= self.codes(report)
        )

    def test_missing_schema_and_row_count_are_errors(self):
        report = self.run_validation(
            columns=[name for name in SCHEMA if name != "distance_miles"], expected_rows=2
        )
        self.assertEqual(report["status"], "fail")
        self.assertTrue({"INVALID_SCHEMA", "ROW_COUNT_MISMATCH"} <= self.codes(report))

    def test_duplicate_ids_and_records(self):
        report = self.run_validation(rows=[self.row, self.row])
        self.assertEqual(report["duplicate_id_count"], 1)
        self.assertEqual(report["duplicate_record_count"], 1)
        self.assertEqual(report["invalid_row_count"], 2)

    def test_future_and_unsorted_dates(self):
        later = {**self.row, "shipment_id": "HS-LATER", "shipped_at": "2027-01-01 12:00:00"}
        report = self.run_validation(rows=[later, self.row])
        self.assertTrue({"FUTURE_SHIPMENT_DATE", "UNSORTED_DATES"} <= self.codes(report))

    def test_empty_file_and_duplicate_headers(self):
        self.path.write_text("")
        self.assertIn("EMPTY_DATASET", self.codes(validate(self.path, date(2026, 10, 15))))
        self.path.write_text("shipment_id,shipment_id\nA,B\n")
        self.assertIn("INVALID_SCHEMA", self.codes(validate(self.path, date(2026, 10, 15))))

    def test_malformed_rows_are_counted_without_silent_dropping(self):
        self.run_validation()
        with self.path.open("a") as stream:
            stream.write("too,few,fields\n")
        report = validate(self.path, date(2026, 10, 15))
        self.assertEqual(report["row_count"], 2)
        self.assertEqual(report["parsed_row_count"], 1)
        self.assertEqual(report["invalid_row_count"], 1)
        self.assertIn("MALFORMED_ROW", self.codes(report))


if __name__ == "__main__":
    unittest.main()
