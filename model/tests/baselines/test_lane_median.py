"""Lane thresholds, fallback behavior, directional lanes, and training isolation."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from loadpilot_model.baselines.lane_median import fit_from_training, load_lane_median
from loadpilot_model.features.schema import FEATURE_COLUMNS


class LaneMedianTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "train").mkdir()
        values = [1, 4, 2, 3, 5, 9]
        base = {
            "distance_miles": "100",
            "weight_lbs": "10000",
            "origin_latitude": "33",
            "origin_longitude": "-84",
            "destination_latitude": "33",
            "destination_longitude": "-97",
            "origin_state": "GA",
            "destination_state": "TX",
        }
        with (self.root / "train/features.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FEATURE_COLUMNS)
            writer.writeheader()
            writer.writerows(
                [{**base, "lane_id": lane} for lane in ("A-B", " a-b ", "B-A", "B-A", "B-A", "C-D")]
            )
        for name, header, rows in (
            ("targets", ["target_bid_rpm"], [[value] for value in values]),
            (
                "metadata",
                ["shipment_id", "shipped_at"],
                [[f"S-{i}", f"2026-01-{i + 1:02} 00:00:00"] for i in range(6)],
            ),
        ):
            with (self.root / f"train/{name}.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
        report = {
            "fit_partition": "train",
            "fit_row_count": 6,
            "target_column": "target_bid_rpm",
            "source_sha256": "a" * 64,
            "partitions": {
                "train": {
                    "row_count": 6,
                    "shipment_ids": [f"S-{i}" for i in range(6)],
                    "date_min": "2026-01-01 00:00:00",
                    "date_max": "2026-01-06 00:00:00",
                }
            },
        }
        (self.root / "split-report.json").write_text(json.dumps(report))

    def load(self, minimum=2):
        artifact = fit_from_training(self.root, minimum)
        path = self.root / "baseline.json"
        path.write_text(json.dumps(artifact))
        return load_lane_median(path)

    def test_exact_threshold_medians_and_direction_are_preserved(self):
        baseline = self.load()
        result = baseline.predict(" a-b ")
        self.assertEqual(result["predicted_bid_rpm"], 2.5)
        self.assertEqual(result["source"], "lane_median")
        self.assertEqual(result["lane_training_row_count"], 2)
        self.assertEqual(baseline.predict("B-A")["predicted_bid_rpm"], 3)

    def test_sparse_and_unseen_lanes_use_identical_global_training_median(self):
        baseline = self.load()
        for lane, reason in (("C-D", "SPARSE_LANE"), ("X-Y", "UNSEEN_LANE")):
            result = baseline.predict(lane)
            self.assertEqual(result["predicted_bid_rpm"], 3.5)
            self.assertEqual(result["source"], "global_median")
            self.assertEqual(result["reason"], reason)
        artifact = fit_from_training(self.root, 10)
        self.assertEqual(artifact["eligible_lane_count"], 0)
        self.assertEqual(artifact["training_rows_with_lane_reference"], 0)
        self.assertEqual(self.load(10).predict("A-B")["source"], "global_median")

    def test_heldout_files_are_not_required_or_used(self):
        original = fit_from_training(self.root, 2)
        for name in ("validation", "test"):
            (self.root / name).mkdir()
            (self.root / name / "features.csv").write_text("invalid held-out features")
            (self.root / name / "targets.csv").write_text("999999")
        self.assertEqual(fit_from_training(self.root, 2), original)

    def test_invalid_minimum_or_missing_lane_rejected(self):
        for minimum in (0, -1, True, 1.5):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                fit_from_training(self.root, minimum)
        baseline = self.load()
        for lane in (None, "", "NULL", "nan"):
            with self.subTest(lane=lane), self.assertRaises(ValueError):
                baseline.predict(lane)

    def test_misaligned_features_and_invalid_stored_counts_rejected(self):
        baseline_path = self.root / "baseline.json"
        self.load()
        artifact = json.loads(baseline_path.read_text())
        artifact["lanes"]["A-B"]["training_row_count"] = 99
        baseline_path.write_text(json.dumps(artifact))
        with self.assertRaisesRegex(ValueError, "sum"):
            load_lane_median(baseline_path)
        path = self.root / "train/features.csv"
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(ValueError, "align"):
            fit_from_training(self.root, 2)


if __name__ == "__main__":
    unittest.main()
