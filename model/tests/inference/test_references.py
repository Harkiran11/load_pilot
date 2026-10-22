"""Frozen baseline references and descriptive support warnings without fitting."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from loadpilot_model.baselines.global_median import GlobalMedianBaseline
from loadpilot_model.baselines.lane_median import LaneMedianBaseline
from loadpilot_model.inference.references import PricingReferences


class PricingReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "preprocessing").mkdir()
        (self.root / "evaluation").mkdir()
        self.metadata = {
            "source_sha256": "source",
            "split_manifest_sha256": "split",
            "release_status": "unverified",
        }
        baseline = LaneMedianBaseline(
            5,
            GlobalMedianBaseline(2.9, 12),
            {
                "ATL-DFW": {"training_row_count": 10, "median_bid_rpm": 2.6},
                "ATL-JAX": {"training_row_count": 2, "median_bid_rpm": None},
            },
        )
        self.selected = SimpleNamespace(
            baseline=baseline,
            policy={
                "source_sha256": "source",
                "dataset_manifest_sha256": "split",
                "selected_baseline": "lane_median",
                "baseline_id": "lane_median_bid_rpm",
                "policy_version": "baseline-fallback-v1",
            },
        )
        (self.root / "preprocessing/numeric-scaler.json").write_text(
            json.dumps(
                {
                    "feature_order": ["distance_miles", "weight_lbs"],
                    "mean": [100.0, 10000.0],
                    "scale": [10.0, 1000.0],
                }
            )
        )
        slices = [
            {
                "dimension": "lane",
                "slice": "ATL-DFW",
                "methods": {"pytorch": {"prediction_count": 3}},
            }
        ]
        for dimension, labels in (
            ("distance_band", ("[0.0, 150.0)", "[150.0, infinity)")),
            ("weight_band", ("[0.0, 12000.0)", "[12000.0, infinity)")),
        ):
            for i, label in enumerate(labels):
                regression = dimension == "distance_band" and i == 0
                slices.append(
                    {
                        "dimension": dimension,
                        "slice": label,
                        "methods": {
                            "lane_median": {
                                "prediction_count": 20,
                                "pytorch_improvement_mae_rpm_percent": -5 if regression else 20,
                                "pytorch_improvement_mae_dollars_percent": -7 if regression else 25,
                            }
                        },
                    }
                )
        (self.root / "evaluation/comparison.json").write_text(
            json.dumps(
                {
                    "frozen_gates": {"minimum_slice_samples": 10},
                    "band_policy": {"cuts": {"distance_band": [150.0], "weight_band": [12000.0]}},
                    "partitions": {"test": {"slices": slices}},
                }
            )
        )
        with patch(
            "loadpilot_model.inference.references.load_selected_fallback", return_value=self.selected
        ):
            self.references = PricingReferences(self.root, self.metadata)
        self.features = {"lane_id": "ATL-DFW", "distance_miles": "100", "weight_lbs": "10000"}

    def test_eligible_lane_uses_training_median_and_unrounded_dollars(self):
        before = copy.deepcopy(self.features)
        hashes = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.references.describe(self.features)
        reference = result["baseline_reference"]
        self.assertEqual(reference["reference_source"], "lane_median")
        self.assertEqual(reference["predicted_bid_rpm"], 2.6)
        self.assertEqual(reference["predicted_bid_dollars"], 260)
        self.assertEqual(reference["lane_training_row_count"], 10)
        self.assertEqual(self.features, before)
        self.assertEqual(hashes, {p: p.read_bytes() for p in hashes})

    def test_sparse_lane_uses_global_reference_with_explicit_warning(self):
        result = self.references.describe({**self.features, "lane_id": "ATL-JAX"})
        self.assertEqual(result["baseline_reference"]["reference_source"], "global_median")
        self.assertEqual(result["baseline_reference"]["predicted_bid_rpm"], 2.9)
        self.assertEqual(result["baseline_reference"]["reason"], "SPARSE_LANE")
        warnings = {w["code"]: w for w in result["warnings"]}
        self.assertIn("SPARSE_LANE_REFERENCE", warnings)
        self.assertEqual(warnings["LANE_SUPPORT_UNVERIFIED"]["details"]["test_sample_count"], 0)

    def test_uncalibrated_uncertainty_and_frozen_slice_regression_are_explicit(self):
        result = self.references.describe(self.features)
        self.assertEqual(
            result["uncertainty"],
            {
                "status": "not_calibrated",
                "confidence_level": None,
                "rpm_interval": None,
                "dollar_interval": None,
            },
        )
        warnings = {w["code"]: w for w in result["warnings"]}
        self.assertIn("MODEL_RELEASE_UNVERIFIED", warnings)
        self.assertEqual(warnings["LANE_SUPPORT_UNVERIFIED"]["details"]["minimum_test_samples"], 10)
        self.assertEqual(
            warnings["EVALUATED_SLICE_REGRESSION"]["details"]["rpm_improvement_percent"], -5
        )

    def test_numeric_warning_threshold_is_strict_and_not_a_confidence_score(self):
        at_threshold = self.references.describe({**self.features, "distance_miles": "130"})
        self.assertNotIn("NUMERIC_SUPPORT_HEURISTIC", {w["code"] for w in at_threshold["warnings"]})
        beyond = self.references.describe({**self.features, "distance_miles": "131"})
        warning = next(w for w in beyond["warnings"] if w["code"] == "NUMERIC_SUPPORT_HEURISTIC")
        self.assertEqual(warning["fields"], ["distance_miles"])
        self.assertEqual(warning["details"]["absolute_standardized_value"], 3.1)
        self.assertIsNone(beyond["uncertainty"]["confidence_level"])
        long = self.references.describe({**self.features, "distance_miles": "150"})
        self.assertNotIn("EVALUATED_SLICE_REGRESSION", {w["code"] for w in long["warnings"]})

    def test_reference_lineage_mismatch_rejects_initialization(self):
        with patch(
            "loadpilot_model.inference.references.load_selected_fallback", return_value=self.selected
        ):
            with self.assertRaisesRegex(ValueError, "lineage"):
                PricingReferences(self.root, {**self.metadata, "source_sha256": "other"})
