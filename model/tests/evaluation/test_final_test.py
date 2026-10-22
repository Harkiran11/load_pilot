"""One-shot scoring guards using synthetic test records only."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from loadpilot_model.data.dataset import FreightDataset
from loadpilot_model.evaluation.final_test import evaluate_final_test
from loadpilot_model.training.regression import run_tuning
from tests.fixtures import make_saved_dataset


class FinalTestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(torch.set_num_threads, torch.get_num_threads())
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.run = self.root / "run"
        make_saved_dataset(self.dataset)
        self.training = run_tuning(
            self.dataset, self.run, learning_rates=(0.003,), epochs=2, batch_size=5
        )
        plan = self.training["plan"]
        self.policy = self.root / "baseline.json"
        self.policy.write_text(
            json.dumps(
                {
                    "source_sha256": plan["source_sha256"],
                    "dataset_manifest_sha256": plan["split_manifest_sha256"],
                    "fit_partition": "train",
                    "selection_partition": "validation",
                    "selected_baseline": "lane_median",
                    "validation_metrics": [
                        {
                            "baseline": "lane_median",
                            "coverage": 1.0,
                            "mae_rpm": 1.0,
                            "mae_dollars": 100.0,
                        }
                    ],
                }
            )
        )

    def test_freezes_plan_before_test_access_and_scores_once_without_mutation(self):
        frozen = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for parent in (self.dataset, self.run)
            for path in parent.rglob("*")
            if path.is_file()
        }

        def guarded_dataset(*args, **kwargs):
            self.assertTrue((self.run / "final-test-plan.json").exists())
            self.assertEqual(args[1], "test")
            self.assertTrue(kwargs["allow_test"])
            return FreightDataset(*args, **kwargs)

        with patch("loadpilot_model.evaluation.final_test.FreightDataset", guarded_dataset):
            report = evaluate_final_test(self.dataset, self.run, self.policy)
        with (self.run / "test-predictions.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(report["metrics"]["row_count"], 3)
        self.assertEqual(report["metrics"]["coverage"], 1.0)
        for field, error in (("mae_rpm", "error_rpm"), ("mae_dollars", "error_dollars")):
            self.assertAlmostEqual(
                report["metrics"][field], np.mean([abs(float(row[error])) for row in rows])
            )
        self.assertEqual(
            frozen, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen}
        )
        with patch("loadpilot_model.evaluation.final_test.FreightDataset") as factory:
            with self.assertRaisesRegex(ValueError, "already"):
                evaluate_final_test(self.dataset, self.run, self.policy)
            factory.assert_not_called()

    def test_tampered_checkpoint_is_rejected_before_test_access(self):
        with (self.run / "selected-best.pt").open("ab") as stream:
            stream.write(b"changed")
        with patch("loadpilot_model.evaluation.final_test.FreightDataset") as factory:
            with self.assertRaisesRegex(ValueError, "checksum"):
                evaluate_final_test(self.dataset, self.run, self.policy)
            factory.assert_not_called()
        self.assertFalse((self.run / "final-test-plan.json").exists())

    def test_failed_test_load_retains_plan_and_blocks_silent_retry(self):
        (self.dataset / "test/arrays.npz").write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            evaluate_final_test(self.dataset, self.run, self.policy)
        self.assertTrue((self.run / "final-test-plan.json").exists())
        self.assertFalse((self.run / "test-results.json").exists())
        with self.assertRaisesRegex(ValueError, "already"):
            evaluate_final_test(self.dataset, self.run, self.policy)

    def test_lineage_mismatch_is_rejected_before_reserving_test(self):
        policy = json.loads(self.policy.read_text())
        policy["dataset_manifest_sha256"] = "wrong"
        self.policy.write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError, "provenance"):
            evaluate_final_test(self.dataset, self.run, self.policy)
        self.assertFalse((self.run / "final-test-plan.json").exists())
