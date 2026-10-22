"""Full model/preprocessing reload parity using synthetic freight inputs only."""

import copy
import csv
import json
import pickle
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from loadpilot_model.features.preprocessing import transform_records
from loadpilot_model.inference.bundle import runtime_versions, save_inference_bundle
from loadpilot_model.inference.model_bundle import (
    MODEL_CODE,
    bundle_identity,
    load_model_bundle,
    sha256,
)
from loadpilot_model.models.regressor import RegressorConfig
from loadpilot_model.paths import PROJECT_ROOT
from loadpilot_model.training.setup import build_training_components
from tests.fixtures import make_saved_dataset


class ModelPredictionParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "synthetic-rpm-v1"
        self.path.mkdir()
        self.addCleanup(torch.set_num_threads, torch.get_num_threads())
        torch.set_num_threads(1)
        dataset = self.root / "synthetic-dataset"
        make_saved_dataset(dataset)
        report = json.loads((dataset / "split-report.json").read_text())
        original = (dataset / "preprocessor.pkl").read_bytes()
        processor = pickle.loads(original)
        with (dataset / "validation/features.csv").open() as stream:
            self.records = list(csv.DictReader(stream))
        self.encoded = transform_records(processor, self.records)
        components = build_training_components(RegressorConfig(self.encoded.shape[1]))
        # One synthetic optimizer step ensures this is a trained-state round trip.
        x = torch.from_numpy(self.encoded)
        loss = components.criterion(components.model(x), torch.tensor([3.4, 3.5, 3.6]))
        loss.backward()
        components.optimizer.step()
        components.model.eval()
        with torch.inference_mode():
            self.expected = components.model(x).numpy().copy()
        save_inference_bundle(
            processor, report, self.path / "preprocessing", preprocessor_bytes=original
        )
        checkpoint = {
            "model_config": components.model.config.to_dict(),
            "model_state_dict": components.model.state_dict(),
            "setup_config": components.config.to_dict(),
            "epoch": 1,
            "candidate_id": "synthetic-01",
            "validation": {"synthetic": True},
            "encoded_feature_order": report["transformed_feature_names"],
            "preprocessor_sha256": sha256(dataset / "preprocessor.pkl"),
            "source_sha256": report["source_sha256"],
            "split_manifest_sha256": sha256(dataset / "split-report.json"),
        }
        torch.save(checkpoint, self.path / "selected-checkpoint.pt")
        metadata = {
            "model_version": "synthetic-rpm-v1",
            "release_status": "unverified",
            "live_submission_enabled": False,
            "model_config": checkpoint["model_config"],
            "selected_epoch": 1,
            "selected_candidate": "synthetic-01",
            "encoded_feature_order": report["transformed_feature_names"],
            "raw_feature_order": report["feature_columns"],
            "preprocessor_sha256": checkpoint["preprocessor_sha256"],
            "source_sha256": checkpoint["source_sha256"],
            "split_manifest_sha256": checkpoint["split_manifest_sha256"],
            "checkpoint_sha256": sha256(self.path / "selected-checkpoint.pt"),
            "validation_metrics": checkpoint["validation"],
            "training_setup": checkpoint["setup_config"],
        }
        for name, value in (
            ("metadata.json", metadata),
            ("model-config.json", checkpoint["model_config"]),
        ):
            (self.path / name).write_text(json.dumps(value))
        # Minimal synthetic provenance attachments; no historical records or scores.
        for name in (
            "training/run-plan.json",
            "training/tuning-results.json",
            "evaluation/comparison.json",
            "evaluation/final-test-plan.json",
            "evaluation/test-results.json",
            "baselines/fallback-policy-v1.json",
        ):
            path = self.path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"synthetic_fixture": True}))
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copyfile(PROJECT_ROOT / name, self.path / name)
        self.manifest = {
            "schema_version": 1,
            "model_version": "synthetic-rpm-v1",
            "runtime_versions": {**runtime_versions(), "torch": str(torch.__version__)},
            "model_code_sha256": sha256(PROJECT_ROOT / MODEL_CODE),
            "files_sha256": {
                str(p.relative_to(self.path)): sha256(p)
                for p in self.path.rglob("*")
                if p.is_file()
            },
        }
        self.write_manifest()

    def write_manifest(self):
        self.manifest["bundle_id"] = bundle_identity(self.manifest)
        (self.path / "manifest.json").write_text(json.dumps(self.manifest))

    def refresh_file(self, name):
        self.manifest["files_sha256"][name] = sha256(self.path / name)
        self.write_manifest()

    def predict(self, loaded, records):
        with torch.inference_mode():
            return loaded.model(torch.from_numpy(loaded.preprocessing.transform(records))).numpy()

    def test_repeated_predictions_and_independent_loads_match_presave_weights_exactly(self):
        before = copy.deepcopy(self.records)
        hashes = {p: sha256(p) for p in self.path.rglob("*") if p.is_file()}
        loaded = load_model_bundle(self.path)
        for candidate in (loaded, loaded, load_model_bundle(self.path)):
            np.testing.assert_array_equal(
                candidate.preprocessing.transform(self.records), self.encoded
            )
            actual = self.predict(candidate, self.records)
            np.testing.assert_array_equal(actual, self.expected)
            miles = np.array([float(row["distance_miles"]) for row in self.records])
            np.testing.assert_array_equal(actual * miles, self.expected * miles)
            self.assertFalse(candidate.model.training)
            self.assertTrue(
                all(not p.requires_grad and p.grad is None for p in candidate.model.parameters())
            )
        self.assertEqual(self.records, before)
        self.assertEqual(hashes, {p: sha256(p) for p in hashes})

    def test_relocated_bundle_loads_in_fresh_python_process_without_dataset(self):
        relocated = self.root / "relocated"
        shutil.move(self.path, relocated)
        records = self.root / "inputs.json"
        records.write_text(json.dumps(self.records))
        output = self.root / "predictions.npy"
        # Make all source dataset files unavailable to the child process.
        (self.root / "synthetic-dataset").rename(self.root / "unused-source")
        script = """
import json, sys
from pathlib import Path
import numpy as np
import torch
from loadpilot_model.inference.model_bundle import load_model_bundle
torch.set_num_threads(1)
loaded = load_model_bundle(Path(sys.argv[1]))
records = json.loads(Path(sys.argv[2]).read_text())
with torch.inference_mode():
    values = loaded.model(torch.from_numpy(loaded.preprocessing.transform(records))).numpy()
np.save(sys.argv[3], values)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(relocated), str(records), str(output)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        np.testing.assert_array_equal(np.load(output, allow_pickle=False), self.expected)

    def test_loading_and_prediction_never_fit_and_ignore_outcomes_without_mutating_input(self):
        inputs = [
            {
                **row,
                "bid_rate": "999999",
                "won": True,
                "margin_estimate": -999,
                "shipped_at": "future",
                "fuel_surcharge_rpm": "",
            }
            for row in self.records
        ]
        before = copy.deepcopy(inputs)
        with (
            patch.object(ColumnTransformer, "fit_transform", side_effect=AssertionError("refit")),
            patch.object(StandardScaler, "fit", side_effect=AssertionError("refit")),
            patch.object(OneHotEncoder, "fit", side_effect=AssertionError("refit")),
        ):
            loaded = load_model_bundle(self.path)
            np.testing.assert_array_equal(self.predict(loaded, inputs), self.expected)
        self.assertEqual(inputs, before)

    def test_changed_weights_or_preprocessing_fail_before_deserialization(self):
        for name in ("selected-checkpoint.pt", "preprocessing/preprocessor.pkl"):
            path = self.path / name
            original = path.read_bytes()
            path.write_bytes(original + b"tampered")
            with patch("torch.load") as weights, patch("pickle.loads") as processor:
                with self.assertRaisesRegex(ValueError, "checksum"):
                    load_model_bundle(self.path)
                weights.assert_not_called()
                processor.assert_not_called()
            path.write_bytes(original)

    def test_rehashed_feature_order_mismatch_is_rejected(self):
        name = "metadata.json"
        metadata = json.loads((self.path / name).read_text())
        metadata["encoded_feature_order"] = list(reversed(metadata["encoded_feature_order"]))
        (self.path / name).write_text(json.dumps(metadata))
        self.refresh_file(name)
        with self.assertRaisesRegex(ValueError, "misaligned"):
            load_model_bundle(self.path)

    def test_rehashed_incompatible_runtime_or_model_code_is_rejected(self):
        original = copy.deepcopy(self.manifest)
        for field, value, message in (
            ("runtime_versions", {}, "versions"),
            ("model_code_sha256", "changed", "implementation"),
        ):
            self.manifest = {**original, field: value}
            self.write_manifest()
            with self.assertRaisesRegex(ValueError, message):
                load_model_bundle(self.path)

    def test_missing_required_artifact_is_rejected(self):
        del self.manifest["files_sha256"]["selected-checkpoint.pt"]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "missing required"):
            load_model_bundle(self.path)

    def test_same_torch_release_loads_across_platform_builds(self):
        release = str(torch.__version__).partition("+")[0]
        for saved, installed in ((release, release + "+cu130"),
                                 (release + "+cu130", release + "+cpu")):
            with self.subTest(saved=saved, installed=installed):
                self.manifest["runtime_versions"]["torch"] = saved
                self.write_manifest()
                with patch("torch.__version__", installed):
                    loaded = load_model_bundle(self.path)
                np.testing.assert_array_equal(self.predict(loaded, self.records), self.expected)

    def test_platform_build_compatibility_keeps_dependency_release_checks(self):
        original = copy.deepcopy(self.manifest["runtime_versions"])
        for dependency in ("torch", "numpy", "pandas", "scikit_learn", "python"):
            with self.subTest(dependency=dependency):
                self.manifest["runtime_versions"] = {**original, dependency: "0.0.0+cpu"}
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "versions"):
                    load_model_bundle(self.path)
