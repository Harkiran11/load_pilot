"""Compare saved PyTorch predictions with frozen training-only baselines."""

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path

from loadpilot_model.baselines.global_median import load_global_median
from loadpilot_model.baselines.lane_median import load_lane_median
from loadpilot_model.evaluation.bid_comparison import load_partition_records, positive_decimal
from loadpilot_model.evaluation.validation_metrics import (
    SEASONS,
    assign_band,
    band_labels,
    retrospective_season,
    summarize,
)
from loadpilot_model.paths import PROJECT_ROOT

METHODS = ("pytorch", "global_median", "lane_median", "market_rpm")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def improvement(model_error: float, baseline_error: float) -> float | None:
    """Positive values mean less model error; zero reference error has no percentage."""
    return 100 * (baseline_error - model_error) / baseline_error if baseline_error else None


def within_relative_gate(model_error: float, baseline_error: float, minimum: float) -> bool:
    if baseline_error == 0:
        return model_error == 0
    return improvement(model_error, baseline_error) >= minimum


def compare_group(rows: list[dict], minimum: int) -> dict:
    summaries = {
        method: summarize([row for row in rows if row["method"] == method], minimum)
        for method in METHODS
    }
    model = summaries["pytorch"]
    for method, result in summaries.items():
        for metric in ("mae_rpm", "mae_dollars"):
            result[f"pytorch_improvement_{metric}_percent"] = (
                improvement(model[metric], result[metric])
                if model[metric] is not None and result[metric] is not None
                else None
            )
    return summaries


