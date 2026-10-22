"""Training isolation and global median definition checks."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from loadpilot_model.baselines.global_median import fit_from_training, load_global_median


class GlobalMedianTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "train").mkdir()

    def dataset(self, values):
        with (self.root / "train/targets.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["target_bid_rpm"])
            writer.writerows([[value] for value in values])
        with (self.root / "train/metadata.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["shipment_id", "shipped_at"])
            writer.writerows(
                [[f"S-{i}", f"2026-01-{i + 1:02} 00:00:00"] for i in range(len(values))]
            )
        report = {
            "fit_partition": "train",
            "fit_row_count": len(values),
            "target_column": "target_bid_rpm",
            "source_sha256": "a" * 64,
            "partitions": {
                "train": {
                    "row_count": len(values),
                    "shipment_ids": [f"S-{i}" for i in range(len(values))],
                    "date_min": "2026-01-01 00:00:00",
                    "date_max": f"2026-01-{len(values):02} 00:00:00",
                }
            },
        }
        (self.root / "split-report.json").write_text(json.dumps(report))

    def test_odd_and_even_medians_and_saved_prediction(self):
        for values, expected in (([4, 1, 2], 2), ([4, 1, 2, 3], 2.5)):
            with self.subTest(values=values):
                self.dataset(values)
                artifact = fit_from_training(self.root)
                self.assertEqual(artifact["median_bid_rpm"], expected)
                path = self.root / "baseline.json"
                path.write_text(json.dumps(artifact))
                self.assertEqual(load_global_median(path).predict_rpm(), expected)

    def test_missing_or_modified_heldout_files_do_not_affect_result(self):
        self.dataset([2, 3, 4])
        original = fit_from_training(self.root)  # Held-out directories do not even exist.
        for name in ("validation", "test"):
            (self.root / name).mkdir()
            (self.root / name / "targets.csv").write_text("invalid held-out content")
        self.assertEqual(fit_from_training(self.root), original)

    def test_empty_invalid_or_nonpositive_labels_are_rejected(self):
        for values in ([], [0], [-1], ["NaN"], ["inf"], ["bad"]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.dataset(values)
                fit_from_training(self.root)

    def test_misaligned_metadata_or_manifest_counts_are_rejected(self):
        self.dataset([1, 2, 3])
        path = self.root / "split-report.json"
        report = json.loads(path.read_text())
        report["partitions"]["train"]["shipment_ids"].reverse()
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "IDs"):
            fit_from_training(self.root)
        self.dataset([1, 2, 3])
        report = json.loads(path.read_text())
        report["fit_row_count"] = 4
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "row counts"):
            fit_from_training(self.root)


if __name__ == "__main__":
    unittest.main()
