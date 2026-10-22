"""Aggregate arithmetic, coverage, training bands, and validation-only metric checks."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from loadpilot_model.evaluation.bid_comparison import compare_prediction
from loadpilot_model.evaluation.validation_metrics import (
    assign_band,
    evaluate_validation,
    retrospective_season,
    summarize,
    training_band_cuts,
)
from loadpilot_model.features.schema import FEATURE_COLUMNS, METADATA_COLUMNS


class MetricArithmeticTests(unittest.TestCase):
    def test_unweighted_absolute_error_and_signed_bias(self):
        rows = [
            {"status": "available", "signed_error_rpm": "-1", "signed_error_dollars": "-100"},
            {"status": "available", "signed_error_rpm": "3", "signed_error_dollars": "600"},
            {"status": "unavailable", "signed_error_rpm": "", "signed_error_dollars": ""},
        ]
        metrics = summarize(rows)
        self.assertEqual(metrics["mae_rpm"], 2)
        self.assertEqual(metrics["mae_dollars"], 350)
        self.assertEqual(metrics["mean_signed_error_dollars"], 250)
        self.assertEqual(metrics["coverage"], 2 / 3)
        self.assertTrue(metrics["sparse"])

    def test_unavailable_and_empty_slices_are_not_zero_error(self):
        unavailable = summarize([{"status": "unavailable"}])
        self.assertIsNone(unavailable["mae_rpm"])
        self.assertIsNone(unavailable["mae_dollars"])
        self.assertEqual(unavailable["coverage"], 0)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIsNone(summarize([])["coverage"])
        self.assertEqual(summarize([])["status"], "no_eligible_records")

    def test_sample_threshold_is_inclusive(self):
        row = {"status": "available", "signed_error_rpm": "0", "signed_error_dollars": "0"}
        self.assertTrue(summarize([row] * 4)["sparse"])
        self.assertFalse(summarize([row] * 5)["sparse"])
        with self.assertRaises(ValueError):
            summarize([], 0)

    def test_quantile_bands_boundaries_and_duplicate_cuts(self):
        cuts = training_band_cuts(["1", "2", "3", "4"])
        self.assertEqual(cuts, [1.75, 2.5, 3.25])
        self.assertEqual(assign_band("1.75", cuts), "[1.75, 2.5)")
        self.assertEqual(assign_band("1000", cuts), "[3.25, infinity)")
        self.assertEqual(training_band_cuts(["2", "2", "2"]), [2])
        with self.assertRaises(ValueError):
            training_band_cuts([])

    def test_retrospective_seasons_cover_all_months(self):
        expected = [
            "winter",
            "winter",
            "spring",
            "spring",
            "spring",
            "summer",
            "summer",
            "summer",
            "fall",
            "fall",
            "fall",
            "winter",
        ]
        actual = [retrospective_season(f"2026-{month:02}-01 00:00:00") for month in range(1, 13)]
        self.assertEqual(actual, expected)


class ValidationMetricIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.csv"
        self.comparisons = self.root / "comparisons"
        self.comparisons.mkdir()
        rows = [
            {
                "shipment_id": f"S-{i}",
                "shipped_at": f"2026-01-{i + 1:02} 00:00:00",
                "order_type": "open_bid",
                "bid_rate": str(100 * (i + 1)),
                "distance_miles": "100",
                "weight_lbs": str(10000 + i),
                "origin_latitude": "33",
                "origin_longitude": "-84",
                "destination_latitude": "33",
                "destination_longitude": "-97",
                "origin_state": "GA",
                "destination_state": "TX",
                "lane_id": "ATL-DFW",
            }
            for i in range(4)
        ]
        self.write_csv(self.source, list(rows[0]), rows)
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for name, records in (("train", rows[:2]), ("validation", rows[2:])):
            folder = self.root / name
            folder.mkdir()
            self.write_csv(
                folder / "features.csv",
                FEATURE_COLUMNS,
                [{key: row[key] for key in FEATURE_COLUMNS} for row in records],
            )
            if name == "validation":
                self.write_csv(
                    folder / "metadata.csv",
                    METADATA_COLUMNS,
                    [{key: row[key] for key in METADATA_COLUMNS} for row in records],
                )
                self.write_csv(
                    folder / "targets.csv",
                    ["target_bid_rpm"],
                    [{"target_bid_rpm": str(int(row["bid_rate"]) / 100)} for row in records],
                )
        manifest = {
            "source_sha256": source_hash,
            "partitions": {
                "train": {"row_count": 2},
                "validation": {
                    "row_count": 2,
                    "shipment_ids": ["S-2", "S-3"],
                    "date_min": rows[2]["shipped_at"],
                    "date_max": rows[3]["shipped_at"],
                },
            },
        }
        manifest_bytes = json.dumps(manifest).encode()
        (self.root / "split-report.json").write_bytes(manifest_bytes)
        comparison_rows = []
        for record in rows[2:]:
            for baseline in ("global_median", "lane_median", "market_rpm"):
                row = {
                    **{key: record[key] for key in METADATA_COLUMNS},
                    "lane_id": "ATL-DFW",
                    "loaded_miles": "100",
                    "historical_bid_dollars": record["bid_rate"],
                    "historical_bid_rpm": str(int(record["bid_rate"]) // 100),
                    "baseline": baseline,
                    "status": "unavailable" if baseline == "market_rpm" else "available",
                    "predicted_bid_rpm": "",
                    "signed_error_rpm": "",
                    **dict.fromkeys(compare_prediction(3, 100, 300), ""),
                }
                if row["status"] == "available":
                    row.update(compare_prediction(3, 100, record["bid_rate"]))
                    row.update(
                        predicted_bid_rpm="3",
                        signed_error_rpm=str(3 - int(record["bid_rate"]) // 100),
                    )
                comparison_rows.append(row)
        self.write_csv(
            self.comparisons / "comparisons.csv", list(comparison_rows[0]), comparison_rows
        )
        report = {
            "partition": "validation",
            "row_count": 2,
            "source_sha256": source_hash,
            "dataset_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "validation_files_sha256": {
                f"validation/{name}.csv": hashlib.sha256(
                    (self.root / "validation" / f"{name}.csv").read_bytes()
                ).hexdigest()
                for name in ("features", "targets", "metadata")
            },
            "available_counts": {"global_median": 2, "lane_median": 2},
            "unavailable_counts": {"market_rpm": 2},
            "baseline_files_sha256": {},
        }
        (self.comparisons / "report.json").write_text(json.dumps(report))

    def write_csv(self, path, fields, rows):
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_validation_metrics_with_no_test_directory_and_train_only_band_cuts(self):
        report = evaluate_validation(self.root, self.source, self.comparisons)
        self.assertEqual(report["overall"][0]["mae_rpm"], 0.5)
        self.assertEqual(report["overall"][0]["mae_dollars"], 50)
        self.assertIsNone(report["overall"][2]["mae_dollars"])
        self.assertEqual(
            report["band_policy"]["cuts"]["weight_band"], [10000.25, 10000.5, 10000.75]
        )
        for dimension in ("lane", "distance_band", "season", "weight_band"):
            slices = [
                r
                for r in report["slices"]
                if r["dimension"] == dimension and r["baseline"] == "global_median"
            ]
            self.assertEqual(sum(r["prediction_count"] for r in slices), 2)

    def test_duplicated_or_inconsistent_comparison_errors_rejected(self):
        path = self.comparisons / "comparisons.csv"
        original = path.read_bytes()
        with path.open("a") as stream:
            stream.write(path.read_text().splitlines()[1] + "\n")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate_validation(self.root, self.source, self.comparisons)
        path.write_bytes(original)
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["signed_error_dollars"] = "999"
        self.write_csv(path, list(rows[0]), rows)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            evaluate_validation(self.root, self.source, self.comparisons)


if __name__ == "__main__":
    unittest.main()
