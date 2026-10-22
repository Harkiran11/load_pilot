"""Audit saved validation prices without inventing historical fleet or cost snapshots."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from loadpilot_model.api.autopilot import AutopilotPolicy, AutopilotRequest
from loadpilot_model.baselines.global_median import load_global_median
from loadpilot_model.baselines.lane_median import load_lane_median
from loadpilot_model.decision.engine import evaluate
from loadpilot_model.evaluation.bid_comparison import (
    load_validation_records,
    positive_decimal,
    read_table,
)
from loadpilot_model.features.schema import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from loadpilot_model.paths import PROJECT_ROOT


def audit_validation(dataset, source, bundle, predictions, policy=None):
    records, provenance = load_validation_records(dataset, source)
    manifest = json.loads((bundle / "manifest.json").read_text())
    payload = {k: v for k, v in manifest.items() if k != "bundle_id"}
    if manifest["schema_version"] != 1 or (
        hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        != manifest["bundle_id"]
    ):
        raise ValueError("Invalid model manifest")
    frozen = {}
    for name in (
        "metadata.json",
        "evaluation/comparison.json",
        "baselines/global-median-v1.json",
        "baselines/lane-median-v1.json",
    ):
        content = (bundle / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest["files_sha256"][name]:
            raise ValueError("Frozen replay artifact changed")
        frozen[name] = json.loads(content)
    metadata = frozen["metadata.json"]
    if (
        metadata["source_sha256"] != provenance["source_sha256"]
        or metadata["split_manifest_sha256"] != provenance["dataset_manifest_sha256"]
    ):
        raise ValueError("Replay/model training lineage mismatch")
    rows, contents = read_table(
        predictions,
        ("shipment_id", "shipped_at", "loaded_miles", "target_bid_rpm", "predicted_bid_rpm"),
    )
    if (
        hashlib.sha256(contents).hexdigest()
        != frozen["evaluation/comparison.json"]["validation_predictions_sha256"]
    ):
        raise ValueError("Validation predictions differ from frozen model selection")
    if len(rows) != len(records):
        raise ValueError("Prediction/validation population mismatch")
    global_baseline = load_global_median(bundle / "baselines/global-median-v1.json")
    lane_baseline = load_lane_median(bundle / "baselines/lane-median-v1.json")
    for name in ("baselines/global-median-v1.json", "baselines/lane-median-v1.json"):
        if (
            frozen[name]["source_sha256"] != metadata["source_sha256"]
            or frozen[name]["dataset_manifest_sha256"] != metadata["split_manifest_sha256"]
        ):
            raise ValueError("Baseline training lineage mismatch")
    selected_policy = AutopilotPolicy.model_validate(policy) if policy is not None else None
    output = []
    for record, prediction in zip(records, rows):
        if (
            record["shipment_id"] != prediction["shipment_id"]
            or record["shipped_at"] != prediction["shipped_at"]
            or positive_decimal(record["distance_miles"], "distance")
            != positive_decimal(prediction["loaded_miles"], "distance")
            or abs(float(record["historical_bid_rpm"]) - float(prediction["target_bid_rpm"]))
            > 1e-10
        ):
            raise ValueError("Saved predictions do not align with historical validation labels")
        shipment = {key: float(record[key]) for key in NUMERIC_FEATURES}
        shipment.update({key: record[key] for key in CATEGORICAL_FEATURES})
        request = AutopilotRequest.model_validate(
            {
                "shipment_id": record["shipment_id"],
                "unit_id": "UNKNOWN-HISTORICAL-UNIT",
                "shipment": shipment,
                "policy": selected_policy,
                "backend_context": None,
            }
        )
        lane = lane_baseline.predict(record["lane_id"])
        quotes = {
            "pytorch": float(positive_decimal(prediction["predicted_bid_rpm"], "prediction")),
            "global_median": global_baseline.predict_rpm(),
            "lane_median": lane["predicted_bid_rpm"],
            "market_rpm": None,
        }
        now = datetime.strptime(record["shipped_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        for method, rpm in quotes.items():
            reference = {
                "predicted_bid_dollars": lane["predicted_bid_rpm"] * shipment["distance_miles"],
                "predicted_bid_rpm": lane["predicted_bid_rpm"],
                "baseline_id": "lane_median_bid_rpm",
                "policy_version": "baseline-fallback-v1",
                "min_lane_samples": lane["min_lane_samples"],
            }
            candidate = (
                {
                    "pricing_source": "model" if method == "pytorch" else "baseline",
                    "predicted_bid_rpm": rpm,
                    "predicted_bid_dollars": rpm * shipment["distance_miles"],
                    "baseline_reference": reference,
                }
                if rpm is not None
                else None
            )
            decision = evaluate(
                request.model_dump(),
                candidate,
                "PREDICTION_UNAVAILABLE" if rpm is None else None,
                now,
            )
            bid = float(record["historical_bid_dollars"])
            output.append(
                {
                    "shipment_id": record["shipment_id"],
                    "shipped_at": record["shipped_at"],
                    "method": method,
                    "decision": decision["decision"],
                    "reason_codes": decision["reason_codes"],
                    "predicted_bid_rpm": rpm,
                    "predicted_bid_dollars": candidate["predicted_bid_dollars"]
                    if candidate
                    else None,
                    "absolute_error_rpm": abs(rpm - float(record["historical_bid_rpm"]))
                    if rpm is not None
                    else None,
                    "absolute_error_dollars": abs(candidate["predicted_bid_dollars"] - bid)
                    if candidate
                    else None,
                    "projected_profit_dollars": None,
                    "target_margin_compliant": None,
                }
            )
    comparisons = {}
    for method in quotes:
        selected = [row for row in output if row["method"] == method]
        priced = [row for row in selected if row["predicted_bid_rpm"] is not None]
        comparisons[method] = {
            "eligible_count": len(selected),
            "priced_count": len(priced),
            "mae_rpm": sum(row["absolute_error_rpm"] for row in priced) / len(priced)
            if priced
            else None,
            "mae_dollars": sum(row["absolute_error_dollars"] for row in priced) / len(priced)
            if priced
            else None,
            "decision_counts": dict(Counter(row["decision"] for row in selected)),
            "verified_deadhead_cost_count": 0,
            "target_margin_compliance_rate": None,
            "mean_projected_profit_dollars": None,
        }
    report = {
        "schema_version": 1,
        "kind": "retrospective_validation_audit",
        "partition": "validation",
        "shipment_count": len(records),
        "row_count": len(output),
        "ordering": "chronological shipped_at proxy, not historical bid decision time",
        "point_in_time_verified": False,
        "policy_supplied": selected_policy is not None,
        "model_refitted": False,
        "inference_repeated": False,
        "historical_test_rescored": False,
        "price_metrics_interpretation": (
            "Validation was used for model selection; not unbiased new test evidence"
        ),
        "historical_won_reused": False,
        "margin_estimate_used_as_realized_profit": False,
        "market_reference_status": (
            "unavailable: export RPM is offer-derived, not an approved pre-bid quote"
        ),
        "comparisons": comparisons,
        "provenance": provenance,
        "validation_predictions_sha256": hashlib.sha256(contents).hexdigest(),
        "model_bundle_id": manifest["bundle_id"],
        "live_ready": False,
        "submission_enabled": False,
        "limitations": [
            "No historical decision timestamps/timezone confirmation",
            "No selected-unit positions/deadhead/availability/operational snapshots",
            "Export estimated_cost has no proven historical bid-time timestamp",
            "No independent historical pre-bid market feed",
            "No observed complete actual costs/payout or causal counterfactual awards",
        ],
    }
    return report, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--source", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv"
    )
    parser.add_argument(
        "--bundle", type=Path, default=PROJECT_ROOT / "artifacts/models/freight-rpm-v1"
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=PROJECT_ROOT / "artifacts/training/freight-earlystop-v1/validation-predictions.csv",
    )
    parser.add_argument("--policy", type=Path)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/decision-validation-v1"
    )
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text()) if args.policy else None
    report, rows = audit_validation(
        args.dataset, args.source, args.bundle, args.predictions, policy
    )
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (args.output / "replay.json").write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n")
    print(
        f"Audited {report['shipment_count']} validation shipments; "
        "point-in-time verification unavailable."
    )
    print(f"Saved report and chronological records to {args.output}")


if __name__ == "__main__":
    main()