def build_comparison(
    dataset: Path, source: Path, run: Path, baselines: Path, validation_metrics: Path
) -> tuple[dict, list[dict]]:
    """Read existing predictions only; never import the model or execute inference."""
    plan_path = run / "final-test-plan.json"
    plan = json.loads(plan_path.read_text())
    test_report = json.loads((run / "test-results.json").read_text())
    tuning = json.loads((run / "tuning-results.json").read_text())
    policy_path = baselines / "fallback-policy-v1.json"
    policy = json.loads(policy_path.read_text())
    pinned = {
        plan_path: test_report["plan_sha256"],
        run / "test-predictions.csv": test_report["predictions_sha256"],
        run / "tuning-results.json": plan["tuning_results_sha256"],
        run / "selected-best.pt": plan["checkpoint_sha256"],
        policy_path: plan["baseline_policy_sha256"],
        validation_metrics: policy["metrics_sha256"],
        dataset / "split-report.json": plan["split_manifest_sha256"],
        dataset / "preprocessor.pkl": plan["preprocessor_sha256"],
        source: plan["source_sha256"],
    }
    for path, expected in pinned.items():
        if sha256(path) != expected:
            raise ValueError(f"Pinned artifact checksum mismatch: {path.name}")
    if (
        tuning["selected_weights_sha256"] != plan["checkpoint_sha256"]
        or test_report["checkpoint_sha256"] != plan["checkpoint_sha256"]
        or tuning["selected_candidate"] != plan["selected_candidate"]
    ):
        raise ValueError("Model identity differs from the final evaluation plan")
    validation = json.loads(validation_metrics.read_text())
    band_policy = validation["band_policy"]
    if (
        band_policy["fit_partition"] != "train"
        or sha256(dataset / "train/features.csv") != band_policy["training_feature_sha256"]
    ):
        raise ValueError("Slice boundaries must use the pinned training features")
    cuts = band_policy["cuts"]
    baseline_hashes = validation["baseline_files_sha256"]
    for filename, expected in baseline_hashes.items():
        if sha256(baselines / filename) != expected:
            raise ValueError("Baseline differs from its validation-time artifact")
        artifact = json.loads((baselines / filename).read_text())
        if artifact["source_sha256"] != plan["source_sha256"]:
            raise ValueError("Baseline source mismatch")
        if filename != "market-rpm-v1.json":
            if (
                artifact["fit_partition"] != "train"
                or artifact["dataset_manifest_sha256"] != plan["split_manifest_sha256"]
            ):
                raise ValueError("Baseline must come from the original training partition")
            for relative, digest in artifact["training_files_sha256"].items():
                if sha256(dataset / relative) != digest:
                    raise ValueError("Baseline training records have changed")
    if (
        sha256(baselines / policy["baseline_artifact"]) != policy["baseline_sha256"]
        or policy["selected_baseline"] != "lane_median"
    ):
        raise ValueError("Selected baseline differs from the frozen fallback policy")
    market = json.loads((baselines / "market-rpm-v1.json").read_text())
    if market["current_dataset_status"] != "unavailable":
        raise ValueError("This comparison requires the frozen unavailable-market contract")
    global_reference = load_global_median(baselines / "global-median-v1.json")
    lane_reference = load_lane_median(baselines / "lane-median-v1.json")
    selected = next(
        candidate
        for candidate in tuning["candidates"]
        if candidate["candidate_id"] == tuning["selected_candidate"]
    )
    gates = plan["gates"]
    minimum = gates["minimum_slice_samples"]
    all_rows, partitions, lane_usage = [], {}, {}
    for partition in ("validation", "test"):
        records, provenance = load_partition_records(dataset, source, partition)
        with (run / f"{partition}-predictions.csv").open(newline="") as stream:
            predictions = list(csv.DictReader(stream, strict=True))
        if len(predictions) != len(records) or [row["shipment_id"] for row in predictions] != [
            r["shipment_id"] for r in records
        ]:
            raise ValueError("Saved model predictions must cover the exact ordered partition")
        rows, usage = [], Counter()
        for record, saved in zip(records, predictions):
            miles = positive_decimal(record["distance_miles"], "loaded miles")
            target = Decimal(record["historical_bid_rpm"])
            if (
                saved["shipped_at"] != record["shipped_at"]
                or Decimal(saved["loaded_miles"]) != miles
                or not math.isclose(float(saved["target_bid_rpm"]), float(target), rel_tol=1e-12)
            ):
                raise ValueError("Saved prediction context differs from historical record")
            model_rpm = positive_decimal(saved["predicted_bid_rpm"], "model prediction")
            lane = lane_reference.predict(record["lane_id"])
            usage[lane["reason"]] += 1
            values = {
                "pytorch": model_rpm,
                "global_median": global_reference.predict_rpm(),
                "lane_median": lane["predicted_bid_rpm"],
                "market_rpm": None,
            }
            for method, value in values.items():
                rpm = positive_decimal(value, "reference RPM") if value is not None else None
                rows.append(
                    {
                        "partition": partition,
                        "shipment_id": record["shipment_id"],
                        "method": method,
                        "lane": record["lane_id"],
                        "distance_band": assign_band(
                            record["distance_miles"], cuts["distance_band"]
                        ),
                        "weight_band": assign_band(record["weight_lbs"], cuts["weight_band"]),
                        "season": retrospective_season(record["shipped_at"]),
                        "loaded_miles": str(miles),
                        "historical_bid_dollars": record["historical_bid_dollars"],
                        "predicted_bid_rpm": str(rpm) if rpm is not None else "",
                        "predicted_bid_dollars": str(rpm * miles) if rpm is not None else "",
                        "status": "available" if rpm is not None else "unavailable",
                        "signed_error_rpm": str(rpm - target) if rpm is not None else "",
                        "signed_error_dollars": str(
                            rpm * miles - Decimal(record["historical_bid_dollars"])
                        )
                        if rpm is not None
                        else "",
                        "reference_source": lane["source"] if method == "lane_median" else method,
                        "reason": lane["reason"]
                        if method == "lane_median"
                        else market["reason"]
                        if method == "market_rpm"
                        else "FROZEN_REFERENCE",
                    }
                )
        overall = compare_group(rows, minimum)
        expected_metrics = (
            selected["validation"] if partition == "validation" else test_report["metrics"]
        )
        for key in ("mae_rpm", "mae_dollars"):
            if not math.isclose(overall["pytorch"][key], expected_metrics[key], abs_tol=1e-10):
                raise ValueError("Saved predictions disagree with frozen evaluation metrics")
        labels = {
            "lane": sorted({record["lane_id"] for record in records}),
            "distance_band": band_labels(cuts["distance_band"]),
            "weight_band": band_labels(cuts["weight_band"]),
            "season": list(SEASONS),
        }
        slices = []
        for dimension, names in labels.items():
            for name in names:
                summaries = compare_group([row for row in rows if row[dimension] == name], minimum)
                model, baseline = summaries["pytorch"], summaries["lane_median"]
                verified = model["prediction_count"] >= minimum
                if dimension == "lane" and name not in lane_reference.lanes:
                    verified = False
                passed = (
                    all(
                        within_relative_gate(
                            model[key], baseline[key], -gates["maximum_slice_regression_percent"]
                        )
                        for key in ("mae_rpm", "mae_dollars")
                    )
                    if verified
                    else None
                )
                slices.append(
                    {
                        "dimension": dimension,
                        "slice": name,
                        "methods": summaries,
                        "regression_gate": "pass"
                        if passed
                        else "fail"
                        if verified
                        else "unverified",
                    }
                )
        partitions[partition] = {"provenance": provenance, "overall": overall, "slices": slices}
        lane_usage[partition] = dict(usage)
        all_rows.extend(rows)
    test = partitions["test"]
    model, baseline = test["overall"]["pytorch"], test["overall"]["lane_median"]
    overall_gates = {
        "coverage": model["coverage"] >= gates["minimum_coverage"],
        "absolute_rpm_mae": model["mae_rpm"] <= gates["maximum_mae_rpm"],
        "absolute_dollar_mae": model["mae_dollars"] <= gates["maximum_mae_dollars"],
        **{
            f"baseline_improvement_{key}": within_relative_gate(
                model[key], baseline[key], gates["minimum_improvement_vs_test_baseline_percent"]
            )
            for key in ("mae_rpm", "mae_dollars")
        },
    }
    failures = [row for row in test["slices"] if row["regression_gate"] == "fail"]
    unverified = [row for row in test["slices"] if row["regression_gate"] == "unverified"]
    report = {
        "partitions": partitions,
        "band_policy": band_policy,
        "frozen_gates": gates,
        "lane_reference_usage": lane_usage,
        "overall_gate_results": overall_gates,
        "failed_test_slices": [{k: row[k] for k in ("dimension", "slice")} for row in failures],
        "unverified_test_slice_count": len(unverified),
        "pricing_gate_status": "failed"
        if failures or not all(overall_gates.values())
        else "unverified"
        if unverified
        else "passed",
        "operational_policy": "Retain frozen baseline/review policy; no model promotion, "
        "routing changes, or tuning against the consumed test set",
        "interpretation": "Historical submitted-price accuracy; no profit/award claim. "
        "Slice differences are descriptive, without significance estimates.",
        "artifact_sha256": {str(path): sha256(path) for path in pinned},
        "baseline_files_sha256": baseline_hashes,
        "validation_predictions_sha256": sha256(run / "validation-predictions.csv"),
        "test_results_sha256": sha256(run / "test-results.json"),
    }
    return report, all_rows


