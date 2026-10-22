"""Validation-backed selection, runtime failure routing, and policy boundaries."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from loadpilot_model.baselines.fallback import (
    TRIGGERS,
    load_selected_fallback,
    recommend_fallback,
    select_fallback,
)


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.metrics = self.root / "metrics.json"
        self.policy = self.root / "fallback-policy-v1.json"
        common = {
            "schema_version": 1,
            "fit_partition": "train",
            "units": "USD per loaded mile",
            "training_row_count": 6,
            "source_sha256": "a" * 64,
            "dataset_manifest_sha256": "b" * 64,
        }
        global_artifact = {**common, "baseline_id": "global_median_bid_rpm", "median_bid_rpm": 3.5}
        lane_artifact = {
            **common,
            "baseline_id": "lane_median_bid_rpm",
            "min_lane_samples": 2,
            "global_fallback": {"training_row_count": 6, "median_bid_rpm": 3.5},
            "lanes": {
                "A-B": {"training_row_count": 2, "median_bid_rpm": 2.5},
                "B-A": {"training_row_count": 3, "median_bid_rpm": 3},
                "C-D": {"training_row_count": 1, "median_bid_rpm": None},
            },
        }
        self.hashes = {}
        for filename, artifact in (
            ("global-median-v1.json", global_artifact),
            ("lane-median-v1.json", lane_artifact),
        ):
            contents = json.dumps(artifact).encode()
            (self.root / filename).write_bytes(contents)
            self.hashes[filename] = hashlib.sha256(contents).hexdigest()
        self.report = {
            "partition": "validation",
            "row_count": 10,
            "source_sha256": "a" * 64,
            "dataset_manifest_sha256": "b" * 64,
            "baseline_files_sha256": self.hashes,
            "overall": [
                {
                    "baseline": name,
                    "status": "scored",
                    "eligible_count": 10,
                    "prediction_count": 10,
                    "unavailable_count": 0,
                    "coverage": 1,
                    "mae_rpm": rpm,
                    "mae_dollars": dollars,
                }
                for name, rpm, dollars in (("global_median", 0.4, 500), ("lane_median", 0.3, 350))
            ],
        }
        self.shipment = {
            "order_type": "open_bid",
            "lane_id": "A-B",
            "distance_miles": "100",
            "weight_lbs": "10000",
            "origin_latitude": "33",
            "origin_longitude": "-84",
            "destination_latitude": "33",
            "destination_longitude": "-97",
            "origin_state": "GA",
            "destination_state": "TX",
            "bid_rate": "999",
        }
        self.save_selection()

    def save_selection(self):
        self.metrics.write_text(json.dumps(self.report))
        policy = select_fallback(self.metrics, self.root)
        self.policy.write_text(json.dumps(policy))
        return policy

    def test_lane_selected_on_both_errors_with_full_coverage(self):
        policy = json.loads(self.policy.read_text())
        self.assertEqual(policy["selected_baseline"], "lane_median")
        self.assertEqual(policy["validation_metrics"], self.report["overall"])
        self.assertFalse(policy["submission_enabled"])
        self.assertEqual(policy["default_fallback_mode"], "review")

    def test_tradeoff_or_incomplete_lane_coverage_retains_global(self):
        original = deepcopy(self.report)
        self.report["overall"][1]["mae_dollars"] = 600
        self.assertEqual(self.save_selection()["selected_baseline"], "global_median")
        self.report = original
        self.report["overall"][1].update(prediction_count=9, unavailable_count=1, coverage=0.9)
        self.assertEqual(self.save_selection()["selected_baseline"], "global_median")

    def test_missing_global_evidence_and_test_based_selection_rejected(self):
        self.report["overall"][0]["prediction_count"] = 9
        with self.assertRaisesRegex(ValueError, "Global reference"):
            self.save_selection()
        self.report["partition"] = "test"
        with self.assertRaisesRegex(ValueError, "validation"):
            self.save_selection()

    def test_each_failure_trigger_returns_candidate_when_explicitly_enabled(self):
        fallback = load_selected_fallback(self.policy)
        before = deepcopy(self.shipment)
        for trigger in TRIGGERS:
            result = fallback.recommend(
                self.shipment,
                trigger=trigger,
                fallback_mode="baseline",
                unseen_category_action="baseline",
            )
            self.assertEqual(result["status"], "candidate")
            self.assertEqual(result["predicted_bid_rpm"], 2.5)
            self.assertEqual(result["predicted_bid_dollars_rounded"], "250.00")
            self.assertFalse(result["submission_enabled"])
            self.assertTrue(result["requires_guardrail_checks"])
            self.assertEqual(result["reference_reason"], "SUFFICIENT_TRAINING_SAMPLES")
        self.assertEqual(self.shipment, before)

    def test_sparse_unseen_lanes_use_global_while_invalid_inputs_review(self):
        fallback = load_selected_fallback(self.policy)
        for lane in ("C-D", "X-Y"):
            result = fallback.recommend(
                {**self.shipment, "lane_id": lane},
                trigger="MODEL_UNAVAILABLE",
                fallback_mode="baseline",
            )
            self.assertEqual(result["source"], "global_median")
            self.assertEqual(result["predicted_bid_rpm"], 3.5)
        for changes in ({"distance_miles": "0"}, {"lane_id": ""}, {"order_type": "fixed_rate"}):
            result = fallback.recommend(
                {**self.shipment, **changes}, trigger="MODEL_ERROR", fallback_mode="baseline"
            )
            self.assertEqual(result["status"], "review")
            self.assertIsNone(result["predicted_bid_rpm"])

    def test_disabled_and_unsupported_input_policies_are_enforced(self):
        fallback = load_selected_fallback(self.policy)
        self.assertEqual(
            fallback.recommend(self.shipment, trigger="MODEL_TIMEOUT")["reason"],
            "FALLBACK_DISABLED",
        )
        result = fallback.recommend(
            self.shipment, trigger="UNSUPPORTED_MODEL_INPUT", fallback_mode="baseline"
        )
        self.assertEqual(result["reason"], "UNSUPPORTED_INPUT_REQUIRES_REVIEW")
        result = fallback.recommend(
            self.shipment, trigger="INVENTED_TRIGGER", fallback_mode="baseline"
        )
        self.assertEqual(result["status"], "review")

    def test_changed_or_missing_artifacts_fail_to_review(self):
        with (self.root / "lane-median-v1.json").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_selected_fallback(self.policy)
        result = recommend_fallback(
            self.policy, self.shipment, trigger="MODEL_ERROR", fallback_mode="baseline"
        )
        self.assertEqual(result["status"], "review")
        self.assertIsNone(result["predicted_bid_rpm"])
        self.assertEqual(
            recommend_fallback(self.root / "missing.json", self.shipment, trigger="MODEL_ERROR")[
                "status"
            ],
            "review",
        )


if __name__ == "__main__":
    unittest.main()
