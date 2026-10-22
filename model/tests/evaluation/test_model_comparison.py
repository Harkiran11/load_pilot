"""Frozen-prediction comparisons, gate arithmetic, and provenance rejection."""

import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from loadpilot_model.baselines.global_median import fit_from_training as fit_global
from loadpilot_model.baselines.lane_median import fit_from_training as fit_lane
from loadpilot_model.evaluation.model_comparison import (
    build_comparison,
    compare_group,
    improvement,
    save_comparison,
    sha256,
    within_relative_gate,
)
from loadpilot_model.evaluation.validation_metrics import training_band_cuts
from tests.fixtures import make_saved_dataset


class ComparisonArithmeticTests(unittest.TestCase):
    def test_improvement_regression_and_zero_baseline(self):
        self.assertEqual(improvement(8, 10), 20)
        self.assertEqual(improvement(12, 10), -20)
        self.assertIsNone(improvement(0, 0))
        self.assertTrue(within_relative_gate(0, 0, 5))
        self.assertFalse(within_relative_gate(1, 0, 5))
        self.assertTrue(within_relative_gate(9.5, 10, 5))
        self.assertTrue(within_relative_gate(11, 10, -10))
        self.assertFalse(within_relative_gate(11.01, 10, -10))

    def test_unavailable_market_and_sparse_counts_are_not_fabricated(self):
        rows = [
            {
                "method": "pytorch",
                "status": "available",
                "signed_error_rpm": "1",
                "signed_error_dollars": "100",
            },
            {
                "method": "lane_median",
                "status": "available",
                "signed_error_rpm": "2",
                "signed_error_dollars": "200",
            },
            {"method": "market_rpm", "status": "unavailable"},
        ]
        results = compare_group(rows, 10)
        self.assertTrue(results["pytorch"]["sparse"])
        self.assertEqual(results["lane_median"]["pytorch_improvement_mae_rpm_percent"], 50)
        self.assertEqual(results["market_rpm"]["coverage"], 0)
        self.assertIsNone(results["market_rpm"]["mae_rpm"])


class FrozenComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.run = self.root / "run"
        self.baselines = self.root / "baselines"
        self.run.mkdir()
        self.baselines.mkdir()
        make_saved_dataset(self.dataset)

        def read(path):
            with path.open() as stream:
                return list(csv.DictReader(stream))

        source_rows = []
        metrics = {}
        for partition in ("train", "validation", "test"):
            features = read(self.dataset / partition / "features.csv")
            targets = read(self.dataset / partition / "targets.csv")
            metadata = read(self.dataset / partition / "metadata.csv")
            predictions = []
            for feature, target, meta in zip(features, targets, metadata):
                rpm = Decimal(target["target_bid_rpm"])
                miles = Decimal(feature["distance_miles"])
                source_rows.append(
                    {**feature, **meta, "order_type": "open_bid", "bid_rate": str(rpm * miles)}
                )
                predictions.append(
                    {
                        **meta,
                        "loaded_miles": str(miles),
                        "target_bid_rpm": str(rpm),
                        "predicted_bid_rpm": str(rpm + Decimal("0.1")),
                    }
                )
            if partition != "train":
                with (self.run / f"{partition}-predictions.csv").open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(predictions[0]))
                    writer.writeheader()
                    writer.writerows(predictions)
                metrics[partition] = {
                    "mae_rpm": 0.1,
                    "mae_dollars": sum(float(x["loaded_miles"]) * 0.1 for x in predictions) / 3,
                }
        self.source = self.root / "historical.csv"
        with self.source.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(source_rows[0]))
            writer.writeheader()
            writer.writerows(source_rows)
        manifest_path = self.dataset / "split-report.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["source_sha256"] = sha256(self.source)
        manifest_path.write_text(json.dumps(manifest))
        for name, artifact in (
            ("global-median-v1.json", fit_global(self.dataset)),
            ("lane-median-v1.json", fit_lane(self.dataset)),
            (
                "market-rpm-v1.json",
                {
                    "source_sha256": sha256(self.source),
                    "current_dataset_status": "unavailable",
                    "reason": "no quote",
                },
            ),
        ):
            (self.baselines / name).write_text(json.dumps(artifact))
        self.validation_metrics = self.root / "metrics.json"
        train = read(self.dataset / "train/features.csv")
        self.validation_metrics.write_text(
            json.dumps(
                {
                    "baseline_files_sha256": {p.name: sha256(p) for p in self.baselines.iterdir()},
                    "band_policy": {
                        "fit_partition": "train",
                        "training_feature_sha256": sha256(self.dataset / "train/features.csv"),
                        "cuts": {
                            dimension: training_band_cuts([r[field] for r in train])
                            for dimension, field in (
                                ("distance_band", "distance_miles"),
                                ("weight_band", "weight_lbs"),
                            )
                        },
                    },
                }
            )
        )
        policy_path = self.baselines / "fallback-policy-v1.json"
        policy_path.write_text(
            json.dumps(
                {
                    "metrics_sha256": sha256(self.validation_metrics),
                    "baseline_artifact": "lane-median-v1.json",
                    "baseline_sha256": sha256(self.baselines / "lane-median-v1.json"),
                    "selected_baseline": "lane_median",
                }
            )
        )
        # Invalid tensor bytes deliberately prove model weights are never loaded.
        (self.run / "selected-best.pt").write_bytes(b"frozen opaque checkpoint")
        tuning_path = self.run / "tuning-results.json"
        tuning_path.write_text(
            json.dumps(
                {
                    "selected_weights_sha256": sha256(self.run / "selected-best.pt"),
                    "selected_candidate": "candidate-01",
                    "candidates": [
                        {"candidate_id": "candidate-01", "validation": metrics["validation"]}
                    ],
                }
            )
        )
        plan_path = self.run / "final-test-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "tuning_results_sha256": sha256(tuning_path),
                    "checkpoint_sha256": sha256(self.run / "selected-best.pt"),
                    "baseline_policy_sha256": sha256(policy_path),
                    "split_manifest_sha256": sha256(manifest_path),
                    "preprocessor_sha256": sha256(self.dataset / "preprocessor.pkl"),
                    "source_sha256": sha256(self.source),
                    "selected_candidate": "candidate-01",
                    "gates": {
                        "minimum_slice_samples": 10,
                        "maximum_slice_regression_percent": 10,
                        "minimum_coverage": 1,
                        "maximum_mae_rpm": 1,
                        "maximum_mae_dollars": 100,
                        "minimum_improvement_vs_test_baseline_percent": 5,
                    },
                }
            )
        )
        (self.run / "test-results.json").write_text(
            json.dumps(
                {
                    "plan_sha256": sha256(plan_path),
                    "checkpoint_sha256": sha256(self.run / "selected-best.pt"),
                    "predictions_sha256": sha256(self.run / "test-predictions.csv"),
                    "metrics": metrics["test"],
                }
            )
        )

    def compare(self):
        return build_comparison(
            self.dataset, self.source, self.run, self.baselines, self.validation_metrics
        )

    def test_comparison_reuses_predictions_and_preserves_frozen_inputs(self):
        before = {p: sha256(p) for p in self.root.rglob("*") if p.is_file()}
        report, rows = self.compare()
        self.assertEqual(len(rows), 24)
        self.assertAlmostEqual(report["partitions"]["test"]["overall"]["pytorch"]["mae_rpm"], 0.1)
        self.assertEqual(report["lane_reference_usage"]["test"], {"SUFFICIENT_TRAINING_SAMPLES": 3})
        self.assertEqual(report["pricing_gate_status"], "unverified")
        self.assertEqual(before, {p: sha256(p) for p in before})
        output = self.root / "comparison"
        save_comparison(report, rows, output)
        self.assertTrue((output / "slices.csv").exists())
        with self.assertRaises(FileExistsError):
            save_comparison(report, rows, output)

    def test_changed_prediction_and_changed_baseline_are_rejected(self):
        path = self.run / "test-predictions.csv"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.compare()
        path.write_bytes(original)
        path = self.baselines / "lane-median-v1.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Baseline differs"):
            self.compare()
