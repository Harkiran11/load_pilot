"""Chronological fitting, validation-only selection, and fixed-epoch artifact checks."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from torch.utils.data import DataLoader

from loadpilot_model.data.dataset import FreightDataset, create_dataloader
from loadpilot_model.evaluation.regression import evaluate_regressor
from loadpilot_model.models.regressor import FreightRPMRegressor, RegressorConfig
from loadpilot_model.training.regression import fit_candidate, run_tuning, select_candidate
from loadpilot_model.training.setup import TrainingSetupConfig, build_training_components
from tests.fixtures import make_saved_dataset


class RegressionTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "dataset"
        make_saved_dataset(self.root)
        self.addCleanup(torch.set_num_threads, torch.get_num_threads())
        torch.set_num_threads(1)
        self.train = FreightDataset(self.root, "train")
        self.validation = FreightDataset(self.root, "validation")

    def test_fixed_epoch_fit_updates_weights_and_repeats_with_same_seed(self):
        setup = TrainingSetupConfig()
        initial = build_training_components(RegressorConfig(len(self.train.feature_names)), setup)
        initial_weights = {key: value.clone() for key, value in initial.model.state_dict().items()}
        model, metrics, predicted = fit_candidate(
            self.train, self.validation, setup, epochs=3, batch_size=5
        )
        repeated, repeated_metrics, repeated_predicted = fit_candidate(
            self.train, self.validation, setup, epochs=3, batch_size=5
        )
        self.assertEqual(metrics, repeated_metrics)
        np.testing.assert_array_equal(predicted, repeated_predicted)
        self.assertEqual(metrics["epochs"], 3)
        self.assertTrue(
            any(
                not torch.equal(value, initial_weights[key])
                for key, value in model.state_dict().items()
            )
        )
        self.assertTrue(
            all(
                torch.equal(value, repeated.state_dict()[key])
                for key, value in model.state_dict().items()
            )
        )

    def test_evaluation_is_ordered_forward_only_and_metrics_match_predictions(self):
        components = build_training_components(RegressorConfig(len(self.train.feature_names)))
        before = {key: value.clone() for key, value in components.model.state_dict().items()}
        metrics, predicted = evaluate_regressor(
            components.model, create_dataloader(self.validation), components.criterion
        )
        error = predicted - self.validation.target_rpm_exact
        self.assertEqual(metrics["mae_rpm"], float(np.abs(error).mean()))
        self.assertEqual(
            metrics["mae_dollars"], float(np.abs(error * self.validation.loaded_miles).mean())
        )
        self.assertTrue(
            all(
                torch.equal(value, before[key])
                for key, value in components.model.state_dict().items()
            )
        )
        self.assertTrue(all(p.grad is None for p in components.model.parameters()))
        with self.assertRaisesRegex(ValueError, "row order"):
            evaluate_regressor(
                components.model, DataLoader(self.validation, shuffle=True), components.criterion
            )

    def test_selection_uses_only_validation_mae_with_deterministic_ties(self):
        results = [
            {
                "candidate_id": "A",
                "last_epoch_train_huber": 0.001,
                "validation": {"mae_rpm": 0.4, "mae_dollars": 200},
            },
            {
                "candidate_id": "B",
                "last_epoch_train_huber": 9.0,
                "validation": {"mae_rpm": 0.3, "mae_dollars": 300},
            },
            {
                "candidate_id": "C",
                "last_epoch_train_huber": 0.0,
                "validation": {"mae_rpm": 0.3, "mae_dollars": 250},
            },
        ]
        self.assertEqual(select_candidate(results)["candidate_id"], "C")
        with self.assertRaises(ValueError):
            select_candidate([])

    def test_mixed_lineage_test_partition_and_invalid_epochs_are_rejected(self):
        self.validation.preprocessor_sha256 = "different"
        with self.assertRaisesRegex(ValueError, "lineage"):
            fit_candidate(self.train, self.validation, TrainingSetupConfig(), epochs=1)
        self.validation.preprocessor_sha256 = self.train.preprocessor_sha256
        self.validation.partition = "test"
        with self.assertRaisesRegex(ValueError, "selection"):
            fit_candidate(self.train, self.validation, TrainingSetupConfig(), epochs=1)
        with self.assertRaisesRegex(ValueError, "reserved"):
            components = build_training_components(RegressorConfig(len(self.train.feature_names)))
            evaluate_regressor(
                components.model, create_dataloader(self.validation), components.criterion
            )
        with self.assertRaisesRegex(ValueError, "epochs"):
            fit_candidate(self.train, self.validation, TrainingSetupConfig(), epochs=0)

    def test_tuning_ignores_unreadable_test_and_persists_final_selected_model(self):
        (self.root / "test/arrays.npz").write_bytes(b"reserved and unreadable")
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        output = Path(self.temp.name) / "run"
        report = run_tuning(
            self.root, output, learning_rates=(0.003, 0.001), epochs=2, batch_size=5
        )
        self.assertEqual(report["selection_partition"], "validation")
        self.assertEqual(len(report["candidates"]), 2)
        weights = torch.load(output / "selected-best.pt", weights_only=True, map_location="cpu")
        model = FreightRPMRegressor(RegressorConfig.from_dict(weights["model_config"]))
        model.load_state_dict(weights["model_state_dict"])
        with torch.inference_mode():
            expected = model(self.validation.features).numpy()
        import csv

        with (output / "validation-predictions.csv").open(newline="") as stream:
            predictions = np.array(
                [float(row["predicted_bid_rpm"]) for row in csv.DictReader(stream)]
            )
        np.testing.assert_array_equal(predictions, expected.astype(np.float64))
        self.assertEqual(
            before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
        )
        self.assertEqual(
            json.loads((output / "tuning-results.json").read_text())["selected_candidate"],
            weights["candidate_id"],
        )
        with self.assertRaisesRegex(ValueError, "output exists"):
            run_tuning(self.root, output, epochs=2)

    def test_patience_stops_and_restores_best_checkpoint_instead_of_last_epoch(self):
        losses = iter([0.6, 0.4, 0.5, 0.7, 0.4])

        def controlled_validation(*args):
            metrics, predictions = evaluate_regressor(*args)
            metrics["huber_loss"] = next(losses)
            return metrics, predictions

        output = Path(self.temp.name) / "candidate"
        with patch("loadpilot_model.training.regression.evaluate_regressor", controlled_validation):
            model, result, _ = fit_candidate(
                self.train,
                self.validation,
                TrainingSetupConfig(),
                epochs=10,
                patience=2,
                min_delta=0,
                batch_size=5,
                output_dir=output,
            )
        saved = torch.load(output / "best.pt", weights_only=True)
        self.assertEqual(result["epochs"], 4)
        self.assertEqual(result["best_epoch"], 2)
        self.assertEqual(saved["epoch"], 2)
        self.assertEqual(result["stop_reason"], "patience_exhausted")
        self.assertTrue(result["history"][-1]["stop"])
        self.assertTrue(saved["optimizer_state_dict"]["state"])
        self.assertEqual(len((output / "history.csv").read_text().splitlines()), 5)
        for key, value in model.state_dict().items():
            self.assertTrue(torch.equal(value, saved["model_state_dict"][key]))


if __name__ == "__main__":
    unittest.main()
