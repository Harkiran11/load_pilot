"""Select and load an auditable validation-backed baseline fallback."""

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from loadpilot_model.baselines.global_median import GlobalMedianBaseline, load_global_median
from loadpilot_model.baselines.lane_median import LaneMedianBaseline, load_lane_median
from loadpilot_model.evaluation.bid_comparison import rpm_to_bid_dollars
from loadpilot_model.features.schema import build_features
from loadpilot_model.paths import PROJECT_ROOT

TRIGGERS = {
    "MODEL_UNAVAILABLE",
    "MODEL_ERROR",
    "MODEL_TIMEOUT",
    "INVALID_MODEL_OUTPUT",
    "MODEL_UNRELIABLE",
    "UNSUPPORTED_MODEL_INPUT",
}
FILES = {"global_median": "global-median-v1.json", "lane_median": "lane-median-v1.json"}


def full_coverage(metric: dict, count: int) -> bool:
    return (
        metric["status"] == "scored"
        and metric["eligible_count"] == count
        and metric["prediction_count"] == count
        and metric["unavailable_count"] == 0
        and metric["coverage"] == 1
        and all(
            isinstance(metric[name], (int, float))
            and not isinstance(metric[name], bool)
            and math.isfinite(metric[name])
            and metric[name] >= 0
            for name in ("mae_rpm", "mae_dollars")
        )
    )


def select_fallback(metrics_path: Path, baselines: Path) -> dict:
    contents = metrics_path.read_bytes()
    report = json.loads(contents)
    count = report["row_count"]
    if report["partition"] != "validation" or not isinstance(count, int) or count < 1:
        raise ValueError("Fallback selection requires nonempty validation metrics")
    metrics = {row["baseline"]: row for row in report["overall"]}
    global_metrics, lane_metrics = metrics["global_median"], metrics["lane_median"]
    artifacts, hashes = {}, {}
    for name, filename in FILES.items():
        raw = (baselines / filename).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != report["baseline_files_sha256"][filename]:
            raise ValueError("Baseline artifact changed since validation evaluation")
        artifact = json.loads(raw)
        if (
            artifact["fit_partition"] != "train"
            or artifact["source_sha256"] != report["source_sha256"]
            or artifact["dataset_manifest_sha256"] != report["dataset_manifest_sha256"]
        ):
            raise ValueError("Baseline training/source lineage differs from validation metrics")
        artifacts[name], hashes[name] = artifact, sha
    load_global_median(baselines / FILES["global_median"])
    load_lane_median(baselines / FILES["lane_median"])
    if not full_coverage(global_metrics, count):
        raise ValueError("Global reference lacks complete usable validation evidence; use REVIEW")
    choose_lane = (
        full_coverage(lane_metrics, count)
        and lane_metrics["mae_rpm"] <= global_metrics["mae_rpm"]
        and lane_metrics["mae_dollars"] <= global_metrics["mae_dollars"]
        and (
            lane_metrics["mae_rpm"] < global_metrics["mae_rpm"]
            or lane_metrics["mae_dollars"] < global_metrics["mae_dollars"]
        )
    )
    selected = "lane_median" if choose_lane else "global_median"
    artifact = artifacts[selected]
    return {
        "schema_version": 1,
        "policy_version": "baseline-fallback-v1",
        "selected_baseline": selected,
        "baseline_id": artifact["baseline_id"],
        "baseline_artifact": FILES[selected],
        "baseline_sha256": hashes[selected],
        "selection_rule": "Select lane+global only with full validation coverage and no regression "
        "in either RPM or dollar MAE, with at least one strict improvement; otherwise global",
        "selection_partition": "validation",
        "fit_partition": "train",
        "source_sha256": report["source_sha256"],
        "dataset_manifest_sha256": report["dataset_manifest_sha256"],
        "metrics_sha256": hashlib.sha256(contents).hexdigest(),
        "validation_metrics": report["overall"],
        "min_lane_samples": artifact.get("min_lane_samples"),
        "global_fallback_rpm": artifact["global_fallback"]["median_bid_rpm"]
        if selected == "lane_median"
        else artifact["median_bid_rpm"],
        "improvement_vs_global_percent": {
            name: 100 * (global_metrics[name] - lane_metrics[name]) / global_metrics[name]
            if full_coverage(lane_metrics, count) and global_metrics[name]
            else None
            for name in ("mae_rpm", "mae_dollars")
        },
        "supported_triggers": sorted(TRIGGERS),
        "unreliable_policy": "Explicit upstream failed release/drift/support gate; "
        "no invented confidence score or request-time fitting",
        "default_fallback_mode": "review",
        "submission_enabled": False,
        "invalid_fallback_policy": "REVIEW with no candidate if disabled, unsupported, "
        "or artifact/input integrity checks fail",
        "limitations": [
            "Historical submitted-price accuracy only; not profit or acceptance",
            "Market reference unavailable; no independent pre-bid quote snapshots",
            "Sparse lane slices and unobserved seasons limit generalization claims",
            "No test evaluation, baseline refit, threshold tuning, or live submissions",
        ],
    }


