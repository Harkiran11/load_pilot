"""Inference artifact parity, schema enforcement, and compatibility checks."""

import json
import pickle
import tempfile
import unittest
from pathlib import Path

import numpy as np

from loadpilot_model.data.splitting import build_partitions
from loadpilot_model.features.schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS, NUMERIC_FEATURES
from loadpilot_model.inference.bundle import load_inference_bundle, save_inference_bundle


class InferenceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bundle"
        rows = [
            {
                "shipment_id": f"S-{i}",
                "shipped_at": f"2026-01-{i + 1:02} 12:00:00",
                "distance_miles": str(100 + i),
                "weight_lbs": "10000",
                "origin_latitude": "33",
                "origin_longitude": "-84",
                "destination_latitude": "33",
                "destination_longitude": "-97",
                "lane_id": "ATL-DFW" if i < 7 else "FUTURE-LANE",
                "origin_state": "GA",
                "destination_state": "TX",
                "target_bid_rpm": "3.2",
            }
            for i in range(10)
        ]
        self.splits, self.processor, self.report = build_partitions(rows)
        self.report["source_sha256"] = "a" * 64
        save_inference_bundle(self.processor, self.report, self.path)

    def test_saved_bundle_reproduces_transforms_without_fit(self):
        bundle = load_inference_bundle(self.path)
        for name in ("train", "validation", "test"):
            np.testing.assert_array_equal(
                bundle.transform(self.splits[name]["features"]), self.splits[name]["X"]
            )
        self.assertEqual(bundle.schema["raw_feature_order"], list(FEATURE_COLUMNS))
        self.assertEqual(bundle.schema["fit_row_count"], 7)
        self.assertEqual(bundle.transform([]).shape, (0, bundle.schema["encoded_feature_count"]))

    def test_preserves_original_training_pickle_bytes_and_rejects_different_state(self):
        original = pickle.dumps(self.processor, protocol=4)
        output = self.path.parent / "original"
        save_inference_bundle(self.processor, self.report, output, preprocessor_bytes=original)
        self.assertEqual((output / "preprocessor.pkl").read_bytes(), original)
        load_inference_bundle(output)
        changed = pickle.loads(original)
        changed.named_transformers_["numeric"].mean_[0] += 1
        with self.assertRaisesRegex(ValueError, "differ"):
            save_inference_bundle(
                self.processor,
                self.report,
                self.path.parent / "bad",
                preprocessor_bytes=pickle.dumps(changed),
            )
        self.assertFalse((self.path.parent / "bad").exists())

    def test_mappings_refer_to_correct_encoded_columns_and_training_categories(self):
        bundle = load_inference_bundle(self.path)
        x = bundle.transform([self.splits["train"]["features"][0]])[0]
        for name in CATEGORICAL_FEATURES:
            for mapping in bundle.categorical_mappings[name]:
                index = mapping["encoded_column_index"]
                self.assertEqual(x[index], 1)
                self.assertEqual(
                    bundle.schema["encoded_feature_order"][index], mapping["encoded_column_name"]
                )
        self.assertNotIn(
            "FUTURE-LANE", [m["value"] for m in bundle.categorical_mappings["lane_id"]]
        )
        self.assertEqual(
            bundle.transform(self.splits["test"]["features"])[0, len(NUMERIC_FEATURES)], 0
        )

    def test_missing_required_values_reject_optional_blanks_do_not_affect_transform(self):
        bundle = load_inference_bundle(self.path)
        record = self.splits["train"]["features"][0]
        for missing in (None, "", "NULL", "NaN", "N/A", "  "):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                bundle.transform([{**record, "origin_state": missing}])
        np.testing.assert_array_equal(
            bundle.transform([record]),
            bundle.transform(
                [{**record, "fuel_surcharge_rpm": "", "booked_lead_hours": None, "bid_rate": "999"}]
            ),
        )

    def test_modified_artifact_is_rejected_before_deserialization(self):
        with (self.path / "preprocessor.pkl").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_inference_bundle(self.path)

    def test_incompatible_versions_and_feature_code_are_rejected(self):
        manifest_path = self.path / "manifest.json"
        original = json.loads(manifest_path.read_text())
        changed = {**original, "runtime_versions": {}}
        manifest_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "versions"):
            load_inference_bundle(self.path)
        changed = {**original, "contract_code_sha256": {}}
        manifest_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "code differs"):
            load_inference_bundle(self.path)

    def test_existing_bundle_and_nontraining_origin_are_rejected(self):
        with self.assertRaises(FileExistsError):
            save_inference_bundle(self.processor, self.report, self.path)
        with self.assertRaisesRegex(ValueError, "training-only"):
            save_inference_bundle(
                self.processor,
                {**self.report, "fit_partition": "test"},
                Path(self.temp.name) / "invalid",
            )


if __name__ == "__main__":
    unittest.main()
