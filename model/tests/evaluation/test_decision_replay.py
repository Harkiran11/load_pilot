"""Saved validation audit exposes missing historical operational evidence."""

import csv
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from loadpilot_model.evaluation.bid_comparison import load_validation_records
from loadpilot_model.evaluation.decision_replay import audit_validation
from tests.evaluation import test_bid_comparison


class HistoricalDecisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_bid_comparison.ValidationComparisonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.dataset, self.source = self.fixture.dataset, self.fixture.source
        self.bundle = self.fixture.root / "bundle"
        (self.bundle / "baselines").mkdir(parents=True)
        (self.bundle / "evaluation").mkdir()
        records, provenance = load_validation_records(self.dataset, self.source)
        self.predictions = self.fixture.root / "validation-predictions.csv"
        with self.predictions.open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "shipment_id",
                    "shipped_at",
                    "loaded_miles",
                    "target_bid_rpm",
                    "predicted_bid_rpm",
                ],
            )
            writer.writeheader()
            for row in records:
                writer.writerow(
                    {
                        "shipment_id": row["shipment_id"],
                        "shipped_at": row["shipped_at"],
                        "loaded_miles": row["distance_miles"],
                        "target_bid_rpm": row["historical_bid_rpm"],
                        "predicted_bid_rpm": 3.0,
                    }
                )
        for name in ("global-median-v1.json", "lane-median-v1.json"):
            (self.bundle / "baselines" / name).write_bytes(
                (self.fixture.baselines / name).read_bytes()
            )
        (self.bundle / "metadata.json").write_text(
            json.dumps(
                {
                    "source_sha256": provenance["source_sha256"],
                    "split_manifest_sha256": provenance["dataset_manifest_sha256"],
                }
            )
        )
        (self.bundle / "evaluation/comparison.json").write_text(
            json.dumps(
                {
                    "validation_predictions_sha256": hashlib.sha256(
                        self.predictions.read_bytes()
                    ).hexdigest()
                }
            )
        )
        manifest = {
            "schema_version": 1,
            "files_sha256": {
                str(path.relative_to(self.bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.bundle.rglob("*.json")
            },
        }
        manifest["bundle_id"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))

    def test_chronological_price_comparisons_never_invent_missing_costs_or_market(self):
        report, rows = audit_validation(self.dataset, self.source, self.bundle, self.predictions)
        self.assertEqual(report["shipment_count"], 3)
        self.assertEqual(len(rows), 12)
        self.assertEqual(
            [row["shipped_at"] for row in rows], sorted(row["shipped_at"] for row in rows)
        )
        self.assertTrue(all(row["decision"] == "REVIEW" for row in rows))
        self.assertTrue(all(row["projected_profit_dollars"] is None for row in rows))
        self.assertIsNone(report["comparisons"]["pytorch"]["target_margin_compliance_rate"])
        self.assertEqual(report["comparisons"]["market_rpm"]["priced_count"], 0)
        self.assertFalse(report["point_in_time_verified"])
        self.assertFalse(report["historical_won_reused"])
        self.assertFalse(report["margin_estimate_used_as_realized_profit"])

    def test_no_test_partition_or_prediction_inference_is_needed(self):
        original = Path.read_bytes

        def guarded(path):
            self.assertNotIn("test", path.parts)
            self.assertNotEqual(path.name, "test-predictions.csv")
            return original(path)

        with patch.object(Path, "read_bytes", guarded):
            report, _ = audit_validation(self.dataset, self.source, self.bundle, self.predictions)
        self.assertFalse(report["inference_repeated"])
        self.assertFalse(report["historical_test_rescored"])

    def test_changed_prediction_artifact_fails_provenance_check(self):
        self.predictions.write_text(self.predictions.read_text().replace(",3.0", ",9.0"))
        with self.assertRaisesRegex(ValueError, "predictions differ"):
            audit_validation(self.dataset, self.source, self.bundle, self.predictions)