@dataclass(frozen=True)
class SelectedFallback:
    baseline: GlobalMedianBaseline | LaneMedianBaseline
    policy: dict
    policy_sha256: str

    def recommend(
        self,
        shipment: dict,
        *,
        trigger: str,
        fallback_mode: str = "review",
        unseen_category_action: str = "review",
    ) -> dict:
        result = {
            "status": "review",
            "predicted_bid_rpm": None,
            "predicted_bid_dollars": None,
            "baseline_id": self.policy["baseline_id"],
            "policy_version": self.policy["policy_version"],
            "policy_sha256": self.policy_sha256,
            "baseline_sha256": self.policy["baseline_sha256"],
            "trigger": trigger,
            "submission_enabled": False,
            "requires_guardrail_checks": True,
        }

        def review(reason):
            return {**result, "reason": reason}

        if trigger not in TRIGGERS:
            return review("UNSUPPORTED_FALLBACK_TRIGGER")
        if fallback_mode not in {"review", "baseline"} or unseen_category_action not in {
            "review",
            "baseline",
        }:
            return review("INVALID_FALLBACK_POLICY")
        if fallback_mode != "baseline":
            return review("FALLBACK_DISABLED")
        if trigger == "UNSUPPORTED_MODEL_INPUT" and unseen_category_action != "baseline":
            return review("UNSUPPORTED_INPUT_REQUIRES_REVIEW")
        if shipment.get("order_type") != "open_bid":
            return review("UNSUPPORTED_ORDER_TYPE")
        try:
            features = build_features(shipment)
            if isinstance(self.baseline, LaneMedianBaseline):
                reference = self.baseline.predict(features["lane_id"])
            else:
                reference = {
                    "predicted_bid_rpm": self.baseline.predict_rpm(),
                    "source": "global_median",
                    "reason": "GLOBAL_TRAINING_REFERENCE",
                    "lane_training_row_count": None,
                }
            dollars = rpm_to_bid_dollars(reference["predicted_bid_rpm"], features["distance_miles"])
            rounded = dollars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (ValueError, ArithmeticError):
            return review("UNSUPPORTED_BASELINE_INPUT")
        if rounded <= 0:
            return review("INVALID_BASELINE_PRICE")
        return {
            **result,
            "status": "candidate",
            **reference,
            "reference_reason": reference["reason"],
            "reason": "BASELINE_FALLBACK",
            "predicted_bid_dollars": str(dollars),
            "predicted_bid_dollars_rounded": str(rounded),
        }


def load_selected_fallback(path: Path) -> SelectedFallback:
    contents = path.read_bytes()
    policy = json.loads(contents)
    if (
        policy["schema_version"] != 1
        or policy["selection_partition"] != "validation"
        or policy["fit_partition"] != "train"
        or policy["submission_enabled"] is not False
        or policy["default_fallback_mode"] != "review"
    ):
        raise ValueError("Unsupported baseline fallback policy")
    name = policy["selected_baseline"]
    if name not in FILES or policy["baseline_artifact"] != FILES[name]:
        raise ValueError("Unsupported selected baseline artifact")
    baseline_path = path.parent / policy["baseline_artifact"]
    raw = baseline_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != policy["baseline_sha256"]:
        raise ValueError("Selected fallback artifact checksum mismatch; use REVIEW")
    artifact = json.loads(raw)
    for field in ("baseline_id", "source_sha256", "dataset_manifest_sha256"):
        if artifact[field] != policy[field]:
            raise ValueError("Selected fallback provenance mismatch; use REVIEW")
    baseline = (
        load_lane_median(baseline_path)
        if name == "lane_median"
        else load_global_median(baseline_path)
    )
    return SelectedFallback(baseline, policy, hashlib.sha256(contents).hexdigest())


def recommend_fallback(
    path: Path,
    shipment: dict,
    *,
    trigger: str,
    fallback_mode: str = "review",
    unseen_category_action: str = "review",
) -> dict:
    """Route missing/corrupted fallback artifacts to review, never an invented bid."""
    try:
        fallback = load_selected_fallback(path)
        return fallback.recommend(
            shipment,
            trigger=trigger,
            fallback_mode=fallback_mode,
            unseen_category_action=unseen_category_action,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "status": "review",
            "reason": "FALLBACK_ARTIFACT_UNAVAILABLE_OR_INVALID",
            "trigger": trigger,
            "predicted_bid_rpm": None,
            "predicted_bid_dollars": None,
            "submission_enabled": False,
            "requires_guardrail_checks": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metrics",
        type=Path,
        default=PROJECT_ROOT / "artifacts/evaluation/validation-metrics-v1/metrics.json",
    )
    parser.add_argument("--baselines", type=Path, default=PROJECT_ROOT / "artifacts/baselines")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/baselines/fallback-policy-v1.json"
    )
    args = parser.parse_args()
    try:
        policy = select_fallback(args.metrics, args.baselines)
        if args.output.resolve().parent != args.baselines.resolve():
            raise ValueError("Save policy beside its referenced baseline artifacts")
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(policy, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Unable to select fallback: {error}\n")
    print(f"Selected {policy['baseline_id']}; saved metrics and fallback policy to {args.output}")
    print("Fallback requires explicit baseline mode; live submission remains disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
