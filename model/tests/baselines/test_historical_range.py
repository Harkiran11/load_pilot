"""History statistics are training-only, supported and pinned to model lineage."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loadpilot_model.baselines.historical_range import (
    HistoricalPricingRange,
    digest,
    fit_from_training,
    identity,
    statistics,
)
from loadpilot_model.baselines.lane_median import fit_from_training as fit_lane_median
from tests.fixtures import make_saved_dataset


class HistoricalRangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dataset = self.root / "dataset"
        make_saved_dataset(self.dataset)
        self.artifact = fit_from_training(self.dataset, minimum_samples=3)
        self.path = self.root / "history.json"
        self.path.write_text(json.dumps(self.artifact))
        self.bundle = self.root / "bundle"
        (self.bundle / "baselines").mkdir(parents=True)
        metadata = {
            "source_sha256": self.artifact["source_sha256"],
            "split_manifest_sha256": self.artifact["dataset_manifest_sha256"],
        }
        (self.bundle / "metadata.json").write_text(json.dumps(metadata))
        (self.bundle / "baselines/lane-median-v1.json").write_text(
            json.dumps(fit_lane_median(self.dataset))
        )
        manifest = {
            "schema_version": 1,
            "files_sha256": {
                name: digest((self.bundle / name).read_bytes())
                for name in ("metadata.json", "baselines/lane-median-v1.json")
            },
        }
        (self.bundle / "manifest.json").write_text(
            json.dumps({**manifest, "bundle_id": identity(manifest)})
        )

    def test_fit_uses_training_only_and_linear_quartiles(self):
        original = Path.read_bytes

        def guarded(path):
            self.assertNotIn("validation", path.parts)
            self.assertNotIn("test", path.parts)
            return original(path)

        with patch.object(Path, "read_bytes", guarded):
            fitted = fit_from_training(self.dataset, minimum_samples=3)
        self.assertEqual(fitted["fit_partition"], "train")
        self.assertEqual(fitted["training_row_count"], 14)
        self.assertEqual(fitted["global"], statistics([2 + i / 10 for i in range(14)]))
        self.assertAlmostEqual(fitted["global"]["lower_rpm"], 2.325)
        self.assertAlmostEqual(fitted["global"]["upper_rpm"], 2.975)

    def test_specific_lane_and_distance_group_and_loaded_mile_conversion(self):
        history = HistoricalPricingRange(self.path, self.bundle)
        result = history.describe(" atl-dfw ", 101)
        self.assertEqual(result["source"], "lane_distance_band")
        self.assertEqual(result["sample_count"], 4)
        self.assertAlmostEqual(result["lower_bid_dollars"], result["lower_rpm"] * 101)
        self.assertEqual(history.sha256, digest(self.path.read_bytes()))

    def test_sparse_lane_uses_distance_peers_and_global_as_last_resort(self):
        history = HistoricalPricingRange(self.path, self.bundle)
        self.assertEqual(history.describe("UNSEEN-LANE", 101)["source"], "distance_band")
        # Raise threshold for selection to exercise lane and global fallbacks.
        history.artifact["minimum_samples"] = 10
        self.assertEqual(history.describe("ATL-DFW", 101)["source"], "lane")
        result = history.describe("UNSEEN-LANE", 101)
        self.assertEqual(result["source"], "global")
        self.assertEqual(result["sample_count"], 14)

    def test_degenerate_history_never_invents_a_spread(self):
        self.assertEqual(
            statistics([3.0] * 20),
            {"sample_count": 20, "lower_rpm": 3.0, "median_rpm": 3.0, "upper_rpm": 3.0},
        )

    def test_corruption_and_changed_training_lineage_are_rejected(self):
        corrupt = json.loads(self.path.read_text())
        corrupt["global"]["lower_rpm"] = 2.0
        self.path.write_text(json.dumps(corrupt))
        with self.assertRaisesRegex(ValueError, "checksum"):
            HistoricalPricingRange(self.path, self.bundle)
        artifact = {**self.artifact, "source_sha256": "other-source"}
        artifact.pop("artifact_id")
        self.path.write_text(json.dumps({**artifact, "artifact_id": identity(artifact)}))
        with self.assertRaisesRegex(ValueError, "lineage"):
            HistoricalPricingRange(self.path, self.bundle)

    def test_unsupported_global_sample_count_is_rejected(self):
        with self.assertRaises(ValueError):
            fit_from_training(self.dataset, minimum_samples=20)

    def test_serving_does_not_read_datasets_or_fit_statistics(self):
        history = HistoricalPricingRange(self.path, self.bundle)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("request-time IO")):
            with patch(
                "loadpilot_model.baselines.historical_range.statistics",
                side_effect=AssertionError("request-time fit"),
            ):
                self.assertGreater(history.describe("ATL-DFW", 101)["lower_rpm"], 0)