def save_comparison(report: dict, rows: list[dict], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    tables = {"predictions": rows, "overall": [], "slices": []}
    for partition, result in report["partitions"].items():
        for method, metrics in result["overall"].items():
            tables["overall"].append({"partition": partition, "method": method, **metrics})
        for row in result["slices"]:
            for method, metrics in row["methods"].items():
                tables["slices"].append(
                    {
                        "partition": partition,
                        "dimension": row["dimension"],
                        "slice": row["slice"],
                        "regression_gate": row["regression_gate"],
                        "method": method,
                        **metrics,
                    }
                )
    for name, records in tables.items():
        with (output / f"{name}.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    from loadpilot_model.evaluation.plots import plot_model_comparison

    plot_model_comparison(report, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument(
        "--source", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv"
    )
    parser.add_argument(
        "--run-dir", type=Path, default=PROJECT_ROOT / "artifacts/training/freight-earlystop-v1"
    )
    parser.add_argument("--baselines", type=Path, default=PROJECT_ROOT / "artifacts/baselines")
    parser.add_argument(
        "--validation-metrics",
        type=Path,
        default=PROJECT_ROOT / "artifacts/evaluation/validation-metrics-v1/metrics.json",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/model-comparison-v1"
    )
    args = parser.parse_args()
    try:
        report, rows = build_comparison(
            args.dataset, args.source, args.run_dir, args.baselines, args.validation_metrics
        )
        save_comparison(report, rows, args.output_dir)
    except (OSError, ValueError, KeyError, csv.Error) as error:
        parser.exit(2, f"Unable to compare model and baselines: {error}\n")
    print(
        json.dumps(
            {
                "test_overall": report["partitions"]["test"]["overall"],
                "gate_status": report["pricing_gate_status"],
                "failed_test_slices": report["failed_test_slices"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
