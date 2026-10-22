"""Check loaded-mile dollar conversion, original-bid alignment, and held-out isolation."""

import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from loadpilot_model.baselines.global_median import fit_from_training as fit_global
from loadpilot_model.baselines.lane_median import fit_from_training as fit_lane
from loadpilot_model.data.preparation import load_open_bid_rows
from loadpilot_model.data.splitting import build_partitions
from loadpilot_model.data.validation import SCHEMA
from loadpilot_model.evaluation.bid_comparison import (
    build_comparisons,
    compare_prediction,
    load_validation_records,
    rpm_to_bid_dollars,
)
from loadpilot_model.features.schema import FEATURE_COLUMNS, METADATA_COLUMNS, TARGET_COLUMN


class PriceConversionTests(unittest.TestCase):
    def test_total_dollars_signed_error_and_absolute_error(self):
        self.assertEqual(rpm_to_bid_dollars("2.5", "100"), Decimal("250"))
        row = compare_prediction("2.5", "100", "300")
        self.assertEqual(Decimal(row["signed_error_dollars"]), Decimal("-50"))
        self.assertEqual(Decimal(row["absolute_error_dollars"]), Decimal("50"))
        self.assertEqual(
            Decimal(compare_prediction("3.5", "100", "300")["signed_error_dollars"]), 50
        )

    def test_cent_rounding_does_not_replace_unrounded_comparison(self):
        row = compare_prediction("2.345", "1", "2.34")
        self.assertEqual(row["predicted_bid_dollars"], "2.345")
        self.assertEqual(row["predicted_bid_dollars_rounded"], "2.35")
        self.assertEqual(Decimal(row["signed_error_dollars"]), Decimal("0.005"))
        self.assertEqual(Decimal(row["rounded_signed_error_dollars"]), Decimal("0.01"))

    def test_invalid_rpm_miles_and_actual_bid_are_rejected(self):
        for value in (None, "bad", "", 0, -1, True, "NaN", "Infinity"):
            for field in ("rpm", "miles", "bid"):
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    compare_prediction(
                        value if field == "rpm" else 3,
                        value if field == "miles" else 100,
                        value if field == "bid" else 300,
                    )


class ValidationComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "historical.csv"
        self.dataset = self.root / "dataset"
        self.baselines = self.root / "baselines"
        self.baselines.mkdir()
        rows = []
        for i in range(20):
            row = dict.fromkeys(SCHEMA, "")
            row.update(
                shipment_id=f"S-{i}",
                lane_id="ATL-DFW",
                origin_city="Atlanta",
                origin_state="GA",
                destination_city="Dallas",
                destination_state="TX",
                origin_latitude="33",
                origin_longitude="-84",
                destination_latitude="33",
                destination_longitude="-97",
                distance_miles=str(100 + i),
                weight_lbs="10000",
                order_type="open_bid",
                offered_rate="9999",
                bid_rate=str(300 + i),
                won="false",
                cost_per_mile="2",
                estimated_cost="200",
                shipped_at=f"2026-01-{i + 1:02} 12:00:00",
                market_rpm="99",
                season="winter",
            )
            rows.append(row)
        with self.source.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(SCHEMA))
            writer.writeheader()
            writer.writerows(rows)
        selected, selection = load_open_bid_rows(self.source, date(2026, 10, 15))
        splits, _, report = build_partitions(selected)
        report["source_sha256"] = selection["sha256"]
        self.dataset.mkdir()
        (self.dataset / "split-report.json").write_text(json.dumps(report))
        for name in ("train", "validation"):  # No test directory exists.
            (self.dataset / name).mkdir()
            for table, fields in (
                ("features", FEATURE_COLUMNS),
                ("targets", (TARGET_COLUMN,)),
                ("metadata", METADATA_COLUMNS),
            ):
                with (self.dataset / name / f"{table}.csv").open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(splits[name][table])
        (self.baselines / "global-median-v1.json").write_text(json.dumps(fit_global(self.dataset)))
        (self.baselines / "lane-median-v1.json").write_text(json.dumps(fit_lane(self.dataset)))
        (self.baselines / "market-rpm-v1.json").write_text(
            json.dumps({"source_sha256": selection["sha256"]})
        )

    def test_matches_original_bids_and_exposes_unavailable_market_without_test_files(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.baselines.iterdir()}
        rows, report = build_comparisons(self.dataset, self.source, self.baselines)
        self.assertEqual(report["row_count"], 3)
        self.assertEqual(len(rows), 9)
        self.assertEqual(report["available_counts"], {"global_median": 3, "lane_median": 3})
        self.assertEqual(report["unavailable_counts"], {"market_rpm": 3})
        for row in rows:
            self.assertNotEqual(row["historical_bid_dollars"], "9999")
            if row["status"] == "available":
                self.assertEqual(
                    Decimal(row["predicted_bid_dollars"]),
                    Decimal(row["predicted_bid_rpm"]) * Decimal(row["loaded_miles"]),
                )
            else:
                self.assertEqual(row["predicted_bid_dollars"], "")
                self.assertEqual(row["absolute_error_dollars"], "")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_mismatched_original_source_and_misaligned_targets_fail(self):
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "source"):
            load_validation_records(self.dataset, self.source)
        self.source.write_bytes(original)
        targets = self.dataset / "validation/targets.csv"
        targets.write_text("target_bid_rpm\n999\n999\n999\n")
        with self.assertRaisesRegex(ValueError, "actual submitted bid"):
            load_validation_records(self.dataset, self.source)

    def test_baseline_from_different_training_split_is_rejected(self):
        path = self.baselines / "lane-median-v1.json"
        artifact = json.loads(path.read_text())
        artifact["dataset_manifest_sha256"] = "wrong"
        path.write_text(json.dumps(artifact))
        with self.assertRaisesRegex(ValueError, "training partition"):
            build_comparisons(self.dataset, self.source, self.baselines)


if __name__ == "__main__":
    unittest.main()
