"""Independent baseline integrity checks and fail-closed fallback settings."""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loadpilot_model.baselines.fallback import select_fallback
from loadpilot_model.baselines.global_median import fit_from_training as fit_global
from loadpilot_model.baselines.lane_median import fit_from_training as fit_lane
from loadpilot_model.inference.bundle import contract_code_hashes
from loadpilot_model.inference.fallback import BaselineFallback, FallbackSettings
from loadpilot_model.inference.model_bundle import bundle_identity, sha256
from loadpilot_model.paths import PROJECT_ROOT
from tests.fixtures import make_saved_dataset


class ServingFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        make_saved_dataset(self.dataset)
        self.bundle = self.root / "bundle"
        baseline_dir = self.bundle / "baselines"
        baseline_dir.mkdir(parents=True)
        report = json.loads((self.dataset / "split-report.json").read_text())
        for name, artifact in (
            ("global-median-v1.json", fit_global(self.dataset)),
            ("lane-median-v1.json", fit_lane(self.dataset)),
        ):
            (baseline_dir / name).write_text(json.dumps(artifact))
        metrics_path = self.root / "metrics.json"
        metrics_path.write_text(
            json.dumps(
                {
                    "partition": "validation",
                    "row_count": 3,
                    "source_sha256": report["source_sha256"],
                    "dataset_manifest_sha256": sha256(self.dataset / "split-report.json"),
                    "baseline_files_sha256": {p.name: sha256(p) for p in baseline_dir.iterdir()},
                    "overall": [
                        {
                            "baseline": name,
                            "status": "scored",
                            "eligible_count": 3,
                            "prediction_count": 3,
                            "unavailable_count": 0,
                            "coverage": 1.0,
                            "mae_rpm": rpm,
                            "mae_dollars": dollars,
                        }
                        for name, rpm, dollars in (
                            ("global_median", 1.0, 100),
                            ("lane_median", 0.8, 80),
                        )
                    ],
                }
            )
        )
        (baseline_dir / "fallback-policy-v1.json").write_text(
            json.dumps(select_fallback(metrics_path, baseline_dir))
        )
        (self.bundle / "metadata.json").write_text(
            json.dumps(
                {
                    "model_version": "synthetic-v1",
                    "source_sha256": report["source_sha256"],
                    "split_manifest_sha256": sha256(self.dataset / "split-report.json"),
                    "live_submission_enabled": False,
                }
            )
        )
        (self.bundle / "preprocessing").mkdir()
        (self.bundle / "preprocessing/manifest.json").write_text(
            json.dumps(
                {
                    "contract_code_sha256": contract_code_hashes(),
                }
            )
        )
        self.rehash()
        with (self.dataset / "train/features.csv").open() as stream:
            self.shipment = {**next(csv.DictReader(stream)), "order_type": "open_bid"}

    def rehash(self):
        manifest = {
            "schema_version": 1,
            "model_version": "synthetic-v1",
            "files_sha256": {
                str(p.relative_to(self.bundle)): sha256(p)
                for p in self.bundle.rglob("*")
                if p.is_file() and p.name != "manifest.json"
            },
        }
        # Include the nested preprocessing manifest, excluding only the root manifest.
        manifest["files_sha256"]["preprocessing/manifest.json"] = sha256(
            self.bundle / "preprocessing/manifest.json"
        )
        manifest["bundle_id"] = bundle_identity(manifest)
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))

    def test_default_review_and_explicit_baseline_are_distinct_without_mutation(self):
        fallback = BaselineFallback(self.bundle)
        before = dict(self.shipment)
        self.assertIsNone(fallback.recommend(self.shipment, "MODEL_ERROR", FallbackSettings()))
        result = fallback.recommend(self.shipment, "MODEL_ERROR", FallbackSettings(mode="baseline"))
        self.assertEqual(result["pricing_source"], "baseline")
        self.assertIsNone(result["model_version"])
        self.assertEqual(result["status"], "fallback_advisory")
        self.assertTrue(result["requires_review"])
        self.assertTrue(result["requires_guardrail_checks"])
        self.assertFalse(result["submission_enabled"])
        self.assertEqual(
            result["predicted_bid_dollars"],
            result["predicted_bid_rpm"] * float(self.shipment["distance_miles"]),
        )
        self.assertEqual(self.shipment, before)

    def test_unseen_input_requires_separate_opt_in_and_invalid_input_never_prices(self):
        fallback = BaselineFallback(self.bundle)
        unseen = {**self.shipment, "lane_id": "UNSEEN-LANE"}
        self.assertIsNone(
            fallback.recommend(unseen, "UNSUPPORTED_MODEL_INPUT", FallbackSettings(mode="baseline"))
        )
        result = fallback.recommend(
            unseen,
            "UNSUPPORTED_MODEL_INPUT",
            FallbackSettings(mode="baseline", unseen_category_action="baseline"),
        )
        self.assertEqual(result["baseline_reference"]["reference_source"], "global_median")
        for change in ({"distance_miles": 0}, {"weight_lbs": None}, {"order_type": "fixed_rate"}):
            self.assertIsNone(
                fallback.recommend(
                    {**self.shipment, **change}, "MODEL_ERROR", FallbackSettings(mode="baseline")
                )
            )

    def test_missing_or_changed_baseline_artifacts_are_rejected(self):
        path = self.bundle / "baselines/lane-median-v1.json"
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            BaselineFallback(self.bundle)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            BaselineFallback(self.bundle)

    def test_fallback_loads_without_model_weights_pickle_or_torch(self):
        script = """
import builtins, json, sys
from pathlib import Path
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'torch' or name.startswith('torch.'):
        raise ImportError('torch intentionally unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from loadpilot_model.inference.fallback import BaselineFallback, FallbackSettings
fallback = BaselineFallback(Path(sys.argv[1]))
result = fallback.recommend(
    json.loads(sys.argv[2]), 'MODEL_UNAVAILABLE', FallbackSettings(mode='baseline'))
assert result['pricing_source'] == 'baseline'
assert not result['submission_enabled']
"""
        subprocess.run(
            [sys.executable, "-c", script, str(self.bundle), json.dumps(self.shipment)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_invalid_environment_config_is_not_silently_defaulted(self):
        for env in (
            {"LOADPILOT_PRICING_FALLBACK_MODE": "automatic"},
            {"LOADPILOT_PRICING_UNSEEN_CATEGORY_ACTION": "guess"},
            {"LOADPILOT_PRICING_MODEL_BLOCKED": "maybe"},
        ):
            with patch.dict("os.environ", env, clear=True), self.assertRaises(ValueError):
                FallbackSettings.from_environment()
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(FallbackSettings.from_environment(), FallbackSettings())
