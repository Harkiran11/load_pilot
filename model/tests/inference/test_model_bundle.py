"""Versioning and manifest guards for local model packaging."""

import json
import tempfile
import unittest
from pathlib import Path

from loadpilot_model.inference.model_bundle import (
    bundle_identity,
    check_version,
    export_model_bundle,
    load_model_bundle,
)


class ModelPackagingTests(unittest.TestCase):
    def test_version_cannot_escape_output_directory(self):
        for value in ("../v1", "", "/tmp/v1", "a/b", "a b", ".v1", "v" * 65, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_version(value)
        check_version("freight-rpm-v1.0_test")

    def test_existing_version_rejected_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "v1").mkdir()
            (root / "v1/keep.txt").write_text("original")
            with self.assertRaisesRegex(ValueError, "already exists"):
                export_model_bundle(root, root, root, root, root, "v1")
            self.assertEqual((root / "v1/keep.txt").read_text(), "original")

    def test_manifest_identity_pins_model_version_and_metadata_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"schema_version": 1, "model_version": "v1", "files_sha256": {}}
            manifest["bundle_id"] = bundle_identity(manifest)
            manifest["model_version"] = "v2"
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "identity"):
                load_model_bundle(root)
