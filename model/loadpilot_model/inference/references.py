"""Frozen baseline reference prices and descriptive model-support warnings."""

import json
import math
from pathlib import Path

from loadpilot_model.baselines.fallback import load_selected_fallback
from loadpilot_model.evaluation.validation_metrics import assign_band

SUPPORT_POLICY_VERSION = "pricing-support-v1"
NUMERIC_Z_THRESHOLD = 3.0


class PricingReferences:
    """Read verified package artifacts once; never fit or authorize a fallback bid."""

    def __init__(self, bundle: Path, metadata: dict):
        self.metadata = metadata
        self.selected = load_selected_fallback(bundle / "baselines/fallback-policy-v1.json")
        if (
            self.selected.policy["source_sha256"] != metadata["source_sha256"]
            or self.selected.policy["dataset_manifest_sha256"] != metadata["split_manifest_sha256"]
            or self.selected.policy["selected_baseline"] != "lane_median"
        ):
            raise ValueError("Reference baseline differs from packaged model lineage/policy")
        self.scaler = json.loads((bundle / "preprocessing/numeric-scaler.json").read_text())
        self.evaluation = json.loads((bundle / "evaluation/comparison.json").read_text())
        self.test_slices = self.evaluation["partitions"]["test"]["slices"]
        self.minimum_test_samples = self.evaluation["frozen_gates"]["minimum_slice_samples"]

    def describe(self, features: dict) -> dict:
        reference = self.selected.baseline.predict(features["lane_id"])
        rpm = float(reference["predicted_bid_rpm"])
        dollars = rpm * float(features["distance_miles"])
        if not math.isfinite(rpm) or rpm <= 0 or not math.isfinite(dollars) or dollars <= 0:
            raise RuntimeError("Invalid frozen baseline reference price")
        baseline = {
            "baseline_id": self.selected.policy["baseline_id"],
            "policy_version": self.selected.policy["policy_version"],
            "reference_source": reference["source"],
            "reason": reference["reason"],
            "predicted_bid_rpm": rpm,
            "predicted_bid_dollars": dollars,
            "lane_training_row_count": reference["lane_training_row_count"],
            "min_lane_samples": reference["min_lane_samples"],
        }
        warnings = []

        def warn(code, message, fields=None, details=None):
            warnings.append(
                {"code": code, "message": message, "fields": fields or [], "details": details or {}}
            )

        if self.metadata["release_status"] == "unverified":
            warn("MODEL_RELEASE_UNVERIFIED", "Model is advisory; release gates remain unverified.")
        lane_slice = next(
            (
                row
                for row in self.test_slices
                if row["dimension"] == "lane" and row["slice"] == features["lane_id"]
            ),
            None,
        )
        count = lane_slice["methods"]["pytorch"]["prediction_count"] if lane_slice else 0
        if count < self.minimum_test_samples:
            warn(
                "LANE_SUPPORT_UNVERIFIED",
                "Held-out lane evidence is insufficient for verification.",
                ["lane_id"],
                {"test_sample_count": count, "minimum_test_samples": self.minimum_test_samples},
            )
        if reference["source"] == "global_median":
            warn(
                "SPARSE_LANE_REFERENCE",
                "Reference uses global training median for this sparse lane.",
                ["lane_id"],
                {
                    "lane_training_row_count": reference["lane_training_row_count"],
                    "min_lane_samples": reference["min_lane_samples"],
                },
            )
        for name, mean, scale in zip(
            self.scaler["feature_order"], self.scaler["mean"], self.scaler["scale"]
        ):
            z_score = abs((float(features[name]) - mean) / scale)
            if not math.isfinite(z_score):
                raise RuntimeError("Nonfinite numeric support diagnostic")
            if z_score > NUMERIC_Z_THRESHOLD:
                warn(
                    "NUMERIC_SUPPORT_HEURISTIC",
                    "Value is far from the training mean; "
                    "this diagnostic is not a calibrated probability or range guarantee.",
                    [name],
                    {"absolute_standardized_value": z_score, "threshold": NUMERIC_Z_THRESHOLD},
                )
        for dimension, field in (
            ("distance_band", "distance_miles"),
            ("weight_band", "weight_lbs"),
        ):
            label = assign_band(features[field], self.evaluation["band_policy"]["cuts"][dimension])
            row = next(
                s for s in self.test_slices if s["dimension"] == dimension and s["slice"] == label
            )
            comparison = row["methods"]["lane_median"]
            rpm_gain = comparison["pytorch_improvement_mae_rpm_percent"]
            dollar_gain = comparison["pytorch_improvement_mae_dollars_percent"]
            if any(value is not None and value < 0 for value in (rpm_gain, dollar_gain)):
                warn(
                    "EVALUATED_SLICE_REGRESSION",
                    "Model errors exceeded the baseline in this "
                    "historical test slice; this is descriptive evidence, not a routing rule.",
                    [field],
                    {
                        "slice": label,
                        "test_sample_count": comparison["prediction_count"],
                        "rpm_improvement_percent": rpm_gain,
                        "dollar_improvement_percent": dollar_gain,
                    },
                )
        return {
            "baseline_reference": baseline,
            "warnings": warnings,
            "support_policy_version": SUPPORT_POLICY_VERSION,
            "uncertainty": {
                "status": "not_calibrated",
                "confidence_level": None,
                "rpm_interval": None,
                "dollar_interval": None,
            },
        }
